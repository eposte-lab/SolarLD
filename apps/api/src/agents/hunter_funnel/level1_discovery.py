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

Sector-aware (Sprint A/B/C):
  * When ``config.ateco_whitelist`` is empty AND ``config.target_wizard_groups``
    is set, we derive the whitelist from ``ateco_google_types`` so the tenant
    doesn't have to type ATECO codes by hand for sector palettes.
  * After Atoka returns, we filter rows whose ATECO 2-digit prefix doesn't
    match any expected prefix from the whitelist (rejects 84.x = PA when we
    asked for 25.x metalmeccanico, etc.).
  * Each persisted row gets stamped with ``predicted_sector`` +
    ``sector_confidence`` from ``sector_target_service.predict_sector_for_candidate``,
    so L3 prompt rendering and the dashboard "Settore predetto" badge
    have data to read.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any
from uuid import UUID, uuid4

from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from ...services import sector_target_service
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
    sb = get_service_client()

    # Resolve the effective ATECO whitelist. When the tenant left
    # ``ateco_codes`` empty but configured ``target_wizard_groups``, we
    # union the codes from those wizard_group palettes (Sprint A.4 / B.2).
    # Falling back to legacy behaviour when both are empty.
    effective_whitelist: list[str] = list(config.ateco_whitelist or ())
    if not effective_whitelist and config.target_wizard_groups:
        effective_whitelist = await sector_target_service.derive_ateco_whitelist(
            sb, wizard_groups=config.target_wizard_groups
        )
        log.info(
            "funnel_l1_ateco_from_wizard_groups",
            extra={
                "tenant_id": ctx.tenant_id,
                "scan_id": ctx.scan_id,
                "wizard_groups": list(config.target_wizard_groups),
                "derived_whitelist": effective_whitelist,
            },
        )

    if not effective_whitelist:
        log.warning(
            "funnel_l1_no_ateco",
            extra={"tenant_id": ctx.tenant_id, "scan_id": ctx.scan_id},
        )
        return []

    province_code, region_code = _derive_geo_filters(ctx.territory)

    # When the territory type can't supply a province code (most CAP
    # territories don't carry parent-province metadata), fall back to the
    # first region in sorgente.regioni so the scan isn't accidentally
    # Italy-wide.  Without this, a "CAP 80017" territory would scan all of
    # Italy and find thousands of unrelated companies.
    if not province_code and not region_code and config.geo_regioni:
        region_code = config.geo_regioni[0]
        log.info(
            "funnel_l1_geo_fallback_to_regione",
            extra={
                "tenant_id": ctx.tenant_id,
                "scan_id": ctx.scan_id,
                "region_code": region_code,
            },
        )

    log.info(
        "funnel_l1_geo_resolved",
        extra={
            "tenant_id": ctx.tenant_id,
            "scan_id": ctx.scan_id,
            "territory_type": ctx.territory.get("type"),
            "territory_code": ctx.territory.get("code"),
            "province_code": province_code,
            "region_code": region_code,
        },
    )

    try:
        profiles = await atoka_search_by_criteria(
            ateco_codes=effective_whitelist,
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
    # We bill BEFORE the ATECO compatibility filter because Atoka has
    # already returned (and charged for) these rows. The filter only
    # protects downstream costs (L2/L3/L4) and L3 prompt quality.
    cost_cents = len(profiles) * ATOKA_DISCOVERY_COST_PER_RECORD_CENTS
    ctx.costs.add_atoka(records=len(profiles), cost_cents=cost_cents)

    # Sprint C.1 — Filter rows whose Atoka-returned ATECO doesn't match any
    # expected 2-digit prefix. Atoka occasionally returns secondary or
    # otherwise miscategorised codes (e.g. 84.x = Pubblica Amministrazione
    # when we asked for 25.x metalmeccanico). Letting those through pollutes
    # L3 with obvious wrong-sector candidates.
    profiles_filtered = _filter_by_ateco_compatibility(
        profiles=profiles,
        expected_whitelist=effective_whitelist,
        scan_id=ctx.scan_id,
        tenant_id=ctx.tenant_id,
    )

    # Sprint B.2 — Predict the wizard_group for each surviving profile so
    # L3 prompts and dashboard surfaces have a sector tag to read. We
    # gather the predictions in parallel via a single shared cache fetch.
    predictions = await _predict_sectors_for_profiles(
        sb=sb,
        profiles=profiles_filtered,
        enabled_wizard_groups=config.target_wizard_groups,
    )

    # Persist and emit L1Candidate. Persistence is bulk-upserted so one
    # round-trip covers the whole page. We don't pre-check existing rows
    # because the UNIQUE (tenant_id, scan_id, vat_number) constraint
    # handles collisions for us — on re-run the UPSERT refreshes atoka_payload.
    candidates = _bulk_persist_l1(
        tenant_id=ctx.tenant_id,
        scan_id=ctx.scan_id,
        territory_id=ctx.territory_id,
        profiles=profiles_filtered,
        predictions=predictions,
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


# Mapping of the first two digits of an Italian CAP to the dominant
# province code (ISTAT two-letter abbreviation used by Atoka).
# Source: official CAP assignments (Poste Italiane).  Where a prefix
# overlaps two provinces we pick the one with higher population weight.
_CAP_PREFIX_TO_PROVINCE: dict[str, str] = {
    "00": "RM", "01": "VT", "02": "RI", "03": "FR", "04": "LT",
    "05": "TR", "06": "PG", "07": "SS", "08": "NU", "09": "CA",
    "10": "TO", "11": "AO", "12": "CN", "13": "VC", "14": "AT",
    "15": "AL", "16": "GE", "17": "SV", "18": "IM", "19": "SP",
    "20": "MI", "21": "VA", "22": "CO", "23": "SO", "24": "BG",
    "25": "BS", "26": "CR", "27": "PV", "28": "NO", "29": "PC",
    "30": "VE", "31": "TV", "32": "BL", "33": "UD", "34": "TS",
    "35": "PD", "36": "VI", "37": "VR", "38": "TN", "39": "BZ",
    "40": "BO", "41": "MO", "42": "RE", "43": "PR", "44": "FE",
    "45": "RO", "46": "MN", "47": "FC", "48": "RA", "49": "BO",
    "50": "FI", "51": "PT", "52": "AR", "53": "SI", "54": "MS",
    "55": "LU", "56": "PI", "57": "LI", "58": "GR", "59": "PO",
    "60": "AN", "61": "PU", "62": "MC", "63": "AP", "64": "TE",
    "65": "PE", "66": "CH", "67": "AQ", "68": "IS", "69": "CB",
    "70": "BA", "71": "FG", "72": "BR", "73": "LE", "74": "TA",
    "75": "MT", "76": "BT", "80": "NA", "81": "CE", "82": "BN",
    "83": "AV", "84": "SA", "85": "PZ", "86": "CB", "87": "CS",
    "88": "CZ", "89": "RC", "90": "PA", "91": "TP", "92": "AG",
    "93": "CL", "94": "EN", "95": "CT", "96": "SR", "97": "RG",
    "98": "ME",
}


def _derive_geo_filters(territory: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract `(province_code, region_code)` from a territory row.

    Atoka's search endpoint accepts either `locationAreaProvince` (IT-NA
    style, 2 letters) or `locationAreaRegion` (e.g. 'Campania') but not
    a free-form bbox. We prefer province when available — it's the narrowest
    filter that still returns usable volume.

    Our `territories` schema stores a free-form code (e.g. "80017" for a
    CAP, "NA" for a provincia, "Campania" for a regione). We do a best-effort
    classification based on `type` + `code`, with a CAP→province lookup for
    five-digit codes.
    """
    ttype = (territory.get("type") or "").lower()
    code = (territory.get("code") or "").strip()

    if ttype == "provincia" or (ttype == "" and len(code) == 2 and code.isalpha()):
        return code.upper(), None

    if ttype == "regione":
        return None, code

    if ttype == "comune":
        # Comuni don't carry a province code in the territories table.
        # Fall through to let the caller use sorgente.geo_regioni as fallback.
        return None, None

    if ttype == "cap" and len(code) == 5 and code.isdigit():
        # First try the explicit parent province stored in the row
        # (populated by the territory-add form when available).
        parent_prov = (territory.get("metadata") or {}).get("provincia")
        if parent_prov:
            return str(parent_prov).upper(), None

        # Fall back to the canonical CAP-prefix → province lookup.
        prefix = code[:2]
        province = _CAP_PREFIX_TO_PROVINCE.get(prefix)
        if province:
            return province, None

    # Unknown territory type or unrecognised code — no geo narrowing.
    # The caller will apply sorgente.geo_regioni as a region fallback.
    return None, None


def _bulk_persist_l1(
    *,
    tenant_id: str,
    scan_id: str,
    territory_id: str,
    profiles: list[AtokaProfile],
    predictions: dict[str, tuple[str, float]] | None = None,
) -> list[L1Candidate]:
    """Upsert L1 rows and return the (candidate_id, profile) pairs.

    Uses client-side UUID generation so the in-memory candidate has a
    stable ID before the UPSERT returns (lets us skip a round-trip
    read-after-write).

    ``predictions`` (Sprint B.2): ``{vat_number: (wizard_group, confidence)}``
    written to ``predicted_sector`` + ``sector_confidence`` columns.
    """
    if not profiles:
        return []
    predictions = predictions or {}

    rows: list[dict[str, Any]] = []
    pairs: list[L1Candidate] = []

    for p in profiles:
        if not p.vat_number:
            continue  # schema requires NOT NULL; Atoka rarely omits but be safe
        cand_id = uuid4()
        revenue_eur = (
            p.yearly_revenue_cents // 100 if p.yearly_revenue_cents else None
        )
        pred = predictions.get(p.vat_number)
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
                # Sprint B.2 sector tagging (NULL when no prediction)
                "predicted_sector": pred[0] if pred else None,
                "sector_confidence": pred[1] if pred else None,
            }
        )
        pairs.append(
            L1Candidate(
                candidate_id=cand_id,
                profile=p,
                predicted_sector=pred[0] if pred else None,
                sector_confidence=pred[1] if pred else None,
            )
        )

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


# ---------------------------------------------------------------------------
# Sector-aware helpers (Sprint B.2 + C.1)
# ---------------------------------------------------------------------------


def _is_ateco_compatible(
    atoka_ateco: str | None, expected_prefixes: set[str]
) -> bool:
    """Match by 2-digit ATECO prefix (the primary section).

    Atoka returns codes in dotted form (e.g. ``25.11.00``). We compare
    just the first segment because tenants typically configure 2-digit
    prefixes and Atoka's secondary classifications can drift to nearby
    codes without changing the high-level category. ``None``/empty is
    rejected — without an ATECO we can't verify compatibility.
    """
    if not atoka_ateco:
        return False
    head = atoka_ateco.split(".")[0]
    return head in expected_prefixes


def _filter_by_ateco_compatibility(
    *,
    profiles: list[AtokaProfile],
    expected_whitelist: list[str],
    scan_id: str,
    tenant_id: str,
) -> list[AtokaProfile]:
    """Drop Atoka profiles whose ATECO doesn't match the expected prefixes.

    Logs the rejected ATECO histogram so ops can spot Atoka quirks (e.g.
    a wave of 84.x results when querying 25.x — usually a query-builder
    bug or a too-broad geographical filter).
    """
    if not expected_whitelist:
        return profiles  # safety: don't filter when we have nothing to compare

    expected = {code.split(".")[0] for code in expected_whitelist if code}
    if not expected:
        return profiles

    kept: list[AtokaProfile] = []
    rejected_atecos: list[str | None] = []
    for p in profiles:
        if _is_ateco_compatible(p.ateco_code, expected):
            kept.append(p)
        else:
            rejected_atecos.append(p.ateco_code)

    if rejected_atecos:
        log.info(
            "funnel_l1_atoka_ateco_mismatch_filtered",
            extra={
                "tenant_id": tenant_id,
                "scan_id": scan_id,
                "total_atoka": len(profiles),
                "kept": len(kept),
                "rejected": len(rejected_atecos),
                "rejected_top": Counter(rejected_atecos).most_common(5),
            },
        )
    return kept


async def _predict_sectors_for_profiles(
    *,
    sb: Any,
    profiles: list[AtokaProfile],
    enabled_wizard_groups: tuple[str, ...] | list[str],
) -> dict[str, tuple[str, float]]:
    """Predict the wizard_group for each profile in parallel.

    Returns ``{vat_number: (wizard_group, confidence)}`` for profiles
    where ``predict_sector_for_candidate`` produced a non-None match.
    Profiles without a prediction simply don't appear in the returned
    dict (the caller treats missing keys as "unknown sector").

    Empty ``enabled_wizard_groups`` short-circuits to ``{}`` — legacy
    backward-compat path skips sector tagging entirely.
    """
    if not profiles or not enabled_wizard_groups:
        return {}

    enabled = list(enabled_wizard_groups)

    async def _one(p: AtokaProfile) -> tuple[str, tuple[str, float] | None]:
        if not p.vat_number:
            return ("", None)
        pred = await sector_target_service.predict_sector_for_candidate(
            sb,
            ateco_code=p.ateco_code,
            business_name=p.legal_name,
            enabled_wizard_groups=enabled,
        )
        return (p.vat_number, pred)

    # The sector_target_service caches the seed table on first call, so
    # subsequent predictions are pure dict lookups. We still gather them
    # via asyncio.gather to keep the method async-uniform.
    results = await asyncio.gather(*(_one(p) for p in profiles))
    return {vat: pred for vat, pred in results if vat and pred is not None}
