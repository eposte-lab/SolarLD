"""Aerial vision service — Claude Sonnet identifies which building is the company's.

Stage 5 of the Building Identification Cascade. When the textual signals
(Atoka civic / Places multi-query / OSM name match) didn't converge with
high confidence on a single building, this service:

  1. Receives N candidate building polygons (from OSM Overpass Stage 4),
     typically capannoni in the same industrial zone.
  2. Builds a Mapbox Static satellite URL centred on each building's
     centroid at high zoom (~80 m field-of-view).
  3. Sends all the URLs to Claude as image content blocks in a single
     message, asking the model to look for the company name written /
     painted / displayed on or near each building.
  4. Returns a ``BuildingCandidate`` for the chosen building with weight
     proportional to Claude's reported confidence — or ``None`` when
     Claude can't see a clear visual match.

Why Claude over Gemini Flash:
  * The Anthropic SDK is already a dependency (other services use it).
  * Sonnet handles multi-image reasoning well — for our use case
    (read text painted on aerial roofs) it outperforms Gemini Flash
    in informal benchmarks.
  * Cost is acceptable: ~5 images × 1290 input tokens + 200 output ≈
    $0.025 per call. Worth it: this stage runs only when the cheaper
    text signals failed, and the alternative is rendering panels on
    the wrong building (which kills the demo).

We deliberately do NOT crop GeoTIFF tiles client-side: Mapbox's static
imagery is high-resolution, ready-to-render, and the URL approach
keeps memory usage flat regardless of how many candidates we evaluate.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger
from . import mapbox_service

if TYPE_CHECKING:
    from .building_identification import BuildingCandidate

log = get_logger(__name__)

_client: AsyncAnthropic | None = None


# Cost recorded for a single multi-image identification call. Anthropic
# charges per token; a typical call with 5 satellite tiles + the prompt
# lands at ~7000 input + ~250 output tokens at Sonnet pricing
# ($3/M input, $15/M output) → ~$0.025 → 3 cents conservative.
VISION_IDENTIFICATION_COST_CENTS = 3

# Minimum confidence Claude must report for us to trust the pick. Below
# this we discard the result entirely — the user picker UI is the safer
# fallback than letting an uncertain vision call decide the demo.
MIN_VISION_CONFIDENCE = 0.4

# Cascade model strategy: try Haiku first (~$1/M input, ~3× cheaper than
# Sonnet at $3/M), accept if it produces a confident match, fall back to
# Sonnet only when Haiku abstains or reports low confidence. Net effect:
# stages where Haiku finds the building (typical for visible signage)
# cost ~$0.007 instead of $0.025 — a ~70% saving on this stage. When
# Haiku abstains we pay both calls (~$0.032 worst-case), still cheaper
# than Sonnet-always thanks to Haiku's cheaper tokens.
HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-5"

# Confidence threshold below which Haiku's result is considered
# unreliable and we promote to Sonnet. Set higher than
# MIN_VISION_CONFIDENCE so a Haiku "0.4" doesn't sneak through but a
# Haiku "0.7" does — the operator still gets the picker UI when both
# models abstain.
HAIKU_PROMOTE_THRESHOLD = 0.65

DEFAULT_MODEL = SONNET_MODEL  # kept for backwards-compat with legacy callers


SYSTEM_PROMPT = (
    "You are a remote-sensing analyst working with high-resolution Italian "
    "aerial imagery. You will be given several satellite tiles, each "
    "centered on a different industrial building or warehouse, and asked "
    "to identify which one belongs to a specific company. You should look "
    "for: company name or logo painted on the roof; company name on the "
    "facade visible from above; signage at the parking entrance; vehicles "
    "with company branding; loading bay markings; or any other visual cue "
    "that uniquely ties a building to the company name in question. "
    "Be CONSERVATIVE: when no clear visual match exists, set match_index "
    "to null. False positives (picking the wrong building) are far worse "
    "than abstaining."
)


def _get_client() -> AsyncAnthropic:
    """Lazily-constructed shared Anthropic client."""
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


def _build_user_prompt(
    *,
    legal_name: str,
    vat_number: str,
    n_candidates: int,
    ateco_description: str | None,
    city: str | None,
) -> str:
    sector = (
        f", settore: {ateco_description}" if ateco_description else ""
    )
    location = f" nella zona industriale di {city}" if city else ""
    return (
        f"Devi identificare quale degli edifici mostrati nelle prossime "
        f"{n_candidates} immagini satellitari appartiene all'azienda "
        f"\"{legal_name}\" (P.IVA {vat_number}{sector}){location}.\n\n"
        f"Le immagini sono numerate da 1 a {n_candidates}, in quest'ordine. "
        f"Cerca elementi visivi che leghino un singolo edificio a quel nome "
        f"d'azienda specifico: nome dell'azienda dipinto sul tetto o sulle "
        f"facciate, logo aziendale, insegne all'ingresso o nei piazzali, "
        f"automezzi marchiati con il nome dell'azienda, segnaletica nei "
        f"baie di carico.\n\n"
        f"Rispondi SOLO con questo JSON, senza prosa né code fence:\n"
        f"{{\n"
        f"  \"match_index\": <numero da 1 a {n_candidates} oppure null>,\n"
        f"  \"confidence\": <numero da 0 a 1>,\n"
        f"  \"reasoning\": \"<una o due frasi che citano gli elementi visivi osservati>\"\n"
        f"}}"
    )


def _parse_vision_response(text: str) -> dict[str, Any] | None:
    """Tolerant JSON parser; mirrors claude_vision_service._parse_vision_response."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Strip ```json ... ``` fences gracefully.
        parts = stripped.split("```", 2)
        if len(parts) >= 2:
            stripped = parts[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip().rstrip("`").strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


# Hard cap on the Anthropic call. Without this, the SDK falls back to
# httpx's 600 s default — long enough that Railway's 30 s idle-connection
# timeout closes the browser connection BEFORE Claude responds, and the
# user sees a generic "Failed to fetch" with no upstream signal as to why.
# 18 s is empirically enough for a 5-image multimodal Sonnet call (p95
# ≈ 6-10 s) with margin; we'd rather skip Vision than hang the cascade.
_VISION_API_TIMEOUT_S = 18.0


async def _run_vision_call(
    *,
    model: str,
    image_blocks: list[dict[str, Any]],
    prompt: str,
    n_candidates: int,
) -> dict[str, Any] | None:
    """Single Anthropic Vision call → parsed dict or None on failure.

    Extracted so the Haiku→Sonnet cascade can reuse it without
    duplicating the wait_for + parse + error handling.
    """
    import asyncio

    try:
        client = _get_client()
        msg = await asyncio.wait_for(
            client.messages.create(
                model=model,
                max_tokens=400,
                temperature=0.0,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            *image_blocks,
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            ),
            timeout=_VISION_API_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning(
            "vision.api_timeout",
            timeout_s=_VISION_API_TIMEOUT_S,
            model=model,
            n_candidates=n_candidates,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "vision.api_error",
            model=model,
            err=type(exc).__name__,
            err_msg=str(exc)[:200],
        )
        return None

    text = ""
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            text = block.text  # type: ignore[attr-defined]
            break
    parsed = _parse_vision_response(text)
    if parsed is None:
        log.warning("vision.parse_failed", model=model, raw=text[:300])
    return parsed


@retry(
    stop=stop_after_attempt(1),  # No retry — too slow under Railway's idle window
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def identify_company_building_in_zone(
    *,
    legal_name: str,
    vat_number: str,
    candidate_buildings: "list[BuildingCandidate]",
    zone_anchor: tuple[float, float],
    ateco_description: str | None = None,
    city: str | None = None,
    model: str = DEFAULT_MODEL,  # legacy kwarg, ignored by the cascade below
) -> "BuildingCandidate | None":
    """Identify the company's building via a Haiku-first → Sonnet cascade.

    Strategy:
      1. Try Claude Haiku 4.5 (~$1/M input). If it returns a confident
         match (confidence ≥ HAIKU_PROMOTE_THRESHOLD), accept and stop.
      2. Otherwise (Haiku abstained, low confidence, or hard failure),
         fall back to Claude Sonnet 4.5 (~$3/M input) which is more
         capable on multi-image attention.

    Net cost: ~70% reduction on runs where Haiku finds the building
    visibly (typical for company names painted on the roof).
    Worst-case cost when both fire: ~$0.032 vs Sonnet-always ~$0.025
    — slightly higher in the failure path but rare.

    Returns:
        A new ``BuildingCandidate`` with ``source="vision"`` and weight
        proportional to the chosen model's reported confidence, or
        ``None`` when:
          * fewer than 2 candidates were given (vision adds no signal),
          * both models abstain or report ``confidence < MIN_VISION_CONFIDENCE``,
          * both API calls fail / time out.
    """
    # Local import to avoid the cycle building_identification → here.
    from .building_identification import BuildingCandidate

    if len(candidate_buildings) < 2:
        return None

    # Build a Mapbox Static URL per candidate. Zoom 19 yields ~0.3m/px
    # at 640×640 — a window of ~190m × 190m, which captures the
    # building of interest plus some surroundings (signage, parking
    # logos) without losing the painted-name level of detail.
    image_blocks: list[dict[str, Any]] = []
    candidate_urls: list[str] = []
    for cand in candidate_buildings:
        try:
            url = mapbox_service.build_static_satellite_url(
                cand.lat,
                cand.lng,
                zoom=19,
                width=640,
                height=640,
            )
        except mapbox_service.MapboxError as exc:
            log.warning("vision.mapbox_url_build_failed", err=str(exc))
            return None
        candidate_urls.append(url)
        image_blocks.append(
            {
                "type": "image",
                "source": {"type": "url", "url": url},
            }
        )

    prompt = _build_user_prompt(
        legal_name=legal_name,
        vat_number=vat_number,
        n_candidates=len(candidate_buildings),
        ateco_description=ateco_description,
        city=city,
    )

    log.info(
        "vision.cascade_starting",
        legal_name=legal_name,
        vat_number=vat_number,
        n_candidates=len(candidate_buildings),
    )

    # ── Tier 1: Haiku ────────────────────────────────────────────────
    parsed: dict[str, Any] | None = None
    chosen_model: str = HAIKU_MODEL
    haiku_parsed = await _run_vision_call(
        model=HAIKU_MODEL,
        image_blocks=image_blocks,
        prompt=prompt,
        n_candidates=len(candidate_buildings),
    )

    haiku_match = haiku_parsed.get("match_index") if haiku_parsed else None
    haiku_conf = (
        float(haiku_parsed.get("confidence", 0.0) or 0.0)
        if haiku_parsed
        else 0.0
    )

    if (
        haiku_parsed is not None
        and haiku_match is not None
        and haiku_conf >= HAIKU_PROMOTE_THRESHOLD
    ):
        # Haiku is confident → accept its result, skip Sonnet entirely.
        parsed = haiku_parsed
        chosen_model = HAIKU_MODEL
        log.info(
            "vision.haiku_accepted",
            legal_name=legal_name,
            confidence=haiku_conf,
            note=f"≥ {HAIKU_PROMOTE_THRESHOLD} → no Sonnet fallback needed",
        )
    else:
        # ── Tier 2: Sonnet fallback ─────────────────────────────────
        log.info(
            "vision.haiku_promoted_to_sonnet",
            legal_name=legal_name,
            haiku_match=haiku_match,
            haiku_confidence=haiku_conf,
            reason=(
                "haiku_failed" if haiku_parsed is None
                else "haiku_no_match" if haiku_match is None
                else "haiku_low_confidence"
            ),
        )
        sonnet_parsed = await _run_vision_call(
            model=SONNET_MODEL,
            image_blocks=image_blocks,
            prompt=prompt,
            n_candidates=len(candidate_buildings),
        )
        # Use Sonnet when it produced a result, even if Haiku also did
        # — Sonnet's multi-image attention is better than Haiku's so we
        # trust it over the cheaper one when both fired.
        if sonnet_parsed is not None:
            parsed = sonnet_parsed
            chosen_model = SONNET_MODEL
        elif haiku_parsed is not None:
            # Sonnet failed entirely (timeout / network) but Haiku gave
            # us something — use it as a last-resort signal even if
            # below the promotion threshold.
            parsed = haiku_parsed
            chosen_model = HAIKU_MODEL
            log.info(
                "vision.haiku_kept_sonnet_failed",
                legal_name=legal_name,
                note="Sonnet API call failed; falling back to low-conf Haiku result",
            )

    if parsed is None:
        # Both models failed.
        return None

    match_index = parsed.get("match_index")
    confidence = float(parsed.get("confidence", 0.0) or 0.0)
    reasoning = str(parsed.get("reasoning", ""))[:400]

    if match_index is None:
        log.info(
            "vision.no_match",
            legal_name=legal_name,
            confidence=confidence,
            reasoning=reasoning,
        )
        return None

    try:
        idx = int(match_index)
    except (TypeError, ValueError):
        log.warning("vision.invalid_match_index", value=str(match_index))
        return None
    if idx < 1 or idx > len(candidate_buildings):
        log.warning(
            "vision.match_index_out_of_range",
            idx=idx,
            n_candidates=len(candidate_buildings),
        )
        return None

    if confidence < MIN_VISION_CONFIDENCE:
        log.info(
            "vision.below_threshold",
            confidence=confidence,
            min=MIN_VISION_CONFIDENCE,
            reasoning=reasoning,
        )
        return None

    chosen = candidate_buildings[idx - 1]
    # Weight: 0.6 base for any vision pick + scale 0..0.4 for confidence
    # → range 0.6..1.0. Caps at 1.0 because vision alone is a single
    # signal — to reach our "high" confidence bucket we still want
    # corroboration from a textual signal.
    weight = 0.6 + 0.4 * min(1.0, max(0.0, confidence))

    log.info(
        "vision.match_found",
        legal_name=legal_name,
        idx=idx,
        confidence=confidence,
        weight=round(weight, 3),
        chosen_model=chosen_model,
        reasoning=reasoning,
    )

    return BuildingCandidate(
        lat=chosen.lat,
        lng=chosen.lng,
        weight=round(weight, 3),
        source="vision",
        polygon_geojson=chosen.polygon_geojson,
        metadata={
            "vision_confidence": confidence,
            "vision_reasoning": reasoning,
            "vision_model": chosen_model,
            "matched_osm_id": chosen.metadata.get("osm_id"),
            # Persist the source candidate's lat/lng so the dashboard
            # can show "vision picked OSM building #123" without a
            # cross-reference round-trip.
            "matched_lat": chosen.lat,
            "matched_lng": chosen.lng,
        },
    )
