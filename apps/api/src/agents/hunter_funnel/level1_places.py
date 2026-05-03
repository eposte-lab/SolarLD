"""L1 — Places-first discovery (FLUSSO 1 v3).

Replaces the v2 Atoka-first L1. The flow:

  1. Load active zones for this tenant from `tenant_target_areas`.
  2. For each zone, look up the sector palette
     (places_keywords, search_radius_m, places_excluded_types) via
     `sector_target_service.get_sector_config_by_wizard_group`.
  3. Call ``places_discovery.discover_for_zone`` to fetch candidates
     with **precise coords** (the capannone, not the headquarter
     registered office).
  4. Cross-zone deduplicate by ``google_place_id`` (the same business
     can show up in multiple adjacent zones; first hit wins).
  5. Bulk-insert into ``scan_candidates`` with stage=1, predicted_sector
     stamped from the zone's primary_sector.

Cost model: each Nearby call is `NEARBY_COST_CENTS` (~2¢). 100 zones ×
1 call each = ~€2/scan. Far cheaper than Atoka discovery (€375 / 100 lead
in v2).

Backward-compat: this lives alongside v2's ``level1_discovery.py``. The
v3 orchestrator (Sprint 4.4) picks v3 when the tenant has at least one
zone in `tenant_target_areas`; otherwise it falls back to v2 (Atoka)
until the demolition lands.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from ...services.places_discovery import (
    NEARBY_COST_CENTS,
    PlaceCandidate,
    discover_for_zone,
)
from ...services.sector_target_service import (
    SectorAreaMapping,
    _warm_cache,
    get_sector_config_by_wizard_group,
)
from .types_v3 import FunnelV3Context, PlaceCandidateRecord

log = get_logger(__name__)


async def run_level1_places(ctx: FunnelV3Context) -> list[PlaceCandidateRecord]:
    """Discover candidates by iterating over the tenant's mapped zones.

    Returns the freshly persisted ``PlaceCandidateRecord``s. Caller
    (orchestrator v3) feeds them into L2 scraping.
    """
    sb = get_service_client()

    # 1) Load zones — just the columns we need to drive discovery.
    zones_res = (
        sb.table("tenant_target_areas")
        .select(
            "id, primary_sector, matched_sectors, centroid_lat, centroid_lng, area_m2"
        )
        .eq("tenant_id", ctx.tenant_id)
        .eq("status", "active")
        .order("matching_score", desc=True)
        .execute()
    )
    zones = zones_res.data or []
    if not zones:
        log.info("level1_places.no_zones", tenant_id=ctx.tenant_id)
        return []

    # 2) Pre-warm the sector palette cache.
    await _warm_cache(sb)

    # 3) Group zones by primary_sector so we can fetch each palette once.
    sectors_in_play = sorted(
        {z["primary_sector"] for z in zones if z.get("primary_sector")}
    )
    sector_configs: dict[str, SectorAreaMapping] = {}
    for s in sectors_in_play:
        cfg = await get_sector_config_by_wizard_group(sb, wizard_group=s)
        if cfg is not None:
            sector_configs[s] = cfg

    # 4) Iterate zones, fan out Places Nearby calls. Cross-zone dedupe by
    #    place_id. We keep the FIRST match (highest-score zone first since
    #    we ordered DESC above), preserving the candidate's "best zone" tag.
    all_candidates: dict[str, tuple[PlaceCandidate, dict[str, Any]]] = {}
    total_calls = 0

    for z in zones:
        if len(all_candidates) >= ctx.max_l1_candidates:
            log.info(
                "level1_places.cap_reached",
                tenant_id=ctx.tenant_id,
                cap=ctx.max_l1_candidates,
            )
            break
        sector = z.get("primary_sector")
        cfg = sector_configs.get(sector) if sector else None
        if cfg is None:
            continue

        try:
            candidates, calls = await discover_for_zone(
                centroid_lat=float(z["centroid_lat"]),
                centroid_lng=float(z["centroid_lng"]),
                sector_config=cfg,
            )
        except Exception as exc:  # noqa: BLE001 — Places call is the boundary
            log.warning(
                "level1_places.zone_error",
                zone_id=z.get("id"),
                err=type(exc).__name__,
            )
            continue
        total_calls += calls

        for cand in candidates:
            if cand.place_id in all_candidates:
                continue
            cand.discovered_in_zone_id = str(z["id"])
            cand.discovered_for_sector = sector
            all_candidates[cand.place_id] = (cand, z)

    # 5) Cost accounting.
    cost_cents = total_calls * NEARBY_COST_CENTS
    ctx.costs.add_places(calls=total_calls, cost_cents=cost_cents)

    if not all_candidates:
        log.info("level1_places.no_candidates", tenant_id=ctx.tenant_id, calls=total_calls)
        return []

    # 6) Bulk insert into scan_candidates. We rely on the v3 schema additions
    #    (google_place_id, predicted_sector). Until migration 0100 lands, the
    #    legacy scan_candidates schema will accept these as additional
    #    columns once 0102 (which keeps roof_id around) is applied.
    rows = []
    for place_id, (cand, zone) in all_candidates.items():
        rows.append(
            {
                "tenant_id": ctx.tenant_id,
                "scan_id": ctx.scan_id,
                "stage": 1,
                "google_place_id": cand.place_id,
                "predicted_sector": cand.discovered_for_sector,
                # When migrating, these enrichment fields move into a single
                # `place_data` blob inside scraped_data; for now we project
                # them onto the existing columns so v3 candidates are
                # readable without the full demolition shipping.
                "enrichment": {
                    "places": {
                        "display_name": cand.display_name,
                        "formatted_address": cand.formatted_address,
                        "lat": cand.lat,
                        "lng": cand.lng,
                        "types": cand.types,
                        "business_status": cand.business_status,
                        "user_ratings_total": cand.user_ratings_total,
                        "rating": cand.rating,
                        "website": cand.website,
                        "phone": cand.phone,
                        "google_maps_uri": cand.google_maps_uri,
                        "discovery_keyword": cand.discovery_keyword,
                        "zone_id": cand.discovered_in_zone_id,
                    }
                },
            }
        )

    res = (
        sb.table("scan_candidates")
        .upsert(rows, on_conflict="tenant_id,google_place_id")
        .execute()
    )
    persisted = res.data or []

    # 7) Build typed records for downstream stages.
    out: list[PlaceCandidateRecord] = []
    for row in persisted:
        place_blob = (row.get("enrichment") or {}).get("places") or {}
        out.append(
            PlaceCandidateRecord(
                candidate_id=row["id"],
                google_place_id=row["google_place_id"],
                display_name=place_blob.get("display_name"),
                formatted_address=place_blob.get("formatted_address"),
                lat=float(place_blob.get("lat") or 0.0),
                lng=float(place_blob.get("lng") or 0.0),
                types=list(place_blob.get("types") or []),
                business_status=place_blob.get("business_status"),
                user_ratings_total=place_blob.get("user_ratings_total"),
                rating=place_blob.get("rating"),
                website=place_blob.get("website"),
                phone=place_blob.get("phone"),
                google_maps_uri=place_blob.get("google_maps_uri"),
                zone_id=place_blob.get("zone_id"),
                predicted_sector=row.get("predicted_sector"),
                discovery_keyword=place_blob.get("discovery_keyword"),
            )
        )

    log.info(
        "level1_places.done",
        tenant_id=ctx.tenant_id,
        zones_scanned=len(zones),
        sectors_covered=len(sectors_in_play),
        places_calls=total_calls,
        candidates_found=len(all_candidates),
        candidates_persisted=len(out),
        cost_cents=cost_cents,
    )
    return out
