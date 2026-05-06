"""On-demand validation of a /scoperta saved list.

Triggered by `POST /v1/prospector/lists/{id}/validate`. Iterates over
items with `validation_status='pending'` and runs the v3 funnel stages
L2 (web scraping) + L3 (building quality heuristics) + L4 (Solar API)
on each one. The L5 Haiku scoring batch is intentionally **not** run
here — that's a per-tenant cron concern. Instead, accepted items are
left ready for the on-demand outreach launch (see
`prospect_list_outreach.py`).

For each item:
  1. Insert a `scan_candidates` row stage=1 with the Places enrichment.
  2. Mark the item `validating`.
  3. Run scraping → quality → Solar inline.
  4. Update `scan_candidates.solar_verdict` + `roof_id` (when accepted).
  5. Update `prospect_list_items.validation_status` from the verdict.
  6. Record list-level lifecycle (`validation_started_at` /
     `validation_completed_at`).

The function is idempotent: re-running on a list that already has
some items processed will only retry the remaining `pending` ones.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import geohash
import httpx

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .building_quality_filter import passes_filter_simple
from .google_solar_service import (
    SolarApiError,
    SolarApiNotFound,
    SolarApiRateLimited,
    fetch_building_insight,
)
from .web_scraper import extract_best_email, scrape_all_for_candidate

log = get_logger(__name__)


# Same thresholds as L4 funnel — kept in sync with `level4_solar_qualify.py`.
MIN_AREA_M2 = 200.0
MIN_KW_INSTALLABILE = 60.0
MIN_SUNSHINE_HOURS = 1200.0


# Map solar_verdict → prospect_list_items.validation_status
VERDICT_TO_STATUS: dict[str, str] = {
    "accepted": "accepted",
    "rejected_tech": "rejected",
    "no_building": "no_building",
    "api_error": "api_error",
    "skipped_below_gate": "skipped",
    # generic_outreach campaigns bypass the Solar gate entirely; the
    # candidate is "accepted" for outreach without rooftop qualification.
    "skipped_non_solar": "accepted",
}


@dataclass(slots=True)
class ValidationResult:
    list_id: str
    total: int
    processed: int
    by_status: dict[str, int]


async def validate_prospect_list(
    *, tenant_id: str, list_id: str
) -> ValidationResult:
    """Run v3 convalida (L2+L3+L4) on every pending item of a list.

    When ``prospect_lists.campaign_type='generic_outreach'`` the L4 Solar
    gate is bypassed (and a placeholder roof is created so that the
    downstream subjects/leads creation in ``prospect_list_outreach.py``
    keeps working without schema changes). All other stages run normally
    so emails/phones still get scraped from each company's website.
    """
    sb = get_service_client()

    # ── Read campaign_type to know whether to run L4 ─────────────────────
    list_meta = (
        sb.table("prospect_lists")
        .select("campaign_type")
        .eq("id", list_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .maybe_single()
        .execute()
    )
    campaign_type: str = (list_meta.data or {}).get("campaign_type") or "solar_rooftop"

    # ── Mark list as validating ──────────────────────────────────────────
    sb.table("prospect_lists").update(
        {"validation_started_at": datetime.utcnow().isoformat()}
    ).eq("id", list_id).eq("tenant_id", tenant_id).execute()

    # ── Pull pending items (avoid huge result sets — process in pages) ──
    items = (
        sb.table("prospect_list_items")
        .select(
            "id, google_place_id, legal_name, place_lat, place_lng, "
            "place_types, business_status, user_ratings_total, rating, "
            "website_domain, phone, scan_candidate_id, validation_status, "
            "hq_address"
        )
        .eq("tenant_id", tenant_id)
        .eq("list_id", list_id)
        .eq("validation_status", "pending")
        .execute()
    )
    rows = items.data or []
    if not rows:
        log.info("prospect_validate.no_pending_items", list_id=list_id)
        sb.table("prospect_lists").update(
            {"validation_completed_at": datetime.utcnow().isoformat()}
        ).eq("id", list_id).execute()
        return ValidationResult(list_id=list_id, total=0, processed=0, by_status={})

    log.info(
        "prospect_validate.start",
        tenant_id=tenant_id,
        list_id=list_id,
        items_pending=len(rows),
    )

    # Reuse a single httpx client across rows to keep TLS connections warm.
    async with httpx.AsyncClient(
        timeout=10.0,
        headers={"User-Agent": "solarlead-validate/1.0 (+https://solarlead.it)"},
    ) as client:
        # Use a synthetic scan_id for these rows so they don't conflict
        # with the daily cron's scan numbering. Same id reused across
        # all items in the same list run.
        scan_id = f"prospect:{list_id}"

        by_status: dict[str, int] = {}
        for row in rows:
            verdict = await _validate_one(
                sb=sb,
                client=client,
                tenant_id=tenant_id,
                scan_id=scan_id,
                item=row,
                campaign_type=campaign_type,
            )
            by_status[verdict] = by_status.get(verdict, 0) + 1

    # ── Mark list as completed ───────────────────────────────────────────
    sb.table("prospect_lists").update(
        {"validation_completed_at": datetime.utcnow().isoformat()}
    ).eq("id", list_id).execute()

    log.info(
        "prospect_validate.done",
        tenant_id=tenant_id,
        list_id=list_id,
        processed=len(rows),
        by_status=by_status,
    )
    return ValidationResult(
        list_id=list_id,
        total=len(rows),
        processed=len(rows),
        by_status=by_status,
    )


async def _validate_one(
    *,
    sb: Any,
    client: httpx.AsyncClient,
    tenant_id: str,
    scan_id: str,
    item: dict[str, Any],
    campaign_type: str = "solar_rooftop",
) -> str:
    """Process a single prospect_list_items row end-to-end.

    Returns the final `validation_status` for telemetry.

    When ``campaign_type='generic_outreach'`` the L4 Solar gate is
    skipped and a placeholder roof is created so the existing
    subjects/leads creation path stays unchanged. The candidate is
    flagged with ``solar_verdict='skipped_non_solar'`` to keep the
    /contatti page (which filters on ``solar_verdict='accepted'``)
    free of non-rooftop entries.
    """
    item_id = item["id"]
    place_id = item.get("google_place_id")
    lat = item.get("place_lat")
    lng = item.get("place_lng")
    name = item.get("legal_name") or "(Senza nome)"
    address = item.get("hq_address")
    website = item.get("website_domain")
    phone_initial = item.get("phone")

    if not place_id or lat is None or lng is None:
        sb.table("prospect_list_items").update(
            {
                "validation_status": "skipped",
                "validated_at": datetime.utcnow().isoformat(),
            }
        ).eq("id", item_id).execute()
        return "skipped"

    # ── Mark validating + create/find scan_candidate ─────────────────────
    sb.table("prospect_list_items").update(
        {"validation_status": "validating"}
    ).eq("id", item_id).execute()

    # Try to reuse an existing scan_candidate for this place_id (the daily
    # cron may have already discovered it). If not, create one stage=1
    # with the Places enrichment we have on the prospect_list_items row.
    candidate_id = item.get("scan_candidate_id")
    if not candidate_id:
        existing = (
            sb.table("scan_candidates")
            .select("id")
            .eq("tenant_id", tenant_id)
            .eq("google_place_id", place_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            candidate_id = existing.data[0]["id"]

    if not candidate_id:
        enrichment = {
            "places": {
                "display_name": name,
                "formatted_address": address,
                "lat": float(lat),
                "lng": float(lng),
                "types": item.get("place_types") or [],
                "business_status": item.get("business_status"),
                "user_ratings_total": item.get("user_ratings_total"),
                "rating": item.get("rating"),
                "website": website,
                "phone": phone_initial,
            }
        }
        new_id = str(uuid.uuid4())
        sb.table("scan_candidates").insert(
            {
                "id": new_id,
                "tenant_id": tenant_id,
                "scan_id": scan_id,
                "stage": 1,
                "google_place_id": place_id,
                "enrichment": enrichment,
            }
        ).execute()
        candidate_id = new_id

    # Persist the scan_candidate_id back on the item so subsequent runs
    # (or the outreach launch) can find it.
    sb.table("prospect_list_items").update(
        {"scan_candidate_id": candidate_id}
    ).eq("id", item_id).execute()

    # ── L2 — Scraping ────────────────────────────────────────────────────
    try:
        scraped = await scrape_all_for_candidate(
            website=website,
            business_name=name,
            city=None,
            client=client,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "prospect_validate.scrape_failed",
            item_id=item_id,
            err=type(exc).__name__,
        )
        scraped = None

    scraped_data: dict[str, Any] = {}
    contact_extraction: dict[str, Any] = {}
    if scraped is not None:
        scraped_data = {
            "website_url": scraped.site.url if scraped.site.url else None,
            "site_emails": list(scraped.site.emails),
            "site_pec": scraped.site.pec,
            "site_phone": scraped.site.phone,
            "pagine_bianche_phone": scraped.pb.phone,
            "opencorporates_vat": scraped.oc.vat_number,
        }
        all_emails = list(scraped.site.emails)
        if scraped.pb.email and scraped.pb.email not in all_emails:
            all_emails.append(scraped.pb.email)
        best = extract_best_email(all_emails)
        contact_extraction = {
            "best_email": best.email if best else None,
            "best_phone": scraped.site.phone or scraped.pb.phone or phone_initial,
        }
        sb.table("scan_candidates").update(
            {
                "stage": 2,
                "scraped_data": scraped_data,
                "contact_extraction": contact_extraction,
            }
        ).eq("id", candidate_id).execute()

    # ── L3 — Building quality (heuristics) ───────────────────────────────
    quality = passes_filter_simple(
        user_ratings_total=item.get("user_ratings_total"),
        website=website or scraped_data.get("website_url"),
        phone=contact_extraction.get("best_phone") or phone_initial,
        business_status=item.get("business_status"),
    )
    sb.table("scan_candidates").update(
        {
            "stage": 3,
            "building_quality_score": quality.score,
        }
    ).eq("id", candidate_id).execute()

    # ── Bypass L4 for generic_outreach campaigns ─────────────────────────
    # Non-Solar campaigns (e.g. amministratori condominio) skip the
    # Google Solar gate. Create a placeholder roof so the unchanged
    # downstream `prospect_list_outreach._promote_to_lead` path still
    # finds a roof_id to attach the subject to.
    if campaign_type == "generic_outreach":
        roof_id = _persist_placeholder_roof(
            sb,
            tenant_id=tenant_id,
            lat=float(lat),
            lng=float(lng),
            address=address,
        )
        if roof_id is None:
            return _mark_verdict(sb, item_id, candidate_id, "api_error")

        sb.table("scan_candidates").update(
            {
                "tenant_id": tenant_id,
                "scan_id": scan_id,
                "stage": 4,
                "solar_verdict": "skipped_non_solar",
                "roof_id": roof_id,
            }
        ).eq("id", candidate_id).execute()

        sb.table("prospect_list_items").update(
            {
                "validation_status": "accepted",
                "validated_at": datetime.utcnow().isoformat(),
            }
        ).eq("id", item_id).execute()
        return "accepted"

    # ── L4 — Solar API ───────────────────────────────────────────────────
    try:
        insight = await fetch_building_insight(float(lat), float(lng), client=client)
    except SolarApiNotFound:
        return _mark_verdict(sb, item_id, candidate_id, "no_building")
    except (SolarApiRateLimited, SolarApiError) as exc:
        log.warning(
            "prospect_validate.solar_api_error",
            item_id=item_id,
            err=type(exc).__name__,
        )
        return _mark_verdict(sb, item_id, candidate_id, "api_error")

    sunshine = insight.estimated_yearly_kwh / max(insight.estimated_kwp, 1.0)
    if (
        insight.area_sqm < MIN_AREA_M2
        or insight.estimated_kwp < MIN_KW_INSTALLABILE
        or sunshine < MIN_SUNSHINE_HOURS
    ):
        return _mark_verdict(sb, item_id, candidate_id, "rejected_tech")

    # Accept — persist roof + link
    roof_row = {
        "tenant_id": tenant_id,
        "lat": insight.lat,
        "lng": insight.lng,
        "geohash": geohash.encode(insight.lat, insight.lng, precision=8),
        "data_source": "google_solar",
        "area_sqm": insight.area_sqm,
        "estimated_kwp": insight.estimated_kwp,
        "estimated_yearly_kwh": insight.estimated_yearly_kwh,
        "exposure": insight.dominant_exposure,
        "pitch_degrees": insight.pitch_degrees,
        "shading_score": insight.shading_score,
        "raw_data": insight.raw,
        "address": (
            (insight.locality or "") + " " + (insight.postal_code or "")
        ).strip()
        or address,
        "comune": insight.locality,
        "cap": insight.postal_code,
        "status": "identified",
    }
    try:
        res = (
            sb.table("roofs")
            .upsert(roof_row, on_conflict="tenant_id,geohash")
            .execute()
        )
        roof_id = res.data[0]["id"] if res.data else None
        if not roof_id:
            existing = (
                sb.table("roofs")
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq("geohash", roof_row["geohash"])
                .limit(1)
                .execute()
            )
            roof_id = (existing.data or [{}])[0].get("id")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "prospect_validate.roof_persist_failed",
            err=type(exc).__name__,
            msg=str(exc)[:300],
        )
        return _mark_verdict(sb, item_id, candidate_id, "api_error")

    sb.table("scan_candidates").update(
        {
            "tenant_id": tenant_id,
            "scan_id": scan_id,
            "stage": 4,
            "solar_verdict": "accepted",
            "roof_id": roof_id,
        }
    ).eq("id", candidate_id).execute()

    sb.table("prospect_list_items").update(
        {
            "validation_status": "accepted",
            "validated_at": datetime.utcnow().isoformat(),
        }
    ).eq("id", item_id).execute()
    return "accepted"


def _mark_verdict(sb: Any, item_id: str, candidate_id: str, verdict: str) -> str:
    """Persist a non-accepted verdict on both scan_candidate + item, and
    return the mapped prospect_list_items.validation_status."""
    sb.table("scan_candidates").update(
        {"stage": 4, "solar_verdict": verdict}
    ).eq("id", candidate_id).execute()
    status_value = VERDICT_TO_STATUS.get(verdict, "skipped")
    sb.table("prospect_list_items").update(
        {
            "validation_status": status_value,
            "validated_at": datetime.utcnow().isoformat(),
        }
    ).eq("id", item_id).execute()
    return status_value


def _persist_placeholder_roof(
    sb: Any,
    *,
    tenant_id: str,
    lat: float,
    lng: float,
    address: str | None,
) -> str | None:
    """Insert (or look up) a placeholder ``roofs`` row for non-Solar campaigns.

    Generic_outreach campaigns (amministratori condominio, dental clinics,
    etc.) don't need rooftop validation but the schema requires every
    ``subjects`` row to have a non-NULL ``roof_id``. Rather than make
    ``subjects.roof_id`` nullable (which would touch every JOIN in the
    codebase) we attach a minimal roof carrying only the lat/lng + Places
    address. Solar metric columns (area_sqm, estimated_kwp, …) stay NULL,
    and the existing DataRow auto-hide on the lead detail page makes the
    "Tetto e impianto" card collapse gracefully.

    Idempotent: same ``(tenant_id, geohash)`` returns the existing row
    rather than 23505. This means two prospect_list_items at the same
    physical address share a roof — fine for our purposes (it'd be the
    same roof anyway) and matches the production solar path's behaviour.
    """
    gh = geohash.encode(lat, lng, precision=8)
    row = {
        "tenant_id": tenant_id,
        "lat": lat,
        "lng": lng,
        "geohash": gh,
        "data_source": "places_only",
        "address": address,
        "status": "non_solar",
    }
    try:
        res = (
            sb.table("roofs")
            .upsert(row, on_conflict="tenant_id,geohash")
            .execute()
        )
        roof_id = res.data[0]["id"] if res.data else None
        if not roof_id:
            existing = (
                sb.table("roofs")
                .select("id")
                .eq("tenant_id", tenant_id)
                .eq("geohash", gh)
                .limit(1)
                .execute()
            )
            roof_id = (existing.data or [{}])[0].get("id")
        return roof_id
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "prospect_validate.placeholder_roof_failed",
            err=type(exc).__name__,
            msg=str(exc)[:300],
        )
        return None
