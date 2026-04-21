"""B2C post-engagement Solar qualify — the *inverted* funnel.

Standard B2B flow: scan → qualify roof → outreach → engagement.
B2C flow here is the opposite: outreach (Meta ad / letter) → lead
submits form / replies → **then** we run Solar on their specific
address. This module implements that last step.

Why deferred-qualification works for B2C:
  * We don't have civic addresses for random CAPs (no ISTAT street
    data), so running Solar at scan time would mean guessing a
    coordinate inside the CAP and getting a *random* roof — which is
    useless.
  * Solar-per-lead is cheap (~€0.03) when the lead has raised its
    hand. Solar-per-CAP-household is prohibitive when 95% of those
    households will never reply.
  * Qualifying only engaged leads aligns the API spend with the
    commercial outcome.

Called from the Tracking / Replies agents when a B2C lead signals
positive intent. Also reachable via an explicit API endpoint for
admin-triggered retries.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import geohash

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..models.enums import RoofDataSource, RoofStatus
from .google_solar_service import (
    SolarApiError,
    SolarApiNotFound,
    fetch_building_insight,
)
from .mapbox_service import MapboxError, forward_geocode

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Address extraction
# ---------------------------------------------------------------------------


def _address_from_lead(lead: dict[str, Any]) -> str | None:
    """Pull whatever address hint we have out of the inbound payload.

    Meta's lead form field names come through as a list of
    ``{"name": "street_address", "values": ["Via Roma 12"]}`` objects
    in the Graph API response stored under
    ``inbound_payload.graph_fields``. We also accept flat string
    fields for tests and for leads created via the manual API.
    """
    payload = lead.get("inbound_payload") or {}

    # Flat fields (set by the API or tests)
    flat = payload.get("full_address") or payload.get("street_address")
    city = payload.get("city") or payload.get("comune")
    cap = payload.get("postcode") or payload.get("cap")
    if flat:
        tail = ", ".join(p for p in (cap, city, "Italia") if p)
        return f"{flat}, {tail}" if tail else str(flat)

    # Meta Graph API structured fields
    graph_fields = payload.get("graph_fields") or []
    if isinstance(graph_fields, list):
        named = {
            f.get("name"): (f.get("values") or [""])[0]
            for f in graph_fields
            if isinstance(f, dict)
        }
        street = named.get("street_address") or named.get("indirizzo")
        city = city or named.get("city") or named.get("comune")
        cap = cap or named.get("postcode") or named.get("cap")
        if street:
            tail = ", ".join(p for p in (cap, city, "Italia") if p)
            return f"{street}, {tail}" if tail else street

    # Worst case: no street. Return the CAP centroid address — Solar
    # will still find *a* building there, usually the dominant one in
    # the zone. Quality is much lower but we flag it on the roof row.
    if cap:
        parts = [f"CAP {cap}", city or "", "Italia"]
        return ", ".join(p for p in parts if p)

    return None


def _subject_from_lead(
    lead: dict[str, Any], roof_id: UUID, tenant_id: str
) -> dict[str, Any]:
    """Build a `subjects` insert row from the Meta lead fields.

    We hash the PII in the caller — `pii_hash` is NOT NULL on the
    table. For B2C leads we hash ``full_name|cap`` since we rarely
    have a full address.
    """
    payload = lead.get("inbound_payload") or {}
    graph_fields = payload.get("graph_fields") or []
    named: dict[str, str] = {}
    if isinstance(graph_fields, list):
        named = {
            str(f.get("name")): str((f.get("values") or [""])[0])
            for f in graph_fields
            if isinstance(f, dict)
        }

    first = payload.get("first_name") or named.get("first_name") or ""
    last = payload.get("last_name") or named.get("last_name") or ""
    if not (first or last):
        full = payload.get("full_name") or named.get("full_name") or ""
        if full:
            parts = full.strip().split(None, 1)
            first = first or parts[0]
            last = last or (parts[1] if len(parts) > 1 else "")

    cap = payload.get("postcode") or payload.get("cap") or named.get("postcode")
    city = payload.get("city") or payload.get("comune") or named.get("city")
    street = (
        payload.get("full_address")
        or payload.get("street_address")
        or named.get("street_address")
    )

    import hashlib

    hash_src = f"{(first+last).lower()}|{cap or ''}".encode("utf-8")
    pii_hash = hashlib.sha256(hash_src).hexdigest()

    return {
        "tenant_id": tenant_id,
        "roof_id": str(roof_id),
        "type": "b2c",
        "owner_first_name": first or None,
        "owner_last_name": last or None,
        "postal_address_line1": street,
        "postal_cap": cap,
        "postal_city": city,
        "pii_hash": pii_hash,
        "data_sources": [{"source": "meta_lead_ads"}],
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def qualify_b2c_lead(
    *, tenant_id: str, lead_id: str
) -> dict[str, Any]:
    """Run Solar qualification for a B2C lead.

    Returns a small status dict the arq task surfaces in its result:
    ``{"status": "qualified"|"rejected"|"skipped", "reason": str|None}``.
    Idempotent — if the lead already has a roof_id we short-circuit.
    """
    sb = get_service_client()

    lead_res = (
        sb.table("leads")
        .select(
            "id, tenant_id, source, pipeline_status, roof_id, "
            "subject_id, inbound_payload"
        )
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    lead = (lead_res.data or [{}])[0]
    if not lead.get("id"):
        return {"status": "skipped", "reason": "lead_not_found"}

    if lead.get("roof_id"):
        # Already qualified from an earlier engagement — nothing to do.
        return {"status": "skipped", "reason": "already_qualified"}

    source = lead.get("source") or ""
    if not source.startswith("b2c_"):
        return {"status": "skipped", "reason": "non_b2c_source"}

    address = _address_from_lead(lead)
    if not address:
        log.info(
            "b2c_qualify.no_address",
            extra={"lead_id": lead_id, "tenant_id": tenant_id},
        )
        return {"status": "skipped", "reason": "no_address_hint"}

    # ---- Geocode + Solar ------------------------------------------------
    try:
        geo = await forward_geocode(address)
    except MapboxError as exc:
        log.warning(
            "b2c_qualify.geocode_failed",
            extra={"lead_id": lead_id, "err": str(exc)[:200]},
        )
        return {"status": "skipped", "reason": "geocode_error"}

    if geo is None:
        return {"status": "skipped", "reason": "geocode_no_match"}

    try:
        insight = await fetch_building_insight(geo.lat, geo.lng)
    except SolarApiNotFound:
        return {"status": "rejected", "reason": "no_building"}
    except SolarApiError as exc:
        log.warning(
            "b2c_qualify.solar_failed",
            extra={"lead_id": lead_id, "err": str(exc)[:200]},
        )
        return {"status": "skipped", "reason": "solar_error"}

    # Minimum B2C thresholds — lower than B2B. A family roof with
    # ~3 kWp and decent orientation still converts.
    accepted = (
        insight.estimated_kwp >= 3.0
        and insight.dominant_exposure != "N"
    )

    gh = geohash.encode(insight.lat or geo.lat, insight.lng or geo.lng, precision=8)

    roof_row = {
        "tenant_id": tenant_id,
        "lat": insight.lat or geo.lat,
        "lng": insight.lng or geo.lng,
        "geohash": gh,
        "address": geo.address or address,
        "cap": geo.cap,
        "comune": geo.comune,
        "provincia": geo.provincia,
        "area_sqm": insight.area_sqm,
        "estimated_kwp": insight.estimated_kwp,
        "estimated_yearly_kwh": insight.estimated_yearly_kwh,
        "exposure": insight.dominant_exposure,
        "pitch_degrees": insight.pitch_degrees,
        "shading_score": insight.shading_score,
        "data_source": RoofDataSource.GOOGLE_SOLAR.value,
        "classification": "b2c",
        "status": (
            RoofStatus.DISCOVERED if accepted else RoofStatus.REJECTED
        ).value,
        "raw_data": {
            "solar": insight.raw,
            "b2c_post_engagement": {
                "lead_id": lead_id,
                "trigger_source": source,
                "geocode_relevance": geo.relevance,
            },
        },
    }

    try:
        up = (
            sb.table("roofs")
            .upsert(roof_row, on_conflict="tenant_id,geohash")
            .execute()
        )
        roof_id = up.data[0]["id"] if up.data else None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "b2c_qualify.roof_upsert_failed",
            extra={"lead_id": lead_id, "err": str(exc)[:200]},
        )
        return {"status": "skipped", "reason": "roof_upsert_error"}

    if not roof_id:
        return {"status": "skipped", "reason": "roof_upsert_no_id"}

    # Subject
    try:
        existing_subj = (
            sb.table("subjects")
            .select("id")
            .eq("tenant_id", tenant_id)
            .eq("roof_id", roof_id)
            .limit(1)
            .execute()
        )
        if existing_subj.data:
            subject_id = existing_subj.data[0]["id"]
        else:
            subj_row = _subject_from_lead(lead, UUID(str(roof_id)), tenant_id)
            ins = sb.table("subjects").insert(subj_row).execute()
            subject_id = ins.data[0]["id"] if ins.data else None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "b2c_qualify.subject_upsert_failed",
            extra={"lead_id": lead_id, "err": str(exc)[:200]},
        )
        return {"status": "skipped", "reason": "subject_upsert_error"}

    # Attach roof+subject to the lead. We also retag the source so
    # analytics can tell Meta-form leads apart from leads that were
    # promoted from a reply / WhatsApp engagement — the former keeps
    # `b2c_meta_ads`, the latter becomes `b2c_post_engagement`.
    new_source = (
        "b2c_post_engagement" if source != "b2c_meta_ads" else source
    )
    try:
        sb.table("leads").update(
            {
                "roof_id": roof_id,
                "subject_id": subject_id,
                "pipeline_status": "qualified"
                if accepted
                else lead.get("pipeline_status"),
                "source": new_source,
            }
        ).eq("id", lead_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "b2c_qualify.lead_update_failed",
            extra={"lead_id": lead_id, "err": str(exc)[:200]},
        )
        return {"status": "skipped", "reason": "lead_update_error"}

    # Bump the source audience counter (if any) so the dashboard
    # funnel shows qualified_roofs ticking up as replies come in.
    audience_id = (lead.get("inbound_payload") or {}).get("audience_id")
    if audience_id and accepted:
        try:
            sb.rpc(
                "b2c_audiences_inc",
                {
                    "_audience_id": audience_id,
                    "_field": "qualified_roofs",
                    "_delta": 1,
                },
            ).execute()
        except Exception:  # noqa: BLE001
            # The RPC is optional — if it's not installed on this
            # environment we just skip the counter bump.
            pass

    log.info(
        "b2c_qualify.complete",
        extra={
            "lead_id": lead_id,
            "tenant_id": tenant_id,
            "accepted": accepted,
            "kwp": float(insight.estimated_kwp or 0),
            "exposure": insight.dominant_exposure,
        },
    )
    return {
        "status": "qualified" if accepted else "rejected",
        "reason": None,
        "roof_id": str(roof_id),
        "estimated_kwp": float(insight.estimated_kwp or 0),
    }
