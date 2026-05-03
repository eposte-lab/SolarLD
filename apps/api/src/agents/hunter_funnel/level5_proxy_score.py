"""L5 — Claude Haiku proxy scoring (FLUSSO 1 v3, no-Atoka).

Scores L4-accepted SolarQualified candidates 0-100 using only public
data (Places + scraped + Solar). The scorer is intentionally cheap
(~€0.001/candidate batched 10x) so the funnel can pre-filter aggressively
before investing the rendering cost (€0.55/lead) at L6.

Output: ScoredV3Candidate with overall_score + recommended_for_rendering.
Only candidates with `recommended_for_rendering=True` (overall_score >=
threshold) are promoted to L6 by the orchestrator.

Sector-aware: when the tenant has `target_wizard_groups`, the prompt
context already reflects the predicted_sector and the LLM is asked to
flag `wrong_sector` candidates explicitly.
"""

from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from ...services.claude_service import get_client
from ...services.sector_target_service import (
    _warm_cache,
)
from .types_v3 import (
    FunnelV3Context,
    ScoredV3Candidate,
    SolarQualified,
)

log = get_logger(__name__)


_BATCH_SIZE = 10
_BATCH_CONCURRENCY = 4
_COST_PER_CANDIDATE_CENTS = 1

_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "proxy_score_v3.md"
)


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Public agent
# ---------------------------------------------------------------------------


async def run_level5_proxy_score(
    ctx: FunnelV3Context,
    candidates: list[SolarQualified],
) -> list[ScoredV3Candidate]:
    """Batch-score candidates, persist score breakdown, return all."""
    if not candidates:
        return []

    accepted = [c for c in candidates if c.solar_verdict == "accepted"]
    if not accepted:
        log.info("level5_proxy.no_accepted", tenant_id=ctx.tenant_id)
        return []

    sb = get_service_client()
    await _warm_cache(sb)

    batches = [
        accepted[i : i + _BATCH_SIZE]
        for i in range(0, len(accepted), _BATCH_SIZE)
    ]

    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def run_batch(batch: list[SolarQualified]) -> list[ScoredV3Candidate]:
        async with sem:
            try:
                return await _score_batch(batch, ctx=ctx)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "level5_proxy.batch_error",
                    err=type(exc).__name__,
                    msg=str(exc)[:200],
                )
                return [_fallback_score(c) for c in batch]

    nested = await asyncio.gather(*[run_batch(b) for b in batches])
    scored = [s for sub in nested for s in sub]

    # Cost accounting
    ctx.costs.add_claude(
        scored=len(scored), cost_cents=len(scored) * _COST_PER_CANDIDATE_CENTS
    )

    # Persist score breakdown
    _bulk_persist_v3_scores(scored)

    # Sort by overall_score DESC for downstream consumers (L6 takes top N)
    scored.sort(key=lambda s: s.overall_score, reverse=True)

    log.info(
        "level5_proxy.done",
        tenant_id=ctx.tenant_id,
        scored=len(scored),
        recommended=sum(1 for s in scored if s.recommended_for_rendering),
        avg_score=round(
            sum(s.overall_score for s in scored) / max(1, len(scored)), 1
        ),
    )
    return scored


# ---------------------------------------------------------------------------
# Batch scoring
# ---------------------------------------------------------------------------


async def _score_batch(
    batch: list[SolarQualified], *, ctx: FunnelV3Context
) -> list[ScoredV3Candidate]:
    prompt = _build_batch_prompt(batch=batch, ctx=ctx)
    client = get_client()

    resp = await client.messages.create(
        model="claude-haiku-4-5-20250929",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content if hasattr(b, "text"))
    return _parse_batch_response(text, batch=batch, ctx=ctx)


def _build_batch_prompt(
    *, batch: list[SolarQualified], ctx: FunnelV3Context
) -> str:
    template = _load_prompt()

    candidates_payload: list[dict[str, Any]] = []
    for c in batch:
        rec = c.record
        candidates_payload.append(
            {
                "candidate_id": str(rec.candidate_id),
                "places": {
                    "display_name": rec.display_name,
                    "formatted_address": rec.formatted_address,
                    "types": rec.types,
                    "user_ratings_total": rec.user_ratings_total,
                    "rating": rec.rating,
                    "website": rec.website,
                    "phone": rec.phone,
                    "business_status": rec.business_status,
                },
                "scraped": {
                    "website": {
                        "emails_count": len(c.scraped.website_emails),
                        "pec_present": bool(c.scraped.website_pec),
                        "decision_maker_present": bool(
                            c.scraped.website_decision_maker
                        ),
                    },
                    "pagine_bianche_found": bool(c.scraped.pagine_bianche_phone),
                    "opencorporates": {
                        "vat": c.scraped.opencorporates_vat,
                        "founding_date": c.scraped.opencorporates_founding_date,
                        "legal_form": c.scraped.opencorporates_legal_form,
                    },
                },
                "building_quality_score": c.building_quality_score,
                "solar": {
                    "verdict": c.solar_verdict,
                    "area_m2": c.solar_area_m2,
                    "kw_installable": c.solar_kw_installable,
                    "panels_count": c.solar_panels_count,
                    "sunshine_hours": c.solar_sunshine_hours,
                },
                "predicted_sector": rec.predicted_sector,
                "active_sectors": list(getattr(ctx.config, "target_wizard_groups", ()) or []),
            }
        )

    return (
        f"{template}\n\n"
        f"# Candidates to score\n\n"
        f"```json\n{json.dumps(candidates_payload, ensure_ascii=False, indent=2)}\n```\n"
    )


def _parse_batch_response(
    text: str, *, batch: list[SolarQualified], ctx: FunnelV3Context
) -> list[ScoredV3Candidate]:
    """Parse the JSON array, fall back to per-candidate fallback on errors."""
    text = text.strip()
    # Strip code fences if Haiku wrapped the JSON.
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
        text = text.rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        log.warning("level5_proxy.parse_error", err=str(exc)[:200])
        return [_fallback_score(c) for c in batch]

    if not isinstance(parsed, list):
        log.warning("level5_proxy.unexpected_shape", typ=type(parsed).__name__)
        return [_fallback_score(c) for c in batch]

    by_id = {item.get("candidate_id"): item for item in parsed if isinstance(item, dict)}

    out: list[ScoredV3Candidate] = []
    for c in batch:
        item = by_id.get(str(c.record.candidate_id))
        if item is None:
            out.append(_fallback_score(c))
            continue
        out.append(_scored_from_payload(c, item))
    return out


def _scored_from_payload(
    c: SolarQualified, item: dict[str, Any]
) -> ScoredV3Candidate:
    icp = _clamp(item.get("icp_fit_score"))
    solar = _clamp(item.get("solar_potential_score"))
    contact = _clamp(item.get("contact_completeness_score"))
    overall = _clamp(item.get("overall_score"))
    flags = _str_list(item.get("flags"))
    reasons = _str_list(item.get("reasons"))

    # Threshold-based recommended_for_rendering, robust to the LLM
    # forgetting / lying about it.
    llm_recommend = bool(item.get("recommended_for_rendering"))
    recommended = llm_recommend and overall >= 60

    return ScoredV3Candidate(
        record=c.record,
        scraped=c.scraped,
        contact=c.contact,
        building_quality_score=c.building_quality_score,
        roof_id=c.roof_id,
        solar_verdict=c.solar_verdict,
        solar_area_m2=c.solar_area_m2,
        solar_kw_installable=c.solar_kw_installable,
        solar_panels_count=c.solar_panels_count,
        solar_sunshine_hours=c.solar_sunshine_hours,
        icp_fit_score=icp,
        solar_potential_score=solar,
        contact_completeness_score=contact,
        overall_score=overall,
        predicted_size_category=_str_or_none(item.get("predicted_size_category")),
        reasons=reasons,
        flags=flags,
        recommended_for_rendering=recommended,
        predicted_ateco_codes=_str_list(item.get("predicted_ateco_codes")),
    )


def _fallback_score(c: SolarQualified) -> ScoredV3Candidate:
    """Conservative fallback when Haiku batch fails entirely.

    Score derived from the building quality + solar kWp ratio. Always
    `recommended_for_rendering=False` so we don't burn rendering budget
    on un-validated candidates.
    """
    bqs_pct = (c.building_quality_score or 0) * 20  # 0-5 → 0-100
    solar_pct = min(100, int((c.solar_kw_installable or 0) / 3))
    contact_pct = (
        100 if (c.contact.best_email and c.contact.pec)
        else 70 if c.contact.best_email
        else 30
    )
    icp_pct = 50  # neutral
    overall = round(icp_pct * 0.30 + bqs_pct * 0.30 + solar_pct * 0.25 + contact_pct * 0.15)

    return ScoredV3Candidate(
        record=c.record,
        scraped=c.scraped,
        contact=c.contact,
        building_quality_score=c.building_quality_score,
        roof_id=c.roof_id,
        solar_verdict=c.solar_verdict,
        solar_area_m2=c.solar_area_m2,
        solar_kw_installable=c.solar_kw_installable,
        solar_panels_count=c.solar_panels_count,
        solar_sunshine_hours=c.solar_sunshine_hours,
        icp_fit_score=icp_pct,
        solar_potential_score=solar_pct,
        contact_completeness_score=contact_pct,
        overall_score=overall,
        predicted_size_category=None,
        reasons=["fallback_score"],
        flags=["llm_unavailable"],
        recommended_for_rendering=False,
        predicted_ateco_codes=[],
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _bulk_persist_v3_scores(scored: list[ScoredV3Candidate]) -> None:
    if not scored:
        return
    sb = get_service_client()
    rows = []
    for s in scored:
        rows.append(
            {
                "id": str(s.record.candidate_id),
                "stage": 5,
                "score": s.overall_score,
                "score_reasons": s.reasons,
                "score_flags": s.flags,
                "predicted_ateco_codes": s.predicted_ateco_codes,
                "proxy_score_data": {
                    "icp_fit_score": s.icp_fit_score,
                    "solar_potential_score": s.solar_potential_score,
                    "contact_completeness_score": s.contact_completeness_score,
                    "overall_score": s.overall_score,
                    "predicted_size_category": s.predicted_size_category,
                    "recommended_for_rendering": s.recommended_for_rendering,
                },
            }
        )
    try:
        sb.table("scan_candidates").upsert(rows, on_conflict="id").execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("level5_proxy.persist_failed", err=type(exc).__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        if isinstance(v, str):
            out.append(v)
    return out


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
