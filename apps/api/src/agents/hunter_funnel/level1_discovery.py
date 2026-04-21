"""Level 1 — Atoka discovery.

Takes a tenant's ICP (ATECO codes + size + geography from the Sorgente
module) and returns a list of matching companies as `AtokaProfile`s.

Cost shape: Atoka charges per record returned, not per API call, so paging
through 5000 results costs ~€50. The cap in `FunnelContext.max_l1_candidates`
is enforced *before* the search kicks off and again as we collect pages —
we stop the moment we've collected enough, even if Atoka has more pages.

Dedupe: a VAT that already exists in `scan_candidates` for the same
`scan_id` is skipped on re-runs (so a retry doesn't double-charge Atoka).
VATs seen in *other* scans stay — the user may want to re-scan a territory
after enriching their ICP, and that's not a dedup case.

No Solar is called at this level. The output rows are persisted with
`stage = 1` and scored further downstream.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from ...services.italian_business_service import (
    ATOKA_DISCOVERY_COST_PER_RECORD_CENTS,
    AtokaProfile,
    EnrichmentUnavailable,
    atoka_search_by_criteria,
)
from .types import FunnelContext, L1Candidate

log = get_logger(__name__)


async def run_level1(ctx: FunnelContext) -> list[L1Candidate]:
    """Atoka discovery by ATECO + province + size + revenue.

    Returns: list of L1Candidate, one per unique VAT persisted in
    `scan_candidates` with `stage=1`. Empty list is a legitimate result
    (no Italian companies matched the tenant's ICP).
    """
    config = ctx.config

    if not config.ateco_whitelist:
        log.warning(
            "funnel_l1_no_ateco",
            extra={"tenant_id": ctx.tenant_id, "scan_id": ctx.scan_id},
        )
        return []

    province_code, region_code = _derive_geo_filters(ctx.territory)

    try:
        profiles = await atoka_search_by_criteria(
            ateco_codes=list(config.ateco_whitelist),
            province_code=province_code,
            region_code=region_code,
            employees_min=config.min_employees,
            employees_max=config.max_employees,
            revenue_min_eur=config.min_revenue_eur,
            revenue_max_eur=config.max_revenue_eur,
            limit=min(ctx.max_l1_candidates, 500),
        )
    except EnrichmentUnavailable as exc:
        log.error(
            "funnel_l1_atoka_failed",
            extra={
                "tenant_id": ctx.tenant_id,
                "scan_id": ctx.scan_id,
                "err": str(exc),
            },
        )
        return []

    # Atoka billing — `len(profiles)` because they bill per returned record.
    cost_cents = len(profiles) * ATOKA_DISCOVERY_COST_PER_RECORD_CENTS
    ctx.costs.add_atoka(records=len(profiles), cost_cents=cost_cents)

    # Persist and emit L1Candidate. Persistence is bulk-upserted so one
    # round-trip covers the whole page. We don't pre-check existing rows
    # because the UNIQUE (tenant_id, scan_id, vat_number) constraint
    # handles collisions for us — on re-run the UPSERT refreshes atoka_payload.
    candidates = _bulk_persist_l1(
        tenant_id=ctx.tenant_id,
        scan_id=ctx.scan_id,
        territory_id=ctx.territory_id,
        profiles=profiles,
    )

    log.info(
        "funnel_l1_complete",
        extra={
            "tenant_id": ctx.tenant_id,
            "scan_id": ctx.scan_id,
            "atoka_returned": len(profiles),
            "persisted": len(candidates),
            "cost_cents": cost_cents,
        },
    )
    return candidates


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _derive_geo_filters(territory: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract `(province, region)` codes from a territory row.

    Atoka's search endpoint accepts either `locationAreaProvince` (IT-NA
    style, 2 letters) or `locationAreaRegion` (e.g. 'Campania') but not
    a free-form bbox. We prefer province when available — it's the narrowest
    filter that still returns usable volume.

    Our `territories` schema stores a free-form code (e.g. "80100" for a
    CAP, "NA" for a provincia, "Campania" for a regione). We do a best-effort
    classification based on `type` + `code` length.
    """
    ttype = (territory.get("type") or "").lower()
    code = (territory.get("code") or "").strip()

    if ttype == "provincia" or (ttype == "" and len(code) == 2 and code.isalpha()):
        return code.upper(), None
    if ttype == "regione":
        return None, code
    if ttype == "cap" and len(code) == 5:
        # A CAP doesn't map cleanly to Atoka — fall back to the territory's
        # parent provincia if the row carries one in its JSON blob.
        parent_prov = (territory.get("metadata") or {}).get("provincia")
        if parent_prov:
            return parent_prov.upper(), None
    # Unknown territory type → no geo narrowing. Atoka will return
    # country-wide results; the ATECO filter remains in place so this
    # isn't a "fetch all of Italy" disaster.
    return None, None


def _bulk_persist_l1(
    *,
    tenant_id: str,
    scan_id: str,
    territory_id: str,
    profiles: list[AtokaProfile],
) -> list[L1Candidate]:
    """Upsert L1 rows and return the (candidate_id, profile) pairs.

    Uses client-side UUID generation so the in-memory candidate has a
    stable ID before the UPSERT returns (lets us skip a round-trip
    read-after-write).
    """
    if not profiles:
        return []

    rows: list[dict[str, Any]] = []
    pairs: list[L1Candidate] = []

    for p in profiles:
        if not p.vat_number:
            continue  # schema requires NOT NULL; Atoka rarely omits but be safe
        cand_id = uuid4()
        revenue_eur = (
            p.yearly_revenue_cents // 100 if p.yearly_revenue_cents else None
        )
        rows.append(
            {
                "id": str(cand_id),
                "tenant_id": tenant_id,
                "scan_id": scan_id,
                "territory_id": territory_id,
                "vat_number": p.vat_number,
                "business_name": p.legal_name or None,
                "ateco_code": p.ateco_code,
                "employees": p.employees,
                "revenue_eur": revenue_eur,
                "hq_address": p.hq_address,
                "hq_cap": p.hq_cap,
                "hq_city": p.hq_city,
                "hq_province": p.hq_province,
                "hq_lat": p.hq_lat,
                "hq_lng": p.hq_lng,
                "atoka_payload": p.raw,
                "stage": 1,
            }
        )
        pairs.append(L1Candidate(candidate_id=cand_id, profile=p))

    if not rows:
        return []

    sb = get_service_client()
    try:
        # ON CONFLICT (tenant_id, scan_id, vat_number) — matches the UNIQUE
        # from migration 0031. On conflict we refresh the mutable fields so
        # a re-run with updated Atoka data overwrites stale payload.
        result = sb.table("scan_candidates").upsert(
            rows, on_conflict="tenant_id,scan_id,vat_number"
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.error("funnel_l1_upsert_failed", extra={"err": str(exc)})
        return []

    # On upsert-conflict Postgres returns the *existing* row's id rather
    # than the one we sent, so we rewrite candidate_id from the response.
    returned = result.data or []
    by_vat: dict[str, UUID] = {}
    for row in returned:
        vat = row.get("vat_number")
        rid = row.get("id")
        if vat and rid:
            by_vat[vat] = UUID(rid)

    for cand in pairs:
        real_id = by_vat.get(cand.profile.vat_number)
        if real_id is not None:
            cand.candidate_id = real_id

    return pairs
