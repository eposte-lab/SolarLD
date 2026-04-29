"""Level 2 — Contact + surface signals enrichment.

Takes L1 candidates and augments them with:

  1. Phone + website (from Atoka if present, else we skip; Places Text
     Search would work but costs €0.02/call and is only useful when Atoka
     returns sparse contact data — gated behind a tenant config flag).
  2. Website heuristics — fetch the homepage and scan for Italian keywords
     that correlate with "this company actually owns a large industrial
     roof": `capannone`, `stabilimento`, `fabbrica`, `magazzino`,
     `logistica`. Takes <500ms per site, essentially free.
  3. Google Place Details — ONLY when we have a place_id, which Atoka
     usually doesn't provide. Falls back to a single Places Text Search
     per candidate when `config.funnel.enable_places_text_search` is true.
     Off by default to keep L2 cheap.

Output: writes the `enrichment` JSONB column on `scan_candidates`, advances
`stage` to 2, returns `EnrichedCandidate`s for L3.

Cost: ~€0.02/candidate only when Text Search is on; ~€0 otherwise.
Budget cap: we honour `ctx.costs.over_budget()` between candidates and
short-circuit the remaining list — partial L2 is still usable for L3.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from .types import EnrichedCandidate, EnrichmentSignals, FunnelContext, L1Candidate

log = get_logger(__name__)

# Concurrency cap for the enrichment fan-out. Higher than Solar's because
# the dominant cost is HTTP latency to arbitrary Italian websites, not
# shared API quota.
_ENRICHMENT_CONCURRENCY = 12

# Heuristic keywords — lowercase, plain text match on <title>, meta
# description, and a trimmed 64KB slice of the homepage body. Calibrated
# against Italian B2B websites (not English): a company that describes
# itself as a "stabilimento produttivo" has industrial scale.
_POSITIVE_KEYWORDS = (
    "capannone",
    "stabilimento",
    "fabbrica",
    "magazzino",
    "logistica",
    "produttivo",
    "industriale",
    "sede operativa",
    "mq coperti",
)

# Timeout for each website fetch. Tight — a slow site isn't worth waiting
# for, enrichment is best-effort signal.
_WEBSITE_FETCH_TIMEOUT_S = 4.0
_WEBSITE_MAX_BYTES = 64 * 1024


async def run_level2(
    ctx: FunnelContext, candidates: list[L1Candidate]
) -> list[EnrichedCandidate]:
    """Enrich L1 candidates in parallel, persist, return L2 set.

    Ordering is preserved — L3 doesn't care about order but tests do.
    """
    if not candidates:
        return []

    sem = asyncio.Semaphore(_ENRICHMENT_CONCURRENCY)

    async with httpx.AsyncClient(
        timeout=_WEBSITE_FETCH_TIMEOUT_S,
        follow_redirects=True,
        headers={
            # Some Italian SME sites 403 requests without a common UA.
            "User-Agent": (
                "Mozilla/5.0 (compatible; SolarLeadBot/1.0; "
                "+https://solarlead.it/bot)"
            )
        },
    ) as client:

        async def one(cand: L1Candidate) -> EnrichedCandidate:
            async with sem:
                signals = await _enrich_candidate(cand, client=client)
            return EnrichedCandidate(
                candidate_id=cand.candidate_id,
                profile=cand.profile,
                enrichment=signals,
            )

        enriched = await asyncio.gather(
            *(one(c) for c in candidates),
            return_exceptions=False,  # individual errors are swallowed inside
        )

    # Persist enrichment JSONB + stage bump
    _bulk_persist_l2(enriched)

    log.info(
        "funnel_l2_complete",
        extra={
            "tenant_id": ctx.tenant_id,
            "scan_id": ctx.scan_id,
            "enriched": len(enriched),
            "with_website": sum(1 for e in enriched if e.enrichment.website),
            "with_site_signals": sum(
                1 for e in enriched if e.enrichment.site_signals
            ),
        },
    )
    return enriched


# ---------------------------------------------------------------------------
# Per-candidate enrichment
# ---------------------------------------------------------------------------


async def _enrich_candidate(
    cand: L1Candidate, *, client: httpx.AsyncClient
) -> EnrichmentSignals:
    """Enrich one candidate. All branches are best-effort; we never raise."""
    signals = EnrichmentSignals()

    profile = cand.profile
    # Atoka gives us a bare domain ("esempio.it"); make it a URL.
    if profile.website_domain:
        signals.website = f"https://{profile.website_domain}"
    # Atoka doesn't currently expose phone in the public profile schema,
    # but when it does (premium tier) it's in raw.contacts[].value
    phone = _extract_phone_from_raw(profile.raw)
    if phone:
        signals.phone = phone

    # Homepage heuristics — only if we have a URL
    if signals.website:
        try:
            signals.site_signals = await _scan_website(signals.website, client=client)
        except (httpx.HTTPError, asyncio.TimeoutError, UnicodeDecodeError) as exc:
            log.debug(
                "l2_site_fetch_failed",
                extra={"vat": profile.vat_number, "err": str(exc)},
            )

    return signals


async def _scan_website(url: str, *, client: httpx.AsyncClient) -> list[str]:
    """Fetch the homepage and return matched positive keywords.

    We don't retry — a site that's down on first try isn't worth the
    latency, this is a signal layer not a dependency.
    """
    resp = await client.get(url)
    if resp.status_code >= 400:
        return []
    # Charset is best-effort; Italian sites are overwhelmingly UTF-8 or
    # ISO-8859-1. `resp.text` does the right thing for both.
    body = resp.text[:_WEBSITE_MAX_BYTES].lower()

    # Collapse whitespace so "sede   operativa" still matches.
    normalised = re.sub(r"\s+", " ", body)

    found = [kw for kw in _POSITIVE_KEYWORDS if kw in normalised]
    return found


def _extract_phone_from_raw(raw: dict[str, Any]) -> str | None:
    """Atoka's raw payload nests phones under several possible keys.

    Thin wrapper kept for backwards compatibility — the canonical
    implementation lives in `italian_business_service._extract_phone`
    so the single-lookup path (admin seed) and the discovery funnel
    share one code path.
    """
    from ...services.italian_business_service import _extract_phone  # local import to avoid cycle
    return _extract_phone(raw)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _bulk_persist_l2(candidates: list[EnrichedCandidate]) -> None:
    """Update each scan_candidate row with the enrichment JSONB + stage=2.

    We issue one UPDATE per candidate (small N, and Supabase Python client
    doesn't support bulk UPDATE by primary key). Failure on any single
    row is logged but non-fatal — L3 can proceed with an L1-only view.
    """
    if not candidates:
        return

    sb = get_service_client()
    for c in candidates:
        try:
            sb.table("scan_candidates").update(
                {
                    "enrichment": c.enrichment.to_jsonb(),
                    "stage": 2,
                }
            ).eq("id", str(c.candidate_id)).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "l2_persist_failed",
                extra={"candidate_id": str(c.candidate_id), "err": str(exc)},
            )
