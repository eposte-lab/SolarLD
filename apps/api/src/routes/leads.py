"""Lead endpoints — primary dashboard surface."""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.logging import get_logger
from ..core.queue import enqueue, fire_crm_event
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..models.lead import LeadFeedback, LeadListResponse
from ..services.audit_service import log_action as audit_log
from ..services.moderation_service import apply_released_filter, assert_lead_visible
from ..services.savings_compare_service import compute_epc_annual, compute_savings_compare
from ..services.storage_service import sign_url, upload_bytes

# Bucket holding all per-lead render assets ({tenant}/{lead}/*.png). Private in
# prod — every URL is a freshly minted, short-lived signed URL.
RENDERINGS_BUCKET = "renderings"

# Engagement floor (mirror of the dashboard's engagementTier 'warm'=tiepido) for
# the on-demand Solar-layout overlay: only generate for leads that have warmed
# up, so the one-time base-imagery fetch is bounded to genuinely interesting
# leads.
_SOLAR_LAYOUT_MIN_ENGAGEMENT = 25

log = get_logger(__name__)

router = APIRouter()

# Tetto di rigenerazioni manuali del rendering per singolo lead. Ogni
# rigenerazione costa Solar API + panel-paint nano-banana. Alzato a 100
# (di fatto illimitato) per il collaudo go-live: l'operatore deve poter
# iterare sul centraggio/zoom degli hero lead senza sbattere sul cap.
# Il tetto resta solo come guardia contro click runaway. Deve combaciare
# con MAX_REGEN in RegenerateRenderingButton.tsx.
MAX_RENDERING_REGENERATIONS = 100

# Columns exported to CSV — flat, CRM-friendly shape. Nested
# subject/roof fields are lifted to the top level with a prefix so
# Excel and Salesforce/HubSpot importers map them cleanly.
_CSV_COLUMNS: list[str] = [
    "lead_id",
    "public_slug",
    "pipeline_status",
    "score",
    "score_tier",
    "feedback",
    "contract_value_eur",
    "created_at",
    "outreach_channel",
    "outreach_sent_at",
    "outreach_delivered_at",
    "outreach_opened_at",
    "outreach_clicked_at",
    "dashboard_visited_at",
    "whatsapp_initiated_at",
    "subject_type",
    "business_name",
    "owner_first_name",
    "owner_last_name",
    "decision_maker_email",
    "decision_maker_email_verified",
    "ateco_code",
    "partita_iva",
    "roof_address",
    "roof_comune",
    "roof_provincia",
    "roof_cap",
    "roof_area_sqm",
    "estimated_kwp",
    "estimated_yearly_kwh",
]


def _flatten(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten a lead row (with nested subject/roof objects) for CSV."""
    subj = row.get("subjects") or {}
    roof = row.get("roofs") or {}
    if isinstance(subj, list):
        subj = subj[0] if subj else {}
    if isinstance(roof, list):
        roof = roof[0] if roof else {}

    contract_cents = row.get("contract_value_cents")
    return {
        "lead_id": row.get("id"),
        "public_slug": row.get("public_slug"),
        "pipeline_status": row.get("pipeline_status"),
        "score": row.get("score"),
        "score_tier": row.get("score_tier"),
        "feedback": row.get("feedback"),
        "contract_value_eur": (f"{contract_cents / 100:.2f}" if contract_cents else ""),
        "created_at": row.get("created_at"),
        "outreach_channel": row.get("outreach_channel"),
        "outreach_sent_at": row.get("outreach_sent_at"),
        "outreach_delivered_at": row.get("outreach_delivered_at"),
        "outreach_opened_at": row.get("outreach_opened_at"),
        "outreach_clicked_at": row.get("outreach_clicked_at"),
        "dashboard_visited_at": row.get("dashboard_visited_at"),
        "whatsapp_initiated_at": row.get("whatsapp_initiated_at"),
        "subject_type": subj.get("type"),
        "business_name": subj.get("business_name"),
        "owner_first_name": subj.get("owner_first_name"),
        "owner_last_name": subj.get("owner_last_name"),
        "decision_maker_email": subj.get("decision_maker_email"),
        "decision_maker_email_verified": subj.get("decision_maker_email_verified"),
        "ateco_code": subj.get("ateco_code"),
        "partita_iva": subj.get("partita_iva"),
        "roof_address": roof.get("address"),
        "roof_comune": roof.get("comune"),
        "roof_provincia": roof.get("provincia"),
        "roof_cap": roof.get("cap"),
        "roof_area_sqm": roof.get("area_sqm"),
        "estimated_kwp": roof.get("estimated_kwp"),
        "estimated_yearly_kwh": roof.get("estimated_yearly_kwh"),
    }


@router.get("", response_model=LeadListResponse)
async def list_leads(
    ctx: CurrentUser,
    status: str | None = Query(default=None),
    tier: Literal["hot", "warm", "cold", "rejected"] | None = Query(default=None),
    channel: Literal["email", "postal"] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=200),
    # ─── Follow-up filter slice (used by /leads/follow-up panel) ─────
    # When the operator picks "Filtri" in the bulk follow-up selector
    # we want a single endpoint that mirrors the cron's eligibility
    # logic: score floor, engagement floor, age-of-outreach floor,
    # multiple pipeline statuses simultaneously.
    score_min: int | None = Query(default=None, ge=0, le=100),
    engagement_min: int | None = Query(default=None, ge=0, le=100),
    days_since_outreach_min: int | None = Query(default=None, ge=0, le=365),
    pipeline_status_in: str | None = Query(
        default=None,
        description="CSV of pipeline_status values (e.g. 'sent,clicked,engaged'). "
        "Use INSTEAD of `status=` when you need an OR across multiple states.",
    ),
) -> dict[str, object]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    query = sb.table("leads").select("*", count="exact").eq("tenant_id", tenant_id)
    # Moderation gate: hide un-released leads from a moderated trial tenant
    # (service-role bypasses the RLS leads_select policy — re-impose it here).
    query = apply_released_filter(query, sb, tenant_id)
    if status:
        query = query.eq("pipeline_status", status)
    if tier:
        query = query.eq("score_tier", tier)
    if channel:
        query = query.eq("outreach_channel", channel)

    # Follow-up selector filters — applied additively. All optional.
    if score_min is not None:
        query = query.gte("score", score_min)
    if engagement_min is not None:
        query = query.gte("engagement_score", engagement_min)
    if days_since_outreach_min is not None:
        # `outreach_sent_at <= now() - N days` AND outreach_sent_at NOT NULL
        from datetime import timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=days_since_outreach_min)).isoformat()
        query = query.not_.is_("outreach_sent_at", "null").lte("outreach_sent_at", cutoff)
    if pipeline_status_in:
        statuses = [s.strip() for s in pipeline_status_in.split(",") if s.strip()]
        if statuses:
            query = query.in_("pipeline_status", statuses)

    offset = (page - 1) * per_page
    res = query.order("score", desc=True).range(offset, offset + per_page - 1).execute()

    return {
        "data": res.data or [],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": res.count or 0,
        },
    }


@router.get("/hot")
async def list_hot_leads(
    ctx: CurrentUser,
    since_hours: int = Query(default=72, ge=1, le=720),
    min_score: int = Query(default=60, ge=0, le=100),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, object]:
    """Real-time "Caldi adesso" — leads who showed engagement recently.

    Sprint 8 Fase C.2.

    Returns leads ordered by ``engagement_score DESC, last_portal_event_at
    DESC`` filtered by:
      * tenant_id (RLS via service client + explicit eq)
      * ``engagement_score >= min_score``
      * ``last_portal_event_at >= now() - since_hours``
      * pipeline NOT IN closed/responded states (engaged, appointment,
        whatsapp, closed_won, closed_lost, blacklisted) — the operator
        wants the ones who are *interested but haven't replied*.

    The dashboard "Caldi adesso" tab calls this on poll (no realtime
    sub on this endpoint — the bump function on /portal/track gives a
    sub-second feedback loop already; the operator just refreshes).
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Cut-off: only consider leads who fired a portal event within
    # the window. Without the cut-off, an old lead with score=80
    # from a month ago would camp the top of the list forever.
    from datetime import timedelta

    cutoff = (datetime.now(UTC) - timedelta(hours=since_hours)).isoformat()

    # Pipeline statuses that mean the lead has *already* moved past
    # the "needs follow-up" point. We want the gap between
    # high-engagement and not-yet-responded.
    excluded_statuses = (
        "engaged",
        "whatsapp",
        "appointment",
        "closed_won",
        "closed_lost",
        "blacklisted",
    )

    query = (
        sb.table("leads")
        .select(
            "id, public_slug, score, score_tier, engagement_score, "
            "engagement_score_updated_at, last_portal_event_at, "
            "pipeline_status, outreach_sent_at, "
            "subjects(type, business_name, owner_first_name, "
            "owner_last_name, decision_maker_email), "
            "roofs(address, comune, provincia, estimated_kwp)"
        )
        .eq("tenant_id", tenant_id)
        .gte("engagement_score", min_score)
        .gte("last_portal_event_at", cutoff)
        # ``not_.in_`` is the supabase-py shape for NOT IN.
        .not_.in_("pipeline_status", list(excluded_statuses))
        .order("engagement_score", desc=True)
        .order("last_portal_event_at", desc=True)
        .limit(limit)
    )
    # Moderation gate — hide un-released leads from a moderated trial tenant.
    query = apply_released_filter(query, sb, tenant_id)
    res = query.execute()
    return {
        "data": res.data or [],
        "filters": {
            "since_hours": since_hours,
            "min_score": min_score,
            "limit": limit,
        },
    }


@router.get("/export.csv")
async def export_leads_csv(
    ctx: CurrentUser,
    status: str | None = Query(default=None),
    tier: Literal["hot", "warm", "cold", "rejected"] | None = Query(default=None),
    channel: Literal["email", "postal"] | None = Query(default=None),
    feedback: str | None = Query(default=None),
    max_rows: int = Query(default=5000, ge=1, le=50000),
) -> StreamingResponse:
    """Export the caller's leads as CSV — data egress for CRM imports.

    Filters mirror ``GET /v1/leads`` so the operator can narrow to
    "HOT leads signed this quarter" before dropping the file into
    Salesforce / HubSpot. The response streams row-by-row to avoid
    buffering the whole export in memory on large tenants.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    query = (
        sb.table("leads")
        .select(
            "id, public_slug, pipeline_status, score, score_tier, feedback, "
            "contract_value_cents, created_at, outreach_channel, "
            "outreach_sent_at, outreach_delivered_at, outreach_opened_at, "
            "outreach_clicked_at, dashboard_visited_at, whatsapp_initiated_at, "
            "subjects(type, business_name, owner_first_name, owner_last_name, "
            "decision_maker_email, decision_maker_email_verified, ateco_code, "
            "partita_iva), "
            "roofs(address, comune, provincia, cap, area_sqm, estimated_kwp, "
            "estimated_yearly_kwh)"
        )
        .eq("tenant_id", tenant_id)
    )
    # Moderation gate — never export un-released leads of a moderated tenant.
    query = apply_released_filter(query, sb, tenant_id)
    if status:
        query = query.eq("pipeline_status", status)
    if tier:
        query = query.eq("score_tier", tier)
    if channel:
        query = query.eq("outreach_channel", channel)
    if feedback:
        query = query.eq("feedback", feedback)

    res = query.order("score", desc=True).limit(max_rows).execute()
    rows = res.data or []

    def _generate() -> Any:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        yield buf.getvalue()
        for row in rows:
            buf.seek(0)
            buf.truncate(0)
            writer.writerow(_flatten(row))
            yield buf.getvalue()

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"solarlead-leads-{stamp}.csv"
    return StreamingResponse(
        _generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{lead_id}")
async def get_lead(ctx: CurrentUser, lead_id: str) -> dict[str, object]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    query = (
        sb.table("leads")
        .select("*, subjects(*), roofs(*), campaigns(*)")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    # Moderation gate — a hidden lead 404s, indistinguishable from absent.
    query = apply_released_filter(query, sb, tenant_id)
    res = query.execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    return res.data[0]


def _renderings_object_exists(sb: Any, folder: str, name: str) -> bool:
    """Whether ``{folder}/{name}`` already exists in the renderings bucket.

    Best-effort: any storage error → treat as a miss (we regenerate rather
    than fail). Used to skip re-rendering an already-cached solar layout.
    """
    try:
        listing = sb.storage.from_(RENDERINGS_BUCKET).list(folder) or []
        return any((obj.get("name") == name) for obj in listing)
    except Exception as exc:  # noqa: BLE001
        log.warning("leads.renderings_list_failed", folder=folder, err=str(exc)[:200])
        return False


@router.get("/{lead_id}/solar-layout")
async def lead_solar_layout(ctx: CurrentUser, lead_id: str) -> dict[str, object]:
    """On-demand "real Google Solar panel layout" overlay for a warm/hot lead.

    Draws the PV panels at the EXACT Google Solar API positions (deterministic
    PIL, NO AI) on the building's aerial — distinct from the marketing AI
    render — so the operator can sanity-check that the proposed array actually
    fits the roof (vs an inflated "paper" layout). Built from the already-stored
    ``roofs.raw_data`` panel geometry, so there is NO AI/Replicate cost; the
    final PNG is cached in the renderings bucket, so only the FIRST open per
    lead pays the single base-imagery fetch and every later open is free.

    Returns a short-lived signed URL (the bucket is private). Gated on
    engagement ≥ "tiepido" to bound that one-time generation cost.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    query = (
        sb.table("leads")
        .select("id, engagement_score, roofs(lat, lng, raw_data)")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    query = apply_released_filter(query, sb, tenant_id)
    res = query.execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead non trovato.")
    lead = res.data[0]

    # Bound the one-time base-imagery cost: mirror the dashboard's tiepido+ gate.
    if int(lead.get("engagement_score") or 0) < _SOLAR_LAYOUT_MIN_ENGAGEMENT:
        raise HTTPException(
            status_code=403,
            detail="Layout disponibile solo per lead con engagement sufficiente.",
        )

    roof = lead.get("roofs") or {}
    if isinstance(roof, list):  # PostgREST embeds to-one as object, types widen to list
        roof = roof[0] if roof else {}
    lat, lng = roof.get("lat"), roof.get("lng")
    raw = roof.get("raw_data") or {}
    # Production writers nest the Google buildingInsights JSON under 'solar';
    # the legacy writer stored it bare. Try the nested key first.
    payload = raw.get("solar") or raw
    if not isinstance(payload, dict) or lat is None or lng is None:
        raise HTTPException(status_code=404, detail="Dati Solar non disponibili per questo lead.")

    path = f"{tenant_id}/{lead_id}/solar_layout.png"

    # Cache hit → just mint a fresh signed URL (no regeneration).
    if _renderings_object_exists(sb, f"{tenant_id}/{lead_id}", "solar_layout.png"):
        return {"url": sign_url(RENDERINGS_BUCKET, path, 3600), "cached": True}

    # Cache miss → render the deterministic overlay from stored geometry.
    from ..services.google_solar_service import _parse_building_insight_payload
    from ..services.solar_rendering_service import render_before_after

    insight = _parse_building_insight_payload(payload)
    if not insight.panels:
        raise HTTPException(
            status_code=404, detail="Nessun pannello nel layout Solar per questo lead."
        )
    try:
        _before_bytes, after_bytes = await render_before_after(
            float(lat),
            float(lng),
            insight,
            api_key=settings.google_solar_api_key or None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("leads.solar_layout_render_failed", lead_id=lead_id, err=str(exc)[:200])
        raise HTTPException(status_code=502, detail="Generazione layout non riuscita.") from exc

    # Cache for free repeat opens (private bucket → ignore the public URL).
    try:
        upload_bytes(
            bucket=RENDERINGS_BUCKET, path=path, data=after_bytes, content_type="image/png"
        )
    except Exception as exc:  # noqa: BLE001 — caching is best-effort
        log.warning("leads.solar_layout_cache_failed", lead_id=lead_id, err=str(exc)[:200])

    return {"url": sign_url(RENDERINGS_BUCKET, path, 3600), "cached": False}


@router.get("/{lead_id}/timeline")
async def lead_timeline(ctx: CurrentUser, lead_id: str) -> list[dict[str, object]]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    # Moderation gate — a hidden lead's timeline 404s for a moderated tenant.
    assert_lead_visible(sb, tenant_id, lead_id)
    res = (
        sb.table("events")
        .select("*")
        .eq("lead_id", lead_id)
        .eq("tenant_id", tenant_id)
        .order("occurred_at", desc=True)
        .limit(200)
        .execute()
    )
    return res.data or []


@router.get("/{lead_id}/bolletta")
async def lead_bolletta(ctx: CurrentUser, lead_id: str) -> dict[str, Any]:
    """Bolletta caricata dal lead — file (signed URL) + risparmio EPC annuo.

    Sorgente per la BollettaCard della scheda lead. Riusa la stessa
    pipeline del dossier (``compute_savings_compare`` + ``compute_epc_annual``)
    così i numeri coincidono con quelli che vede il cliente. La signed URL
    del bucket privato ``bollette`` scade in 1h: l'accesso resta dietro
    ``require_tenant``. Sola lettura: nessuna modifica/eliminazione.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    lead_q = (
        sb.table("leads")
        .select("id, tenant_id, roi_data, subjects(type)")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    # Moderation gate — hidden lead 404s for a moderated tenant.
    lead_q = apply_released_filter(lead_q, sb, tenant_id)
    lead_res = lead_q.execute()
    if not lead_res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = lead_res.data[0]

    bill_res = (
        sb.table("bolletta_uploads")
        .select(
            "id, storage_path, mime_type, ocr_kwh_yearly, ocr_eur_yearly, "
            "manual_kwh_yearly, manual_eur_yearly, source, uploaded_at"
        )
        .eq("lead_id", lead_id)
        .order("uploaded_at", desc=True)
        .limit(1)
        .execute()
    )
    if not bill_res.data:
        return {"available": False, "reason": "no_bolletta_uploaded"}
    bill = bill_res.data[0]

    # Valori manuali (correzione utente) prevalgono sull'OCR.
    kwh = bill.get("manual_kwh_yearly") or bill.get("ocr_kwh_yearly")
    eur = bill.get("manual_eur_yearly") or bill.get("ocr_eur_yearly")

    signed_url: str | None = None
    storage_path = bill.get("storage_path")
    if storage_path:
        try:
            signed_url = sign_url("bollette", storage_path, 3600)
        except Exception as exc:  # noqa: BLE001 — file mancante non blocca i numeri
            log.warning("bolletta.sign_url_failed", lead_id=lead_id, err=str(exc)[:200])

    mime = (bill.get("mime_type") or "").lower()
    file_kind = "pdf" if "pdf" in mime else ("image" if mime.startswith("image/") else "file")

    epc: dict[str, float] | None = None
    if kwh and eur:
        result = compute_savings_compare(
            roi_data=lead.get("roi_data"),
            bolletta_kwh_yearly=float(kwh),
            bolletta_eur_yearly=float(eur),
            subject_type=(lead.get("subjects") or {}).get("type") or "unknown",
        )
        if result is not None:
            epc = compute_epc_annual(result)

    return {
        "available": True,
        "signed_url": signed_url,
        "file_kind": file_kind,
        "source": bill.get("source"),
        "uploaded_at": bill.get("uploaded_at"),
        "bill": {
            "kwh": round(float(kwh)) if kwh else None,
            "eur": round(float(eur)) if eur else None,
        },
        "epc": epc,
    }


@router.patch("/{lead_id}/feedback")
async def set_feedback(
    ctx: CurrentUser,
    lead_id: str,
    payload: LeadFeedback,
) -> dict[str, object]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    # Moderation gate — cannot act on a lead hidden from a moderated tenant.
    assert_lead_visible(sb, tenant_id, lead_id)
    update = {
        "feedback": payload.feedback.value,
        "feedback_notes": payload.notes,
        "feedback_at": "now()",
    }
    if payload.contract_value_eur is not None:
        update["contract_value_cents"] = int(payload.contract_value_eur * 100)
    res = sb.table("leads").update(update).eq("id", lead_id).eq("tenant_id", tenant_id).execute()

    # Fan out contract_signed to any registered CRM endpoints AND drop
    # an in-app notification — this is the single most important
    # lifecycle event, so we dispatch straight from the route instead
    # of waiting for an agent to emit it.
    if payload.feedback.value == "contract_signed":
        try:
            await fire_crm_event(
                tenant_id=tenant_id,
                event_type="lead.contract_signed",
                data={
                    "lead_id": lead_id,
                    "contract_value_cents": update.get("contract_value_cents"),
                    "notes": payload.notes,
                },
            )
        except Exception:  # noqa: BLE001
            # Never fail the feedback write because of webhook plumbing.
            pass

        try:
            from ..services.notifications_service import notify

            contract_eur = (
                f"€{(update['contract_value_cents'] or 0) / 100:,.0f}"
                if update.get("contract_value_cents")
                else None
            )
            await notify(
                tenant_id=tenant_id,
                title="Contratto firmato",
                body=(
                    f"Nuovo contratto da {contract_eur}"
                    if contract_eur
                    else "Un lead ha firmato il contratto."
                ),
                severity="success",
                href=f"/leads/{lead_id}",
                metadata={"lead_id": lead_id},
            )
        except Exception:  # noqa: BLE001
            pass

    await audit_log(
        tenant_id,
        "lead.feedback_updated",
        actor_user_id=ctx.sub,
        target_table="leads",
        target_id=lead_id,
        diff={
            "feedback": payload.feedback.value,
            "notes": payload.notes,
            "contract_value_eur": payload.contract_value_eur,
        },
    )
    return {"ok": True, "data": res.data}


@router.delete("/{lead_id}", status_code=204)
async def delete_lead(
    ctx: CurrentUser,
    lead_id: str,
) -> Response:
    """Hard-delete a lead and all cascaded rows (GDPR right to be forgotten).

    The schema has ON DELETE CASCADE on every child table
    (subjects, roofs, campaigns, events, portal_events, conversions,
    crm_webhook_deliveries), so deleting the ``leads`` row is sufficient
    to erase all PII associated with this lead.

    The action is recorded in ``audit_log`` before deletion so the
    operator can prove compliance even after the row is gone.
    Irreversible — confirm in the dashboard before calling.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Verify ownership before deleting (+ moderation gate: a hidden lead
    # 404s, so a moderated tenant cannot delete what it cannot see).
    res_q = (
        sb.table("leads")
        .select("id, subjects(owner_first_name, owner_last_name, business_name)")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    res_q = apply_released_filter(res_q, sb, tenant_id)
    res = res_q.execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead_row = res.data[0]
    subj = lead_row.get("subjects") or {}
    if isinstance(subj, list):
        subj = subj[0] if subj else {}

    # Audit BEFORE deletion so the row still exists during the write
    await audit_log(
        tenant_id,
        "lead.deleted",
        actor_user_id=ctx.sub,
        target_table="leads",
        target_id=lead_id,
        diff={
            "business_name": subj.get("business_name"),
            "owner": " ".join(
                filter(
                    None,
                    [subj.get("owner_first_name"), subj.get("owner_last_name")],
                )
            )
            or None,
            "reason": "gdpr_erasure",
        },
    )

    sb.table("leads").delete().eq("id", lead_id).execute()
    return Response(status_code=204)


@router.post("/{lead_id}/regenerate-rendering")
async def regen_rendering(
    ctx: CurrentUser,
    lead_id: str,
    force: bool = Query(
        default=True,
        description=(
            "When true (default), re-render even if a previous run already "
            "produced an after-image. Pass force=false to make the job a "
            "no-op when rendering_image_url is already set."
        ),
    ),
) -> dict[str, object]:
    """Enqueue a Creative-Agent re-run for this lead.

    Used by the dashboard after a tenant updates their brand colour,
    after a roof's geometry is corrected, or when the tenant wants to
    retry a prior failed rendering.

    The job_id embeds a millisecond timestamp (``creative:…:{ms}``).
    arq deduplicates by job_id and keeps each result in Redis for
    ``keep_result`` (1h): any id reused inside that window is silently
    dropped. A counter-based id (``:r{n}``) breaks the moment the
    counter is ever reset — the reused ``:r1`` collides with the
    cached one — so every regeneration becomes a no-op. A timestamp is
    monotonic and never reused, so each click is always a fresh job.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res_q = (
        sb.table("leads")
        .select("id, rendering_regen_count")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    # Moderation gate — hidden lead 404s for a moderated tenant.
    res_q = apply_released_filter(res_q, sb, tenant_id)
    res = res_q.execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Ogni rigenerazione costa (Solar API + inpainting): limite di
    # MAX_RENDERING_REGENERATIONS per lead.
    regen_count = int(res.data[0].get("rendering_regen_count") or 0)
    if regen_count >= MAX_RENDERING_REGENERATIONS:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Limite di rigenerazioni del rendering raggiunto per "
                f"questo lead ({MAX_RENDERING_REGENERATIONS})."
            ),
        )

    new_count = regen_count + 1
    sb.table("leads").update({"rendering_regen_count": new_count}).eq("id", lead_id).execute()

    # Millisecond timestamp → a job_id that is never reused, so arq's
    # 1h result cache can never deduplicate a genuine regeneration.
    job_ms = int(datetime.now(UTC).timestamp() * 1000)
    job = await enqueue(
        "creative_task",
        {"tenant_id": tenant_id, "lead_id": lead_id, "force": force},
        job_id=f"creative:{tenant_id}:{lead_id}:{job_ms}",
    )
    return {
        "ok": True,
        "lead_id": lead_id,
        "regen_count": new_count,
        "regen_remaining": MAX_RENDERING_REGENERATIONS - new_count,
        **job,
    }


@router.post("/{lead_id}/rescore")
async def rescore_lead(ctx: CurrentUser, lead_id: str) -> dict[str, object]:
    """Re-run the Scoring Agent for a single lead.

    Useful after the tenant tweaks HQ coords, after a new ATECO profile is
    added, or when regional incentives are refreshed by the weekly scraper.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res_q = (
        sb.table("leads")
        .select("id, roof_id, subject_id")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    # Moderation gate — hidden lead 404s for a moderated tenant.
    res_q = apply_released_filter(res_q, sb, tenant_id)
    res = res_q.execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = res.data[0]
    job = await enqueue(
        "scoring_task",
        {
            "tenant_id": tenant_id,
            "roof_id": lead["roof_id"],
            "subject_id": lead["subject_id"],
        },
        job_id=f"scoring:{tenant_id}:{lead['roof_id']}:{lead['subject_id']}",
    )
    return {"ok": True, "lead_id": lead_id, **job}


@router.post("/{lead_id}/send-outreach")
async def send_outreach(
    ctx: CurrentUser,
    lead_id: str,
    channel: Literal["email", "postal"] = Query(default="email"),
    force: bool = Query(
        default=False,
        description=(
            "Re-send the outreach even if outreach_sent_at is already set. "
            "Default false so accidental double-clicks collapse into one job."
        ),
    ),
) -> dict[str, object]:
    """Enqueue an Outreach-Agent run for one lead (Sprint 6: email only).

    Idempotent per lead+channel via the deterministic job_id, so
    double-clicks on the dashboard collapse to a single worker run.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res_q = sb.table("leads").select("id").eq("id", lead_id).eq("tenant_id", tenant_id).limit(1)
    # Moderation gate — hidden lead 404s for a moderated tenant.
    res_q = apply_released_filter(res_q, sb, tenant_id)
    res = res_q.execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    job = await enqueue(
        "outreach_task",
        {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "channel": channel,
            "force": force,
        },
        job_id=f"outreach:{tenant_id}:{lead_id}:{channel}",
    )
    return {"ok": True, "lead_id": lead_id, **job}


# Demo-mode test send. Only enabled when the tenant has the kill-switch
# flipped on (tenants.outreach_blocked=true). Lets the operator type
# their own email address as the recipient so they can verify the
# rendering + send pipeline end-to-end without ever touching the real
# prospect's inbox. The OutreachAgent honours `recipient_override`,
# bypasses the kill-switch, and routes the email to the operator.
_EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SendTestOutreachRequest(BaseModel):
    recipient_override: str = Field(
        ...,
        min_length=5,
        max_length=320,
        description="Operator's own email — receives the test send.",
    )


@router.post("/{lead_id}/send-test-outreach")
async def send_test_outreach(
    ctx: CurrentUser,
    lead_id: str,
    body: SendTestOutreachRequest,
) -> dict[str, object]:
    """Demo-mode: send the outreach email to an operator-supplied address.

    Returns 403 unless the tenant has `outreach_blocked=true`. This
    guards production accounts — only demo tenants can use the override
    flow. The operator's address is also rejected when it matches the
    lead's actual decision_maker_email (prevents accidentally hitting
    the real prospect via the test endpoint).
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    override = (body.recipient_override or "").strip().lower()
    if not _EMAIL_RX.match(override):
        raise HTTPException(status_code=400, detail="Email non valida.")

    # Tenant must be in demo / kill-switch mode for this path to make sense.
    tenant_res = (
        sb.table("tenants").select("id, outreach_blocked").eq("id", tenant_id).single().execute()
    )
    if not tenant_res.data:
        raise HTTPException(status_code=404, detail="Tenant non trovato.")
    if not tenant_res.data.get("outreach_blocked"):
        raise HTTPException(
            status_code=403,
            detail=(
                "Test send disponibile solo per gli account demo "
                "(outreach_blocked=true). Per i tenant in produzione usa "
                "il bottone 'Invia email' standard."
            ),
        )

    # Resolve the lead + its real recipient so we can refuse a self-aimed
    # override (i.e. the operator typed the prospect's address).
    lead_q = (
        sb.table("leads")
        .select("id, subjects(decision_maker_email)")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    # Moderation gate — hidden lead 404s for a moderated tenant.
    lead_q = apply_released_filter(lead_q, sb, tenant_id)
    lead_res = lead_q.execute()
    if not lead_res.data:
        raise HTTPException(status_code=404, detail="Lead non trovato.")
    real_email = (
        ((lead_res.data[0].get("subjects") or {}).get("decision_maker_email") or "").strip().lower()
    )
    if real_email and real_email == override:
        raise HTTPException(
            status_code=400,
            detail=(
                "L'indirizzo di test coincide con l'email reale del lead. "
                "Inserisci la tua email personale per verificare il flusso."
            ),
        )

    # Each click is its own job: a timestamp suffix keeps the job_id
    # unique so ARQ never deduplicates a fresh test send against a
    # previous one (same lead + same address would otherwise collapse
    # to one job and silently no-op on the 2nd click). Force=true so the
    # kill-switch bypass + override actually run even when
    # outreach_sent_at is set (multiple test runs are fine).
    import hashlib

    override_tag = hashlib.sha256(override.encode()).hexdigest()[:10]
    ts = int(datetime.now(tz=UTC).timestamp())
    job = await enqueue(
        "outreach_task",
        {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "channel": "email",
            "force": True,
            "recipient_override": override,
        },
        job_id=f"outreach_test:{tenant_id}:{lead_id}:{override_tag}:{ts}",
    )
    log.info(
        "leads.send_test_outreach.queued",
        tenant_id=tenant_id,
        lead_id=lead_id,
        override_domain=override.split("@", 1)[1],
    )
    return {"ok": True, "lead_id": lead_id, "recipient_override": override, **job}


class ResendToAddressRequest(BaseModel):
    recipient_override: str
    # Optional custom email subject (oggetto). Empty/None → the standard
    # computed subject. The operator uses this to address a specific person at
    # a shared inbox, e.g. "c.a. Sig. Carlo — casella Hilton Napoli".
    subject_override: str | None = Field(default=None, max_length=300)


@router.post("/{lead_id}/resend-to-address")
async def resend_outreach_to_address(
    ctx: CurrentUser,
    lead_id: str,
    body: ResendToAddressRequest,
) -> dict[str, object]:
    """Production: resend a lead's EXACT official outreach to an alternate address.

    Use case: the decision-maker asked for the same offer at a different email
    (address change, additional contact). Sends the identical official
    template + plant data + rendering — the same machinery as the daily
    outreach, just a recipient override.

    Unlike ``send-test-outreach`` (demo tenants only) this works for production
    tenants, but it is NOT silent: every call writes an ``audit_log`` row
    (operator, lead, address, custom subject) so an alternate-recipient send is
    always traceable — the lead's engagement can never be quietly
    redirected/inflated without a trail. The operator may also set a custom
    email subject (``subject_override``) to address a specific person at a
    shared inbox.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    override = (body.recipient_override or "").strip().lower()
    if not _EMAIL_RX.match(override):
        raise HTTPException(status_code=400, detail="Email non valida.")
    subject_override = (body.subject_override or "").strip() or None

    # Resolve the lead + its real recipient (moderation gate). Refuse a
    # self-aimed override — typing the prospect's own address is just a normal
    # resend, for which the standard 'Invia email' button exists.
    lead_q = (
        sb.table("leads")
        .select("id, subjects(decision_maker_email)")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    lead_q = apply_released_filter(lead_q, sb, tenant_id)
    lead_res = lead_q.execute()
    if not lead_res.data:
        raise HTTPException(status_code=404, detail="Lead non trovato.")
    real_email = (
        ((lead_res.data[0].get("subjects") or {}).get("decision_maker_email") or "").strip().lower()
    )
    if real_email and real_email == override:
        raise HTTPException(
            status_code=400,
            detail=(
                "L'indirizzo coincide con l'email reale del lead — usa il "
                "bottone 'Invia email' standard per inviare al prospect."
            ),
        )

    # Audit FIRST — append-only trail (operator, lead, address, custom subject).
    # Best-effort: log_action never raises.
    await audit_log(
        tenant_id,
        "lead.outreach_resent_alt_address",
        actor_user_id=ctx.sub,
        target_table="leads",
        target_id=lead_id,
        diff={"recipient_override": override, "subject_override": subject_override},
    )

    # Same machinery as the daily outreach (identical template/data/rendering),
    # just a recipient override. Unique job_id per click so ARQ never dedupes a
    # fresh resend against a previous one. force=True bypasses the send-window
    # (deliberate operator action); the blacklist / existing-PV / GDPR gates in
    # OutreachAgent still apply.
    import hashlib

    override_tag = hashlib.sha256(override.encode()).hexdigest()[:10]
    ts = int(datetime.now(tz=UTC).timestamp())
    job = await enqueue(
        "outreach_task",
        {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "channel": "email",
            "force": True,
            "recipient_override": override,
            "subject_override": subject_override,
        },
        job_id=f"outreach_resend:{tenant_id}:{lead_id}:{override_tag}:{ts}",
    )
    log.info(
        "leads.resend_to_address.queued",
        tenant_id=tenant_id,
        lead_id=lead_id,
        has_subject_override=bool(subject_override),
        override_domain=override.split("@", 1)[1],
    )
    return {"ok": True, "lead_id": lead_id, "recipient_override": override, **job}


@router.post("/{lead_id}/find-better-contact", status_code=202)
async def find_better_contact(
    ctx: CurrentUser,
    lead_id: str,
) -> dict[str, object]:
    """Re-enrich this lead's contact with the premium decision-maker finder.

    Fire-and-forget: enqueues a background job that looks up a named
    decision-maker email for the company domain (within the capped budget),
    validates it, and updates the lead's contact in place. The operator then
    resends with the standard / resend-to-address flow. Returns 202 + writes an
    audit row; the actual work runs on the worker.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    lead_q = sb.table("leads").select("id").eq("id", lead_id).eq("tenant_id", tenant_id).limit(1)
    lead_q = apply_released_filter(lead_q, sb, tenant_id)
    if not lead_q.execute().data:
        raise HTTPException(status_code=404, detail="Lead non trovato.")

    await audit_log(
        tenant_id,
        "lead.find_better_contact_requested",
        actor_user_id=ctx.sub,
        target_table="leads",
        target_id=lead_id,
    )

    ts = int(datetime.now(tz=UTC).timestamp())
    job = await enqueue(
        "find_better_contact_task",
        {"tenant_id": tenant_id, "lead_id": lead_id},
        job_id=f"find_contact:{tenant_id}:{lead_id}:{ts}",
    )
    log.info("leads.find_better_contact.queued", tenant_id=tenant_id, lead_id=lead_id)
    return {"ok": True, "lead_id": lead_id, "status": "scheduled", **job}


# ---------------------------------------------------------------------------
# Follow-up drafter — Part B.9
# ---------------------------------------------------------------------------
# Two-step flow: generate draft → (user edits in dashboard) → send.
# Draft generation calls Claude with the full lead context so the copy
# is concrete (ROI numbers, first name, engagement signals). The
# send-draft endpoint bypasses OutreachAgent's template system and
# goes straight to Resend + campaigns table, so the sequence log stays
# accurate.


class FollowUpDraftResponse(BaseModel):
    lead_id: str
    subject: str
    body: str


class SendDraftRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=8000)


class SendDraftResponse(BaseModel):
    ok: bool
    campaign_id: str
    message_id: str | None = None


@router.post("/{lead_id}/draft-followup", response_model=FollowUpDraftResponse)
async def draft_followup(
    ctx: CurrentUser,
    lead_id: str,
) -> FollowUpDraftResponse:
    """Generate a personalised follow-up draft using Claude.

    Gathers the full lead context — subject, ROI, engagement signals,
    campaign history, recent events — and feeds it to Claude to produce
    a {subject, body} JSON. The draft is returned as-is; the dashboard
    shows it in an editable textarea before the operator sends it.

    Tier-gated: requires advanced_analytics (Pro+).
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Auth-scope + tier gate
    from ..core.tier import Capability, require_capability
    from ..services.claude_service import complete_json

    lead_q = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, score, score_tier, "
            "outreach_sent_at, outreach_opened_at, outreach_clicked_at, "
            "dashboard_visited_at, whatsapp_initiated_at, "
            "engagement_score, portal_sessions, portal_total_time_sec, "
            "deepest_scroll_pct, feedback, feedback_notes, roi_data, "
            "subjects(type, business_name, owner_first_name, owner_last_name, "
            "decision_maker_email), "
            "roofs(address, comune, provincia, cap, estimated_kwp, "
            "estimated_yearly_kwh, area_sqm)"
        )
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    # Moderation gate — hidden lead 404s for a moderated tenant.
    lead_q = apply_released_filter(lead_q, sb, tenant_id)
    lead_res = lead_q.execute()
    if not lead_res.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead = lead_res.data[0]
    tenant_res = (
        sb.table("tenants")
        .select("tier, settings, business_name, email_from_domain, email_from_name")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    tenant = (tenant_res.data or [{}])[0]

    # Tier gate — advanced_analytics = Pro+
    require_capability(tenant, Capability.ADVANCED_ANALYTICS)

    # Recent campaigns (last 5)
    cmp_res = (
        sb.table("outreach_sends")
        .select("sequence_step, status, sent_at, channel, email_subject, failure_reason")
        .eq("lead_id", lead_id)
        .order("sent_at", desc=True)
        .limit(5)
        .execute()
    )
    campaigns = cmp_res.data or []

    # Recent events (last 10)
    evt_res = (
        sb.table("events")
        .select("event_type, occurred_at")
        .eq("lead_id", lead_id)
        .order("occurred_at", desc=True)
        .limit(10)
        .execute()
    )
    events = evt_res.data or []

    # Build context for Claude
    subj = lead.get("subjects") or {}
    if isinstance(subj, list):
        subj = subj[0] if subj else {}
    roof = lead.get("roofs") or {}
    if isinstance(roof, list):
        roof = roof[0] if roof else {}
    roi = lead.get("roi_data") or {}

    business = subj.get("business_name") or ""
    first_name = subj.get("owner_first_name") or ""
    last_name = subj.get("owner_last_name") or ""
    person = f"{first_name} {last_name}".strip() or "il cliente"
    subject_type = subj.get("type") or "unknown"
    comune = roof.get("comune") or ""
    provincia = roof.get("provincia") or ""
    kwp = roi.get("estimated_kwp") or roof.get("estimated_kwp")
    savings = roi.get("annual_savings_eur")
    payback = roi.get("payback_years")
    co2 = roi.get("co2_saved_kg")

    # Engagement summary
    eng_lines: list[str] = []
    if lead.get("outreach_sent_at"):
        eng_lines.append(f"- Email outreach sent: {lead['outreach_sent_at'][:10]}")
    if lead.get("outreach_opened_at"):
        eng_lines.append(f"- Email opened: {lead['outreach_opened_at'][:10]}")
    if lead.get("outreach_clicked_at"):
        eng_lines.append(f"- CTA clicked: {lead['outreach_clicked_at'][:10]}")
    if lead.get("dashboard_visited_at"):
        total_sec = lead.get("portal_total_time_sec") or 0
        scroll = lead.get("deepest_scroll_pct") or 0
        eng_lines.append(
            f"- Portal visited: {lead['dashboard_visited_at'][:10]} "
            f"(time: {total_sec}s, scroll: {scroll}%)"
        )
    if lead.get("whatsapp_initiated_at"):
        eng_lines.append(f"- WhatsApp CTA clicked: {lead['whatsapp_initiated_at'][:10]}")

    cmp_lines: list[str] = []
    for c in campaigns:
        line = f"  Step {c.get('sequence_step')}: {c.get('channel')} — {c.get('status')}"
        if c.get("email_subject"):
            line += f" (subject: {c['email_subject']})"
        if c.get("sent_at"):
            line += f" on {c['sent_at'][:10]}"
        cmp_lines.append(line)

    evt_lines = [f"  {e['event_type']} at {e['occurred_at'][:16]}" for e in events]

    notes_section = ""
    if lead.get("feedback"):
        notes_section = f"\nOperator feedback: {lead['feedback']}"
        if lead.get("feedback_notes"):
            notes_section += f" — {lead['feedback_notes']}"

    context_block = f"""
Lead type: {subject_type.upper()}
{"Business: " + business if business else ""}
{"Contact person: " + person if person else ""}
{"Location: " + ", ".join(filter(None, [comune, provincia])) if comune or provincia else ""}

ROI data:
{"- Estimated power: " + str(kwp) + " kWp" if kwp else ""}
{"- Annual savings: €" + str(int(savings)) if savings else ""}
{"- Payback period: " + str(payback) + " years" if payback else ""}
{"- CO2 saved: " + str(int(co2)) + " kg/year" if co2 else ""}

Engagement history:
{chr(10).join(eng_lines) if eng_lines else "  (no engagement recorded yet)"}

Campaign sequence:
{chr(10).join(cmp_lines) if cmp_lines else "  (no campaigns yet)"}

Recent events:
{chr(10).join(evt_lines) if evt_lines else "  (no events)"}
{notes_section}
Installer: {tenant.get("business_name", "SolarLead")}
""".strip()

    system = (
        "You are a follow-up email assistant for an Italian solar energy company. "
        "Write concise, warm, and concrete follow-up emails IN ITALIAN. "
        "Always reference specific numbers from the ROI data to make the message personal. "
        "Tone: professional but friendly, never pushy. "
        "Call to action: book a free no-obligation site visit ('sopralluogo gratuito'). "
        "Length: 3-4 short paragraphs. "
        "Subject line: 8-12 words, specific to the lead's situation."
    )
    prompt = (
        f"Write a follow-up email for this lead:\n\n{context_block}\n\n"
        "Return a JSON object with two fields: "
        '"subject" (the email subject line in Italian) and '
        '"body" (the email body in plain text Italian, no HTML, no markdown).'
    )

    schema = '{"subject": "<string>", "body": "<string>"}'
    draft = await complete_json(prompt, schema_hint=schema, system=system, max_tokens=2000)

    subject_line = str(draft.get("subject") or "").strip()
    body_text = str(draft.get("body") or "").strip()

    # Fallback: when Claude returns an empty body (rare — usually a
    # parse failure on a malformed response or a max_tokens truncation
    # mid-string), assemble a deterministic draft from the same context
    # block we already built. Better than 502'ing back to the operator —
    # they can always click "Rigenera" to retry the LLM path.
    if not body_text:
        log.warning("draft_followup.empty_body_fallback", lead_id=lead_id)
        subject_line = subject_line or _fallback_subject(business, person, comune)
        body_text = _fallback_body(
            person=person,
            business=business,
            kwp=kwp,
            savings=savings,
            payback=payback,
            tenant_name=tenant.get("business_name") or "il nostro team",
        )
    elif not subject_line:
        subject_line = _fallback_subject(business, person, comune)

    return FollowUpDraftResponse(
        lead_id=lead_id,
        subject=subject_line,
        body=body_text,
    )


def _fallback_subject(business: str, person: str, comune: str) -> str:
    """Deterministic Italian subject line — used when Claude returns nothing."""
    if business:
        return f"{business} — proposta fotovoltaica personalizzata"
    if comune:
        return f"Proposta fotovoltaica per la sua attività a {comune}"
    return "Proposta fotovoltaica personalizzata"


def _fallback_body(
    *,
    person: str,
    business: str,
    kwp: float | int | None,
    savings: float | int | None,
    payback: float | int | None,
    tenant_name: str,
) -> str:
    """Build a clean Italian follow-up draft from structured ROI data.

    Used as a deterministic backstop when the LLM path fails. Tone is
    consistent with the AI prompt (warm, concrete, soft CTA on
    sopralluogo gratuito).
    """
    greeting = f"Buongiorno {person}," if person and person != "il cliente" else "Buongiorno,"
    intro_target = business or "la sua attività"

    roi_line = ""
    if kwp and savings:
        roi_line = (
            f"\n\nDall'analisi del nostro sistema, l'impianto ottimale per "
            f"{intro_target} sarebbe da circa **{int(kwp)} kWp**, con un "
            f"risparmio annuo stimato di **circa €{int(savings)}**"
            + (f" e un rientro dell'investimento in {payback} anni." if payback else ".")
        )
    elif kwp:
        roi_line = (
            f"\n\nDall'analisi del nostro sistema, l'impianto ottimale per "
            f"{intro_target} sarebbe da circa {int(kwp)} kWp."
        )

    return (
        f"{greeting}\n\n"
        f"sono tornato a cercarla perché credo davvero che il fotovoltaico "
        f"per {intro_target} possa fare una differenza concreta sui costi "
        f"energetici già a partire dai prossimi mesi."
        f"{roi_line}\n\n"
        f"Le proporrei un sopralluogo gratuito e senza impegno per validare "
        f"i numeri sul suo tetto e mostrarle il preventivo esatto. Mi fa sapere "
        f"un giorno e un orario che le sono comodi nella prossima settimana?\n\n"
        f"A presto,\n{tenant_name}"
    )


def _resolve_followup_placeholders(
    text: str,
    *,
    subject: dict[str, Any],
    roof: dict[str, Any],
    roi: dict[str, Any],
    tenant: dict[str, Any],
) -> str:
    """Risolve i segnaposto {{...}} dei template follow-up coi dati reali
    del lead, prima dell'invio. Qualsiasi {{...}} residuo (chiave
    sconosciuta o senza valore) viene rimosso: nessun segnaposto letterale
    finisce nell'email inviata.
    """
    import re as _re

    def _euro(value: Any) -> str:
        try:
            n = int(round(float(value)))
        except (TypeError, ValueError):
            return ""
        return "€" + f"{n:,}".replace(",", ".")

    kwp_raw = roi.get("estimated_kwp") or roof.get("estimated_kwp")
    try:
        kwp = str(int(round(float(kwp_raw)))) if kwp_raw is not None else ""
    except (TypeError, ValueError):
        kwp = ""

    payback_raw = roi.get("payback_years")
    try:
        payback = f"{float(payback_raw):.0f} anni" if payback_raw is not None else ""
    except (TypeError, ValueError):
        payback = ""

    # "risparmio minimo": stima arrotondata per difetto al migliaio —
    # lettura conservativa di "anche solo X all'anno".
    savings_raw = roi.get("annual_savings_eur")
    try:
        risparmio_min = (
            _euro((int(float(savings_raw)) // 1000) * 1000) if savings_raw is not None else ""
        )
    except (TypeError, ValueError):
        risparmio_min = ""

    values: dict[str, str] = {
        "nome": (subject.get("owner_first_name") or "").strip(),
        "azienda": (subject.get("business_name") or "").strip(),
        "comune": (roof.get("comune") or "").strip(),
        "kwp": kwp,
        "risparmio": _euro(savings_raw),
        "risparmio_annuo_minimo": risparmio_min,
        "payback": payback,
        "firma": (tenant.get("email_signature") or tenant.get("business_name") or "").strip(),
    }

    return _re.sub(r"\{\{([a-z_]+)\}\}", lambda m: values.get(m.group(1), ""), text)


def _text_to_html(text: str, *, tenant: dict[str, Any] | None = None) -> str:
    """Plain-text → professional anti-spam HTML email.

    Wraps the body in a clean responsive layout matched to common
    inbox renderers (Gmail, Outlook, Apple Mail). Anti-spam choices:
      • table-based layout with inline CSS only — no external <style>,
        no <script>, no remote fonts (all of which trigger ESP filters)
      • single accent color, no images by default (avoids "image-only"
        spam heuristics)
      • plain-text equivalent generated alongside (Resend SendEmailInput
        already accepts both `html` + `text`)
      • bullet-proof <a> for the optional CTA — `mso-padding-alt` for
        Outlook 2007+ rendering
      • no tracking pixels embedded — Resend handles open/click via
        wrapper redirects which are less abusive than 1×1 GIFs

    The CTA + signature block come from `tenant` (business_name,
    contact_email, etc.). Falls back to neutral copy if not provided.
    """
    import html as _html

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    body_html = "\n".join(
        f'<p style="margin: 0 0 16px; font-size: 15px; line-height: 1.55; '
        f'color: #1f2937;">{_html.escape(p).replace(chr(10), "<br>")}</p>'
        for p in paragraphs
    )

    tenant = tenant or {}
    business_name = (tenant.get("business_name") or "").strip()
    contact_email = (tenant.get("contact_email") or "").strip()
    brand_logo = (tenant.get("brand_logo_url") or "").strip()

    # Header con il logo del tenant. Una sola immagine in cima a
    # un'email ricca di testo è prassi standard e non un trigger spam
    # (diverso da un'email "solo-immagine"). Assente → header vuoto.
    logo_row = (
        '      <tr><td style="padding: 28px 36px 0 36px;">\n'
        f'        <img src="{_html.escape(brand_logo)}" '
        f'alt="{_html.escape(business_name) or "Logo"}" '
        'style="height:34px; width:auto; display:block; border:0;">\n'
        "      </td></tr>\n"
        if brand_logo
        else ""
    )
    body_pad_top = "20px" if brand_logo else "36px"

    # Footer signature block — neutral when tenant is missing.
    footer_lines: list[str] = []
    if business_name:
        footer_lines.append(
            f'<strong style="color:#374151;">{_html.escape(business_name)}</strong>'
        )
    if contact_email:
        footer_lines.append(
            f'<a href="mailto:{_html.escape(contact_email)}" '
            f'style="color:#6b7280; text-decoration:none;">{_html.escape(contact_email)}</a>'
        )
    footer_html = (
        " &middot; ".join(footer_lines)
        if footer_lines
        else '<span style="color:#9ca3af;">Inviata da SolarLead</span>'
    )

    # Wrapper: 600px max-width, white card on light background — tested
    # in Gmail / Outlook365 / Apple Mail / Yahoo. No web fonts (ESP-safe).
    return (
        "<!DOCTYPE html>\n"
        '<html lang="it">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '<meta name="x-apple-disable-message-reformatting">\n'
        "<title>Follow-up</title>\n"
        "</head>\n"
        '<body style="margin:0; padding:0; background:#f9fafb; '
        "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif;\">\n"
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
        'width="100%" style="background:#f9fafb;">\n'
        '  <tr><td align="center" style="padding: 32px 16px;">\n'
        '    <table role="presentation" cellspacing="0" cellpadding="0" '
        'border="0" width="100%" '
        'style="max-width: 600px; background: #ffffff; border-radius: 12px; '
        'box-shadow: 0 1px 3px rgba(0,0,0,0.06); overflow: hidden;">\n'
        f"{logo_row}"
        f'      <tr><td style="padding: {body_pad_top} 36px 20px 36px;">\n'
        f"        {body_html}\n"
        "      </td></tr>\n"
        '      <tr><td style="padding: 0 36px 36px 36px;">\n'
        '        <hr style="border:none; border-top:1px solid #e5e7eb; margin: 8px 0 18px;">\n'
        f'        <p style="margin: 0; font-size: 12px; line-height: 1.5; '
        f'color: #6b7280;">{footer_html}</p>\n'
        "      </td></tr>\n"
        "    </table>\n"
        "  </td></tr>\n"
        "</table>\n"
        "</body>\n"
        "</html>"
    )


@router.post("/{lead_id}/send-draft", response_model=SendDraftResponse)
async def send_draft(
    ctx: CurrentUser,
    lead_id: str,
    body: SendDraftRequest,
) -> SendDraftResponse:
    """Send the (user-edited) follow-up draft via Resend.

    Records a campaigns row (sequence_step = MAX+1 or 10 if fresh),
    updates outreach timestamps, and emits lead.outreach_sent. Bypasses
    OutreachAgent's template renderer so the operator's edited copy is
    sent verbatim.

    Tier-gated: requires advanced_analytics (Pro+).
    """
    from ..core.tier import Capability, require_capability
    from ..services.resend_service import SendEmailInput, send_email

    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Auth-scope + tenant fetch
    lead_q = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, outreach_sent_at, outreach_channel, "
            "roi_data, "
            "subjects(decision_maker_email, owner_first_name, business_name), "
            "roofs(comune, estimated_kwp)"
        )
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    # Moderation gate — hidden lead 404s for a moderated tenant.
    lead_q = apply_released_filter(lead_q, sb, tenant_id)
    lead_res = lead_q.execute()
    if not lead_res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = lead_res.data[0]

    tenant_res = (
        sb.table("tenants")
        .select(
            "tier, settings, business_name, email_from_domain, email_from_name, "
            "contact_email, followup_from_email, brand_logo_url, email_signature"
        )
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    tenant = (tenant_res.data or [{}])[0]

    require_capability(tenant, Capability.ADVANCED_ANALYTICS)

    # Resolve recipient
    subj = lead.get("subjects") or {}
    if isinstance(subj, list):
        subj = subj[0] if subj else {}
    to_email = subj.get("decision_maker_email")
    if not to_email:
        raise HTTPException(
            status_code=422,
            detail="No verified email address on this lead.",
        )

    # Build from address: prefer dedicated followup_from_email when set.
    # Accepts either a bare address or full "Name <addr>" format.
    followup_addr = (tenant.get("followup_from_email") or "").strip()
    if followup_addr:
        # If the operator supplied a bare address, wrap with the sender name.
        if "<" not in followup_addr:
            sender_name = (
                tenant.get("email_from_name") or tenant.get("business_name") or "SolarLead"
            ).strip()
            from_address = f"{sender_name} <{followup_addr}>"
        else:
            from_address = followup_addr
    else:
        name = (tenant.get("email_from_name") or "").strip()
        domain = (tenant.get("email_from_domain") or "").strip()
        from_address = (
            f"{name or tenant.get('business_name', 'SolarLead')} <outreach@{domain}>"
            if domain
            else f"{name or 'SolarLead'} <outreach@solarlead.it>"
        )

    # Risolvi i segnaposto {{...}} dei template coi dati reali del lead
    # (oggetto + corpo) prima dell'invio: nessun {{...}} letterale in posta.
    roof = lead.get("roofs") or {}
    if isinstance(roof, list):
        roof = roof[0] if roof else {}
    roi = lead.get("roi_data") or {}
    resolved_subject = _resolve_followup_placeholders(
        body.subject, subject=subj, roof=roof, roi=roi, tenant=tenant
    )
    resolved_body = _resolve_followup_placeholders(
        body.body, subject=subj, roof=roof, roi=roi, tenant=tenant
    )
    html_body = _text_to_html(resolved_body, tenant=tenant)

    email_input = SendEmailInput(
        from_address=from_address,
        to=[to_email],
        subject=resolved_subject,
        html=html_body,
        text=resolved_body,
        reply_to=tenant.get("contact_email"),
        tags={"lead_id": lead_id, "type": "manual_followup"},
    )

    try:
        result = await send_email(email_input)
    except Exception as exc:
        log.warning("send_draft.resend_failed", lead_id=lead_id, err=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Sequence step: MAX(existing) + 1, floor 2 (manual follow-up is never step 1)
    steps_res = (
        sb.table("outreach_sends")
        .select("sequence_step")
        .eq("lead_id", lead_id)
        .order("sequence_step", desc=True)
        .limit(1)
        .execute()
    )
    max_step = (steps_res.data or [{"sequence_step": 1}])[0].get("sequence_step") or 1
    next_step = max(2, max_step + 1)

    now_iso = datetime.now(UTC).isoformat()
    camp_res = (
        sb.table("outreach_sends")
        .insert(
            {
                "lead_id": lead_id,
                "tenant_id": tenant_id,
                "channel": "email",
                "sequence_step": next_step,
                "status": "sent",
                "template_id": "manual_followup",
                "email_subject": body.subject,
                "email_message_id": result.id,
                "scheduled_for": now_iso,
                "sent_at": now_iso,
                "cost_cents": 1,
                # Mark as manually triggered → cron skips this lead for
                # 24h (see workers/cron.py manual_cooldown logic).
                "is_manual": True,
            }
        )
        .select("id")
        .execute()
    )
    campaign_id = (camp_res.data or [{}])[0].get("id") or ""

    # Advance lead timestamps + register the manual follow-up so the
    # cron skip kicks in.
    lead_update: dict[str, Any] = {"last_followup_sent_at": now_iso}
    if not lead.get("outreach_sent_at"):
        lead_update["outreach_sent_at"] = now_iso
        lead_update["outreach_channel"] = "email"
    sb.table("leads").update(lead_update).eq("id", lead_id).execute()

    # Audit event
    try:
        sb.table("events").insert(
            {
                "tenant_id": tenant_id,
                "lead_id": lead_id,
                "event_type": "lead.outreach_sent",
                "event_source": "route.send_draft",
                "payload": {
                    "channel": "email",
                    "sequence_step": next_step,
                    "campaign_id": campaign_id,
                    "manual": True,
                },
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("send_draft.event_emit_failed", err=str(exc))

    # CRM webhook fanout
    await fire_crm_event(
        tenant_id=tenant_id,
        event_type="lead.outreach_sent",
        data={"lead_id": lead_id, "channel": "email", "campaign_id": campaign_id},
    )

    await audit_log(
        tenant_id,
        "lead.follow_up_sent",
        actor_user_id=ctx.sub,
        target_table="outreach_sends",
        target_id=campaign_id,
        diff={"subject": body.subject, "sequence_step": next_step},
    )
    log.info(
        "send_draft.sent",
        lead_id=lead_id,
        tenant_id=tenant_id,
        message_id=result.id,
        step=next_step,
    )
    return SendDraftResponse(
        ok=True,
        campaign_id=campaign_id,
        message_id=result.id,
    )


@router.post("/send-outreach-batch")
async def send_outreach_batch(
    ctx: CurrentUser,
    tier: Literal["hot", "warm", "cold", "rejected"] | None = Query(default=None),
    channel: Literal["email", "postal"] = Query(default="email"),
    only_new: bool = Query(
        default=True,
        description=(
            "When true (default), skip leads that already have "
            "outreach_sent_at populated. Set false to force a re-send."
        ),
    ),
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict[str, object]:
    """Bulk-enqueue outreach for every matching lead.

    Typical use: on Tuesday morning the installer presses 'send this
    week's campaign' and we fan out one outreach job per Hot lead that
    hasn't been contacted yet.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    query = sb.table("leads").select("id, outreach_sent_at").eq("tenant_id", tenant_id)
    if tier:
        query = query.eq("score_tier", tier)
    if only_new:
        query = query.is_("outreach_sent_at", "null")
    res = query.order("score", desc=True).limit(limit).execute()

    queued = 0
    for lead in res.data or []:
        await enqueue(
            "outreach_task",
            {
                "tenant_id": tenant_id,
                "lead_id": lead["id"],
                "channel": channel,
                "force": not only_new,
            },
            job_id=f"outreach:{tenant_id}:{lead['id']}:{channel}",
        )
        queued += 1
    return {"ok": True, "queued": queued, "total_matching": len(res.data or [])}


@router.post("/score-pending-subjects")
async def score_pending_subjects(
    ctx: CurrentUser,
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, object]:
    """Promote every `subject` without a matching `leads` row by enqueueing
    a scoring_task. The Scoring agent INSERTs the lead on first run
    (see agents/scoring.py "Upsert lead row" block).

    This fills the gap between the hunter funnel (which creates
    `roofs` + `subjects`) and the outreach pipeline (which reads `leads`).
    The funnel itself doesn't auto-enqueue scoring today — this endpoint is
    the manual trigger the dashboard / ops calls after each scan.

    Idempotent per (tenant_id, roof_id, subject_id) via deterministic job_id;
    double-clicks collapse to a single worker run.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    # LEFT JOIN via two queries — PostgREST doesn't support anti-joins.
    # Pull all tenant subjects, then filter out those with a lead already.
    subj_res = (
        sb.table("subjects")
        .select("id, roof_id")
        .eq("tenant_id", tenant_id)
        .not_.is_("roof_id", "null")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    subjects = subj_res.data or []
    if not subjects:
        return {"ok": True, "queued": 0, "total_subjects": 0, "already_scored": 0}

    subject_ids = [s["id"] for s in subjects]
    existing_res = (
        sb.table("leads")
        .select("subject_id")
        .eq("tenant_id", tenant_id)
        .in_("subject_id", subject_ids)
        .execute()
    )
    already = {row["subject_id"] for row in (existing_res.data or [])}

    queued = 0
    for s in subjects:
        if s["id"] in already:
            continue
        await enqueue(
            "scoring_task",
            {
                "tenant_id": tenant_id,
                "roof_id": s["roof_id"],
                "subject_id": s["id"],
            },
            job_id=f"scoring:{tenant_id}:{s['roof_id']}:{s['id']}",
        )
        queued += 1
    return {
        "ok": True,
        "queued": queued,
        "total_subjects": len(subjects),
        "already_scored": len(already),
    }


@router.post("/rescore-all")
async def rescore_all(
    ctx: CurrentUser,
    tier: Literal["hot", "warm", "cold", "rejected"] | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, object]:
    """Bulk-enqueue rescoring for all leads (optionally filtered by tier).

    Returns the number of scoring jobs queued. Each job is idempotent via
    its deterministic job_id, so repeated calls are safe.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    query = sb.table("leads").select("id, roof_id, subject_id").eq("tenant_id", tenant_id)
    if tier:
        query = query.eq("score_tier", tier)
    res = query.limit(limit).execute()

    queued = 0
    for lead in res.data or []:
        await enqueue(
            "scoring_task",
            {
                "tenant_id": tenant_id,
                "roof_id": lead["roof_id"],
                "subject_id": lead["subject_id"],
            },
            job_id=f"scoring:{tenant_id}:{lead['roof_id']}:{lead['subject_id']}",
        )
        queued += 1
    return {"ok": True, "queued": queued, "total_matching": len(res.data or [])}


@router.post("/backfill-derivations")
async def backfill_derivations(
    ctx: CurrentUser,
    overwrite: bool = Query(
        default=False,
        description="If true, recompute even if roofs.derivations is already set.",
    ),
) -> dict[str, object]:
    """Backfill `roofs.derivations` and the `leads.roi_data` summary.

    Use this once after upgrading to a release that adds derivations to the
    v3 funnel (new builds populate them automatically; pre-existing roofs
    inserted before the change have NULL).

    Steps:
      1. Read this tenant's roofs with `area_sqm IS NOT NULL` and (when
         `overwrite=False`) `derivations IS NULL`.
      2. Call `compute_full_derivations()` for each — pure Python, no
         Solar API call, no Replicate, zero spend.
      3. UPDATE roofs.derivations with the rich snapshot.
      4. For each linked lead, UPDATE leads.roi_data with the 4-field
         summary the dashboard UI consumes (estimated_kwp, annual_savings_eur,
         payback_years, co2_saved_kg) preserving any existing keys via merge.

    Idempotent: re-running with overwrite=False is a no-op once all roofs
    have derivations. The Creative Agent's rendering pipeline is NOT
    re-triggered — existing rendering URLs are untouched.
    """
    from ..services.roi_service import compute_full_derivations

    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # 1) Fetch candidate roofs
    query = (
        sb.table("roofs")
        .select("id, area_sqm, estimated_kwp, estimated_yearly_kwh, raw_data, derivations")
        .eq("tenant_id", tenant_id)
        .not_("area_sqm", "is", None)
    )
    if not overwrite:
        query = query.is_("derivations", None)
    roofs_res = query.limit(1000).execute()
    roofs = roofs_res.data or []

    roofs_updated = 0
    leads_updated = 0
    skipped_no_data = 0

    for roof in roofs:
        kwp = roof.get("estimated_kwp")
        kwh = roof.get("estimated_yearly_kwh")
        area = roof.get("area_sqm")
        raw = roof.get("raw_data") or {}
        solar_blob = raw.get("solar") if isinstance(raw, dict) else None

        # Best-effort dig for panel geometry from the cached Solar response
        # (these fields are present in fresh v3 scans but may be missing on
        # older roofs whose raw_data shape changed).
        panel_count = None
        panel_capacity_w = None
        panel_w = None
        panel_h = None
        try:
            sp = (
                (solar_blob or {}).get("solarPotential", {}) if isinstance(solar_blob, dict) else {}
            )
            panel_count = sp.get("maxArrayPanelsCount") or len(sp.get("solarPanels") or [])
            panel_capacity_w = sp.get("panelCapacityWatts")
            panel_w = sp.get("panelWidthMeters")
            panel_h = sp.get("panelHeightMeters")
        except Exception:  # noqa: BLE001
            pass

        try:
            derivations = compute_full_derivations(
                estimated_kwp=kwp,
                estimated_yearly_kwh=kwh,
                roof_area_sqm=area,
                panel_count=panel_count,
                panel_capacity_w=panel_capacity_w,
                panel_width_m=panel_w,
                panel_height_m=panel_h,
                subject_type="b2b",
                tenant_cost_assumptions=None,
                roi_target_years=None,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "backfill_derivations.compute_failed",
                roof_id=roof["id"],
                err=str(exc)[:200],
            )
            continue

        if derivations is None:
            skipped_no_data += 1
            continue

        # 2) Persist on roofs
        try:
            sb.table("roofs").update({"derivations": derivations}).eq("id", roof["id"]).execute()
            roofs_updated += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "backfill_derivations.roof_update_failed",
                roof_id=roof["id"],
                err=str(exc)[:200],
            )
            continue

        # 3) Update linked leads.roi_data with the UI-consumed 4-field summary
        # (merging into any existing roi_data keys to avoid clobbering data
        # the Creative Agent may have already written).
        roi_summary = {
            "estimated_kwp": derivations.get("estimated_kwp"),
            "annual_savings_eur": derivations.get("yearly_savings_eur"),
            "payback_years": derivations.get("payback_years"),
            "co2_saved_kg": derivations.get("co2_kg_per_year"),
        }
        try:
            leads_res = (
                sb.table("leads")
                .select("id, roi_data")
                .eq("tenant_id", tenant_id)
                .eq("roof_id", roof["id"])
                .execute()
            )
            for lead in leads_res.data or []:
                existing = lead.get("roi_data") or {}
                merged = {**existing, **roi_summary}
                sb.table("leads").update({"roi_data": merged}).eq("id", lead["id"]).execute()
                leads_updated += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "backfill_derivations.lead_update_failed",
                roof_id=roof["id"],
                err=str(exc)[:200],
            )

    return {
        "ok": True,
        "roofs_total": len(roofs),
        "roofs_updated": roofs_updated,
        "leads_updated": leads_updated,
        "skipped_no_data": skipped_no_data,
    }


@router.post("/backfill-realistic-sizing")
async def backfill_realistic_sizing(
    ctx: CurrentUser,
    target: Literal["ready_to_send", "all"] = Query(default="ready_to_send"),
    dry_run: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, object]:
    """Recompute this tenant's stored sizing under the realistic-sizing trim.

    For each target roof: re-parse the stored ``raw_data`` (which now runs the
    trim in ``_parse_building_insights`` — drop slivers / keep the main roof
    planes) and recompute ``estimated_kwp`` / ``estimated_yearly_kwh`` +
    ``compute_full_derivations`` + the ``leads.roi_data`` summary. Pure Python —
    NO Solar API / Replicate / spend. The marketing render is untouched (the AI
    paints freely; only the numbers + the deterministic layout view change).

    ``dry_run`` (default True) computes + reports the before/after delta WITHOUT
    writing — run it first to review the drop, then ``dry_run=false`` to apply.
    ``target=ready_to_send`` (default) does the about-to-send leads first.

    Cost assumptions mirror the L4 write + the derivations backfill
    (subject_type=b2b, tenant defaults); the ONLY intended change is the sizing.
    """
    from ..services.google_solar_service import _parse_building_insight_payload
    from ..services.roi_service import compute_full_derivations

    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # 1) Resolve target roofs via their leads.
    lead_q = (
        sb.table("leads")
        .select("id, roof_id, pipeline_status, roi_data")
        .eq("tenant_id", tenant_id)
        .not_("roof_id", "is", None)
    )
    if target == "ready_to_send":
        # 'ready_to_send' is a real pipeline_status value in the DB but is not a
        # member of the LeadStatus enum — use the literal.
        lead_q = lead_q.eq("pipeline_status", "ready_to_send")
    leads = lead_q.limit(limit).execute().data or []
    leads_by_roof: dict[str, list[dict[str, Any]]] = {}
    for lead in leads:
        leads_by_roof.setdefault(lead["roof_id"], []).append(lead)
    roof_ids = list(leads_by_roof.keys())
    if not roof_ids:
        return {"ok": True, "dry_run": dry_run, "roofs": 0, "note": "no target roofs"}

    roofs = (
        sb.table("roofs")
        .select("id, area_sqm, estimated_kwp, estimated_yearly_kwh, raw_data")
        .in_("id", roof_ids)
        .execute()
        .data
        or []
    )

    roofs_changed = 0
    leads_updated = 0
    skipped = 0
    sum_kwp_old = 0.0
    sum_kwp_new = 0.0
    samples: list[dict[str, Any]] = []

    for roof in roofs:
        raw = roof.get("raw_data") or {}
        payload = (raw.get("solar") if isinstance(raw, dict) else None) or raw
        if not isinstance(payload, dict) or "solarPotential" not in payload:
            skipped += 1
            continue
        try:
            insight = _parse_building_insight_payload(payload)  # trim applied here
        except Exception as exc:  # noqa: BLE001
            log.warning("backfill_sizing.parse_failed", roof_id=roof["id"], err=str(exc)[:200])
            skipped += 1
            continue
        if not insight.panels:
            skipped += 1
            continue

        new_kwp = insight.estimated_kwp
        new_kwh = insight.estimated_yearly_kwh
        old_kwp = float(roof.get("estimated_kwp") or 0.0)
        try:
            derivations = compute_full_derivations(
                estimated_kwp=new_kwp,
                estimated_yearly_kwh=new_kwh,
                roof_area_sqm=insight.area_sqm,
                panel_count=len(insight.panels),
                panel_capacity_w=insight.panel_capacity_w,
                panel_width_m=insight.panel_width_m,
                panel_height_m=insight.panel_height_m,
                subject_type="b2b",
                tenant_cost_assumptions=None,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("backfill_sizing.compute_failed", roof_id=roof["id"], err=str(exc)[:200])
            skipped += 1
            continue

        sum_kwp_old += old_kwp
        sum_kwp_new += new_kwp
        if len(samples) < 25:
            samples.append(
                {
                    "roof_id": roof["id"],
                    "kwp_old": round(old_kwp, 1),
                    "kwp_new": round(new_kwp, 1),
                    "yearly_savings_eur": (derivations or {}).get("yearly_savings_eur"),
                }
            )

        if dry_run or derivations is None:
            continue

        # Apply: write the trimmed sizing + fresh derivations on the roof, and
        # the 4-field summary on each linked lead (merge to preserve other keys).
        try:
            sb.table("roofs").update(
                {
                    "estimated_kwp": new_kwp,
                    "estimated_yearly_kwh": new_kwh,
                    "derivations": derivations,
                }
            ).eq("id", roof["id"]).execute()
            roofs_changed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "backfill_sizing.roof_update_failed", roof_id=roof["id"], err=str(exc)[:200]
            )
            continue

        roi_summary = {
            "estimated_kwp": derivations.get("estimated_kwp"),
            "annual_savings_eur": derivations.get("yearly_savings_eur"),
            "payback_years": derivations.get("payback_years"),
            "co2_saved_kg": derivations.get("co2_kg_per_year"),
        }
        for lead in leads_by_roof.get(roof["id"], []):
            try:
                merged = {**(lead.get("roi_data") or {}), **roi_summary}
                sb.table("leads").update({"roi_data": merged}).eq("id", lead["id"]).execute()
                leads_updated += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "backfill_sizing.lead_update_failed", lead_id=lead["id"], err=str(exc)[:200]
                )

    if not dry_run:
        await audit_log(
            tenant_id,
            "roofs.realistic_sizing_backfilled",
            actor_user_id=ctx.sub,
            target_table="roofs",
            target_id=target,
            diff={"target": target, "roofs_changed": roofs_changed, "leads_updated": leads_updated},
        )

    n = max(1, int(sum_kwp_old > 0) + len(samples))  # avoid div-by-zero in report
    return {
        "ok": True,
        "dry_run": dry_run,
        "target": target,
        "roofs_considered": len(roofs),
        "roofs_changed": roofs_changed,
        "leads_updated": leads_updated,
        "skipped": skipped,
        "avg_kwp_old": round(sum_kwp_old / n, 1) if sum_kwp_old else None,
        "avg_kwp_new": round(sum_kwp_new / n, 1) if sum_kwp_new else None,
        "avg_pct_drop": (
            round(100.0 * (sum_kwp_old - sum_kwp_new) / sum_kwp_old, 1) if sum_kwp_old > 0 else None
        ),
        "samples": samples,
    }


class RoofDelineationInput(BaseModel):
    """Operator-drawn real roof area + whether to persist or just preview."""

    polygon_geojson: dict[str, Any] = Field(
        description="GeoJSON Polygon ([lng,lat] rings) of the real usable roof."
    )
    dry_run: bool = True


@router.post("/{lead_id}/roof-delineation")
async def roof_delineation(
    ctx: CurrentUser, lead_id: str, body: RoofDelineationInput
) -> dict[str, object]:
    """Recompute a lead's sizing from an operator-drawn roof polygon (Feature 2).

    Keeps the Google Solar panels whose centre falls INSIDE ``polygon_geojson``
    and recomputes ``estimated_kwp`` / ``estimated_yearly_kwh`` +
    ``compute_full_derivations`` from that subset. ``dry_run`` (default) previews
    the numbers without writing; ``dry_run=false`` persists the override on
    ``roofs.delineation`` + the recomputed sizing, so it flows to the dossier /
    email. Pure Python — no Solar/Replicate spend; the AI render is untouched.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    from ..services.google_solar_service import _parse_building_insight_payload
    from ..services.roi_service import compute_full_derivations
    from ..services.roof_sizing import (
        extract_all_panels,
        panels_inside_polygon,
        recompute_from_panels,
    )

    query = (
        sb.table("leads")
        .select("id, roi_data, roofs(id, area_sqm, raw_data)")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
    )
    query = apply_released_filter(query, sb, tenant_id)
    res = query.execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead non trovato.")
    lead = res.data[0]
    roof = lead.get("roofs") or {}
    if isinstance(roof, list):
        roof = roof[0] if roof else {}
    raw = roof.get("raw_data") or {}
    payload = (raw.get("solar") if isinstance(raw, dict) else None) or raw
    if not isinstance(payload, dict) or "solarPotential" not in payload:
        raise HTTPException(status_code=404, detail="Dati Solar non disponibili per questo lead.")

    try:
        all_panels = extract_all_panels(payload)
        inside = panels_inside_polygon(all_panels, body.polygon_geojson)
    except Exception as exc:  # noqa: BLE001 — malformed GeoJSON → 400
        raise HTTPException(status_code=400, detail="Poligono non valido.") from exc
    if not inside:
        raise HTTPException(status_code=400, detail="Nessun pannello dentro l'area selezionata.")

    insight = _parse_building_insight_payload(payload)
    sized = recompute_from_panels(insight, inside)
    derivations = compute_full_derivations(
        estimated_kwp=sized.estimated_kwp,
        estimated_yearly_kwh=sized.estimated_yearly_kwh,
        roof_area_sqm=insight.area_sqm,
        panel_count=len(inside),
        panel_capacity_w=insight.panel_capacity_w,
        panel_width_m=insight.panel_width_m,
        panel_height_m=insight.panel_height_m,
        subject_type="b2b",
        tenant_cost_assumptions=None,
    )

    panel_area = round(len(inside) * insight.panel_width_m * insight.panel_height_m, 1)
    preview = {
        "kept_panel_count": len(inside),
        "total_panel_count": len(all_panels),
        "estimated_kwp": sized.estimated_kwp,
        "estimated_yearly_kwh": sized.estimated_yearly_kwh,
        "panel_area_sqm": panel_area,
        "yearly_savings_eur": (derivations or {}).get("yearly_savings_eur"),
        "payback_years": (derivations or {}).get("payback_years"),
    }
    if body.dry_run or derivations is None:
        return {"ok": True, "dry_run": True, **preview}

    # Persist: the manual override supersedes the automatic trim for this roof.
    delineation = {
        "polygon_geojson": body.polygon_geojson,
        "kept_panel_count": len(inside),
        "area_sqm": panel_area,
        "kwp": sized.estimated_kwp,
        "by_user_id": ctx.sub,
        "at": datetime.now(UTC).isoformat(),
    }
    sb.table("roofs").update(
        {
            "estimated_kwp": sized.estimated_kwp,
            "estimated_yearly_kwh": sized.estimated_yearly_kwh,
            "derivations": derivations,
            "delineation": delineation,
        }
    ).eq("id", roof["id"]).execute()
    merged = {
        **(lead.get("roi_data") or {}),
        "estimated_kwp": derivations.get("estimated_kwp"),
        "annual_savings_eur": derivations.get("yearly_savings_eur"),
        "payback_years": derivations.get("payback_years"),
        "co2_saved_kg": derivations.get("co2_kg_per_year"),
    }
    sb.table("leads").update({"roi_data": merged}).eq("id", lead_id).execute()
    await audit_log(
        tenant_id,
        "roofs.delineation_saved",
        actor_user_id=ctx.sub,
        target_table="roofs",
        target_id=roof["id"],
        diff={"kept_panels": len(inside), "kwp": sized.estimated_kwp},
    )
    return {"ok": True, "dry_run": False, **preview}
