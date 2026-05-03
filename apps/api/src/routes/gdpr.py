"""GDPR endpoints — diritto di accesso e cancellazione (FLUSSO 1 v3).

Risponde alle richieste GDPR dell'interessato:

  * GET  /api/gdpr/export?email=...    → art. 15 (diritto di accesso)
  * POST /api/gdpr/erase                → art. 17 (diritto all'oblio)

Auth: l'admin token interno è obbligatorio. La versione "self-service"
(verification-token via email link) è un'iterazione successiva.

Tutte le query sono service-role (bypass RLS) perché un soggetto può
chiedere i suoi dati indipendentemente dal tenant — l'API normalmente
è tenant-scoped, qui no.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr

from ..core.config import settings
from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Auth (admin token only for MVP)
# ---------------------------------------------------------------------------


def _require_admin(request: Request) -> None:
    token = request.headers.get("X-Admin-Token")
    expected = getattr(settings, "admin_api_token", None)
    if not expected or not token or token != expected:
        raise HTTPException(status_code=401, detail="admin token required")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class GDPRExportResponse(BaseModel):
    email: str
    leads: list[dict[str, Any]]
    subjects: list[dict[str, Any]]
    contact_extraction_log: list[dict[str, Any]]
    outreach_sends: list[dict[str, Any]]
    exported_at: str


class GDPREraseRequest(BaseModel):
    email: EmailStr
    reason: str | None = None


class GDPREraseResponse(BaseModel):
    email: str
    erased_leads: int
    erased_subjects: int
    erased_audit_rows: int
    blacklisted: bool
    erased_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/export", response_model=GDPRExportResponse)
async def gdpr_export(
    email: str = Query(..., description="Email dell'interessato"),
    _admin: None = Depends(_require_admin),
) -> GDPRExportResponse:
    """Restituisce tutti i dati associati a un'email pubblica.

    L'output include:
      * leads dove la email compare come `email` o `email_alt`
      * subjects collegati
      * contact_extraction_log (audit trail della provenienza)
      * outreach_sends storiche
    """
    sb = get_service_client()

    # subjects che hanno la email come decision_maker_email
    subj_res = (
        sb.table("subjects")
        .select(
            "id, tenant_id, type, business_name, owner_first_name, owner_last_name, "
            "decision_maker_email, decision_maker_phone, created_at"
        )
        .eq("decision_maker_email", email)
        .execute()
    )
    subjects = subj_res.data or []
    subject_ids = [s["id"] for s in subjects]

    # leads collegati (uno-a-uno con subject)
    leads = []
    if subject_ids:
        lr = (
            sb.table("leads")
            .select("id, public_slug, tenant_id, pipeline_status, score, created_at, subject_id")
            .in_("subject_id", subject_ids)
            .execute()
        )
        leads = lr.data or []

    # contact_extraction_log con la email come valore estratto
    cel = (
        sb.table("contact_extraction_log")
        .select(
            "id, tenant_id, candidate_id, contact_value, contact_type, "
            "source_url, source_type, extraction_method, confidence, extracted_at"
        )
        .eq("contact_value", email)
        .execute()
    )

    # outreach_sends inviati a questo destinatario
    os_res = (
        sb.table("outreach_sends")
        .select("id, tenant_id, lead_id, channel, status, sent_at, delivered_at, opened_at")
        .eq("recipient_email", email)
        .execute()
    )

    return GDPRExportResponse(
        email=email,
        leads=leads,
        subjects=subjects,
        contact_extraction_log=cel.data or [],
        outreach_sends=os_res.data or [],
        exported_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/erase", response_model=GDPREraseResponse)
async def gdpr_erase(
    body: GDPREraseRequest,
    _admin: None = Depends(_require_admin),
) -> GDPREraseResponse:
    """Cancella i dati personali associati all'email + blacklist permanente.

    L'effetto è composto:
      * subjects.decision_maker_email = NULL (mantiene la riga per audit
        ma rimuove il PII)
      * leads collegati → pipeline_status='blacklisted'
      * contact_extraction_log entries cancellate
      * email_blacklist insert (impedisce re-discovery futuro)
    """
    sb = get_service_client()
    email = body.email
    now = datetime.now(timezone.utc).isoformat()

    # 1) Subjects: blank il PII
    subj_res = (
        sb.table("subjects")
        .select("id, tenant_id")
        .eq("decision_maker_email", email)
        .execute()
    )
    subjects = subj_res.data or []
    subject_ids = [s["id"] for s in subjects]
    if subject_ids:
        sb.table("subjects").update(
            {
                "decision_maker_email": None,
                "decision_maker_email_verified": False,
                "owner_first_name": None,
                "owner_last_name": None,
            }
        ).in_("id", subject_ids).execute()

    # 2) Leads: blacklist
    erased_leads = 0
    if subject_ids:
        lr = (
            sb.table("leads")
            .update({"pipeline_status": "blacklisted"})
            .in_("subject_id", subject_ids)
            .execute()
        )
        erased_leads = len(lr.data or [])

    # 3) Audit rows: hard delete (è esattamente il dato che il diritto
    #    all'oblio chiede di rimuovere)
    cel_res = (
        sb.table("contact_extraction_log")
        .delete()
        .eq("contact_value", email)
        .execute()
    )
    erased_audit = len(cel_res.data or [])

    # 4) Blacklist permanente per impedire re-discovery futuro
    blacklisted = False
    try:
        sb.table("email_blacklist").upsert(
            {
                "email": email,
                "reason": body.reason or "gdpr_erasure",
                "added_at": now,
            },
            on_conflict="email",
        ).execute()
        blacklisted = True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "gdpr_erase.blacklist_failed",
            email=email,
            err=type(exc).__name__,
        )

    log.info(
        "gdpr_erase.completed",
        email=email,
        erased_leads=erased_leads,
        erased_subjects=len(subject_ids),
        erased_audit=erased_audit,
        blacklisted=blacklisted,
    )

    return GDPREraseResponse(
        email=email,
        erased_leads=erased_leads,
        erased_subjects=len(subject_ids),
        erased_audit_rows=erased_audit,
        blacklisted=blacklisted,
        erased_at=now,
    )
