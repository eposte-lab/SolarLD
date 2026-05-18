"""L1 — Places-first discovery (FLUSSO 1 v3).

Discovery grows the candidate POOL; it does not feed the funnel
directly. The funnel consumes the pool via a cursor (see
``load_backlog``), one daily batch at a time, so a recurring scan
walks the territory progressively instead of re-processing the same
contacts.

  1. Load the active zones of the scan's comune from
     `tenant_target_areas` (a scan job only touches its own comune).
  2. Skip zones discovered within `_FRESHNESS_DAYS`, or flagged
     `depleted` — no point re-paying Google Places for a zone that
     has nothing new yet.
  3. For each remaining zone, `discover_for_zone` fetches candidates.
  4. Cross-zone dedupe by `google_place_id`.
  5. Insert ONLY genuinely new candidates (`ON CONFLICT DO NOTHING`):
     candidates already in the pool keep their `processed_at` cursor.
  6. Write back per-zone consumption (`last_discovered_at`,
     `candidates_found`, `depleted`).

Cost model: each Nearby call is `NEARBY_COST_CENTS` (~2¢). Skipping
fresh zones is what keeps a "sempre attiva" scan from re-paying
Places every day.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from ...services.places_discovery import (
    NEARBY_COST_CENTS,
    PlaceCandidate,
    discover_for_zone,
)
from ...services.places_to_sector import classify_place
from ...services.sector_target_service import (
    SectorAreaMapping,
    _warm_cache,
    get_sector_config_by_wizard_group,
)
from ...services.web_scraper import is_non_business_domain
from .types_v3 import FunnelV3Context, PlaceCandidateRecord

log = get_logger(__name__)

# A zone discovered within this many days is not re-queried on Places —
# it almost certainly has no new businesses yet, and re-querying just
# burns API budget. New businesses open slowly, so a quarterly window
# is enough for a "sempre attiva" scan to pick them up without re-paying
# Places every couple of weeks for nothing.
_FRESHNESS_DAYS = 90

# On a re-discovery, a zone that yields this few NEW candidates is
# considered tapped out and flagged `depleted` (skipped from then on).
_DEPLETED_NEW_THRESHOLD = 2


def _parse_ts(value: Any) -> datetime:
    """Parse an ISO timestamp (str or datetime) into an aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _row_to_record(row: dict[str, Any]) -> PlaceCandidateRecord:
    """Project a scan_candidates row into a typed PlaceCandidateRecord."""
    place_blob = (row.get("enrichment") or {}).get("places") or {}
    return PlaceCandidateRecord(
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


async def run_level1_places(ctx: FunnelV3Context) -> dict[str, Any]:
    """L1 discovery — grow the candidate pool with genuinely NEW places.

    Does NOT return candidates for processing — the orchestrator reads
    the consumption cursor (`load_backlog`) afterwards. Returns a
    summary dict: ``discovered`` (new candidates inserted),
    ``zones_total``, ``zones_skipped_fresh``, ``places_calls``.
    """
    sb = get_service_client()

    # 1) Load the zones of this scan's territory.
    zq = (
        sb.table("tenant_target_areas")
        .select(
            "id, primary_sector, matched_sectors, centroid_lat, centroid_lng, "
            "area_m2, last_discovered_at, depleted, candidates_found"
        )
        .eq("tenant_id", ctx.tenant_id)
        .eq("status", "active")
    )
    if ctx.comune:
        zq = zq.eq("comune", ctx.comune)
    elif ctx.province_code:
        zq = zq.eq("province_code", ctx.province_code)
    zones = (zq.order("matching_score", desc=True).execute()).data or []
    if not zones:
        log.info("level1_places.no_zones", tenant_id=ctx.tenant_id, comune=ctx.comune)
        return {"discovered": 0, "zones_total": 0, "zones_skipped_fresh": 0, "places_calls": 0}

    # 2) Pre-warm the sector palette cache + resolve configs.
    await _warm_cache(sb)
    sectors_in_play = sorted({z["primary_sector"] for z in zones if z.get("primary_sector")})
    sector_configs: dict[str, SectorAreaMapping] = {}
    for s in sectors_in_play:
        cfg = await get_sector_config_by_wizard_group(sb, wizard_group=s)
        if cfg is not None:
            sector_configs[s] = cfg

    # Strict allow-list of target sectors (zone primary + matched).
    target_sectors: set[str] = set(sectors_in_play)
    for z in zones:
        for s in z.get("matched_sectors") or []:
            if isinstance(s, str):
                target_sectors.add(s)

    # 3) Iterate zones — skip fresh / depleted ones (no Places re-spend).
    now = datetime.now(tz=UTC)
    fresh_cutoff = now - timedelta(days=_FRESHNESS_DAYS)
    all_candidates: dict[str, tuple[PlaceCandidate, dict[str, Any]]] = {}
    total_calls = 0
    zones_skipped_fresh = 0
    zones_discovered: list[dict[str, Any]] = []

    for z in zones:
        sector = z.get("primary_sector")
        cfg = sector_configs.get(sector) if sector else None
        if cfg is None:
            continue
        if z.get("depleted"):
            zones_skipped_fresh += 1
            continue
        last_disc = z.get("last_discovered_at")
        if last_disc and _parse_ts(last_disc) > fresh_cutoff:
            zones_skipped_fresh += 1
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
        zones_discovered.append(z)

        for cand in candidates:
            if cand.place_id in all_candidates:
                continue
            # Sector classification: prefer the business's real Google
            # `place.types`, fall back to the zone primary sector.
            type_based = classify_place(cand.types)
            resolved_sector = type_based or sector
            if resolved_sector not in target_sectors:
                continue
            cand.discovered_in_zone_id = str(z["id"])
            cand.discovered_for_sector = resolved_sector
            all_candidates[cand.place_id] = (cand, z)

    # 4) Cost accounting.
    cost_cents = total_calls * NEARBY_COST_CENTS
    ctx.costs.add_places(calls=total_calls, cost_cents=cost_cents)

    # 5) Insert ONLY new candidates — `ignore_duplicates` makes the
    #    upsert a DO NOTHING, so candidates already in the pool keep
    #    their processed_at cursor (no reset, no re-processing).
    persisted: list[dict[str, Any]] = []
    if all_candidates:
        rows = []
        for _place_id, (cand, _zone) in all_candidates.items():
            website = cand.website
            if website and is_non_business_domain(website):
                website = None
            rows.append(
                {
                    "tenant_id": ctx.tenant_id,
                    "comune": ctx.comune,
                    "scan_id": ctx.scan_id,
                    "stage": 1,
                    "google_place_id": cand.place_id,
                    "predicted_sector": cand.discovered_for_sector,
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
                            "website": website,
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
            .upsert(rows, on_conflict="tenant_id,google_place_id", ignore_duplicates=True)
            .execute()
        )
        persisted = res.data or []

    # 6) Per-zone consumption write-back: stamp last_discovered_at,
    #    accumulate candidates_found, flag depleted when a re-discovery
    #    brought in (almost) nothing new.
    inserted_by_zone: dict[str, int] = {}
    for row in persisted:
        zid = ((row.get("enrichment") or {}).get("places") or {}).get("zone_id")
        if zid:
            inserted_by_zone[str(zid)] = inserted_by_zone.get(str(zid), 0) + 1

    now_iso = now.isoformat()
    for z in zones_discovered:
        zid = str(z["id"])
        n_new = inserted_by_zone.get(zid, 0)
        was_discovered_before = z.get("last_discovered_at") is not None
        sb.table("tenant_target_areas").update(
            {
                "last_discovered_at": now_iso,
                "candidates_found": int(z.get("candidates_found") or 0) + n_new,
                "depleted": was_discovered_before and n_new <= _DEPLETED_NEW_THRESHOLD,
            }
        ).eq("id", zid).execute()

    log.info(
        "level1_places.done",
        tenant_id=ctx.tenant_id,
        comune=ctx.comune,
        zones_total=len(zones),
        zones_skipped_fresh=zones_skipped_fresh,
        places_calls=total_calls,
        discovered=len(persisted),
        cost_cents=cost_cents,
    )
    return {
        "discovered": len(persisted),
        "zones_total": len(zones),
        "zones_skipped_fresh": zones_skipped_fresh,
        "places_calls": total_calls,
    }


async def load_backlog(ctx: FunnelV3Context, *, limit: int) -> list[PlaceCandidateRecord]:
    """Read the next batch of un-processed candidates (the consumption cursor).

    A candidate with ``processed_at IS NULL`` has not yet been run
    through L2-L6. Ordered oldest-first so a recurring scan walks the
    territory progressively (day 1: contacts 1-N, day 2: N+1-2N, …).
    Scoped to the scan's comune so jobs on different territories don't
    consume each other's backlog.
    """
    sb = get_service_client()
    q = (
        sb.table("scan_candidates")
        .select("id, google_place_id, predicted_sector, enrichment")
        .eq("tenant_id", ctx.tenant_id)
        .is_("processed_at", "null")
        .not_.is_("google_place_id", "null")
    )
    if ctx.comune:
        q = q.eq("comune", ctx.comune)
    rows = (q.order("created_at").limit(limit).execute()).data or []
    return [_row_to_record(r) for r in rows]


async def mark_processed(ctx: FunnelV3Context, candidate_ids: list[str]) -> None:
    """Stamp ``processed_at`` on a batch so the cursor advances past it.

    Called after the funnel has decided each candidate's fate (promoted
    to a lead or filtered out) — either way it is consumed and must not
    re-enter the backlog.
    """
    if not candidate_ids:
        return
    sb = get_service_client()
    now_iso = datetime.now(tz=UTC).isoformat()
    sb.table("scan_candidates").update({"processed_at": now_iso}).in_("id", candidate_ids).execute()
