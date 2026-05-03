"""GSE Practice routes.

Surface (mounted at ``/v1`` so URLs include the resource hierarchy):

  GET  /v1/leads/{lead_id}/practice/draft
       Pre-populated form data + eligibility flags. No write side-effects.

  POST /v1/leads/{lead_id}/practice
       Create a practice + N draft documents, enqueue fan-out worker.
       Returns 201 immediately; PDFs appear async (poll the practice
       detail endpoint or document rows).

  GET  /v1/practices                        — list, tenant-scoped
  GET  /v1/practices/{practice_id}          — detail with documents

  GET  /v1/practices/{id}/documents/{template_code}/download
       302 → fresh signed URL (1 h). Mirrors the quote PDF redirect.

  POST /v1/practices/{id}/documents/{template_code}/regenerate
       Re-render synchronously (off the event loop). Returns the row.

  PATCH /v1/practices/{id}/documents/{template_code}
       Status transitions + free-form manual_data. The dashboard's
       "Marca come inviato" / "Inserisci esito" buttons hit this.

Auth: same pattern as ``quotes.py`` — ``CurrentUser`` Depends, then
``require_tenant(ctx)`` to scope.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..services.practice_data_mapper import PracticeDataMapper
from ..services.practice_deadlines_service import (
    list_deadlines_for_practice,
    list_open_deadlines_for_tenant,
)
from ..services.practice_events_service import (
    EVT_DATA_COLLECTED,
    list_events,
)
from ..services.practice_service import (
    DEFAULT_TEMPLATE_CODES,
    RENDERINGS_BUCKET,
    Practice,
    PracticeDocument,
    _emit_practice_event,
    create_practice,
    get_document,
    get_draft_preview,
    get_practice,
    list_practices,
    regenerate_document,
    update_document_status,
)
from ..services.storage_service import sign_url

router = APIRouter()
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class DraftPreviewResponse(BaseModel):
    """The bag the "Crea pratica" modal uses to pre-populate.

    Mirrors the dict ``get_draft_preview`` returns. Pydantic-ified for
    OpenAPI / TypeScript codegen friendliness.
    """

    eligible: bool
    has_existing: bool
    existing_practice_id: str | None = None
    existing_practice_number: str | None = None
    missing_tenant_fields: list[str] = Field(default_factory=list)
    suggested_practice_number: str
    prefill: dict[str, Any]
    quote_id: str | None = None


class CreatePracticeRequest(BaseModel):
    """Form payload from the "Crea pratica" modal.

    Loose typing on impianto/catastale/componenti — the schema evolves
    fast as we add new docs and the form picks up new fields. The
    practices.CHECK constraints catch invalid distributore values
    server-side, so a typo here surfaces as a 400 from Postgres.
    """

    quote_id: UUID | None = None
    impianto_potenza_kw: float
    impianto_pannelli_count: int | None = None
    impianto_pod: str | None = None
    impianto_distributore: str = "e_distribuzione"
    impianto_data_inizio_lavori: str | None = None
    impianto_data_fine_lavori: str | None = None
    catastale_foglio: str | None = None
    catastale_particella: str | None = None
    catastale_subalterno: str | None = None
    componenti_data: dict[str, Any] = Field(default_factory=dict)
    # Sprint 2: template-specific JSONB. Loose schema — keys are
    # documented in practice_data_mapper.py EXTRAS_SHAPE.
    extras: dict[str, Any] = Field(default_factory=dict)
    template_codes: list[str] | None = None  # default = DEFAULT_TEMPLATE_CODES


class PracticeResponse(BaseModel):
    id: str
    tenant_id: str
    lead_id: str
    quote_id: str | None
    practice_number: str
    practice_seq: int
    status: str
    impianto_potenza_kw: float
    impianto_pannelli_count: int | None
    impianto_pod: str | None
    impianto_distributore: str
    impianto_data_inizio_lavori: str | None
    impianto_data_fine_lavori: str | None
    catastale_foglio: str | None
    catastale_particella: str | None
    catastale_subalterno: str | None
    componenti_data: dict[str, Any]
    extras: dict[str, Any]
    created_at: str
    updated_at: str

    @classmethod
    def from_dataclass(cls, p: Practice) -> "PracticeResponse":
        return cls(
            id=p.id,
            tenant_id=p.tenant_id,
            lead_id=p.lead_id,
            quote_id=p.quote_id,
            practice_number=p.practice_number,
            practice_seq=p.practice_seq,
            status=p.status,
            impianto_potenza_kw=p.impianto_potenza_kw,
            impianto_pannelli_count=p.impianto_pannelli_count,
            impianto_pod=p.impianto_pod,
            impianto_distributore=p.impianto_distributore,
            impianto_data_inizio_lavori=p.impianto_data_inizio_lavori,
            impianto_data_fine_lavori=p.impianto_data_fine_lavori,
            catastale_foglio=p.catastale_foglio,
            catastale_particella=p.catastale_particella,
            catastale_subalterno=p.catastale_subalterno,
            componenti_data=p.componenti_data,
            extras=p.extras,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class PracticeDocumentResponse(BaseModel):
    id: str
    practice_id: str
    tenant_id: str
    template_code: str
    template_version: str
    status: str
    pdf_url: str | None
    generation_error: str | None
    generated_at: str | None
    sent_at: str | None
    accepted_at: str | None
    rejected_at: str | None
    rejection_reason: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_dataclass(cls, d: PracticeDocument) -> "PracticeDocumentResponse":
        return cls(
            id=d.id,
            practice_id=d.practice_id,
            tenant_id=d.tenant_id,
            template_code=d.template_code,
            template_version=d.template_version,
            status=d.status,
            pdf_url=d.pdf_url,
            generation_error=d.generation_error,
            generated_at=d.generated_at,
            sent_at=d.sent_at,
            accepted_at=d.accepted_at,
            rejected_at=d.rejected_at,
            rejection_reason=d.rejection_reason,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )


class CreatePracticeResponse(BaseModel):
    """201 body. Includes the practice + initial document rows so the
    redirect target can render the page without an immediate refetch."""

    practice: PracticeResponse
    documents: list[PracticeDocumentResponse]


class PracticeListItem(BaseModel):
    """Slim shape for the index page — no JSONB blobs.

    Includes a derived ``cliente_label`` so the table doesn't have to
    branch on B2B/B2C in the dashboard layer.
    """

    id: str
    practice_number: str
    practice_seq: int
    status: str
    impianto_potenza_kw: float
    impianto_distributore: str
    cliente_label: str
    documenti_totali: int
    documenti_pronti: int
    created_at: str
    updated_at: str


class UpdateDocumentRequest(BaseModel):
    status: str | None = None
    manual_data: dict[str, Any] | None = None
    rejection_reason: str | None = None


# ---------------------------------------------------------------------------
# Routes — draft + create
# ---------------------------------------------------------------------------


async def _check_practice_eligibility(
    *, tenant_id: UUID, lead_id: UUID
) -> list[dict[str, str]]:
    """Return a list of eligibility errors for creating a GSE practice.

    Sprint 2.3 — refuse to instantiate a practice when the lead is
    missing data the GSE form will eventually reject. Each error has a
    machine-readable ``code`` and a user-facing Italian ``message`` so
    the dashboard can render them as a checklist next to the
    "Crea pratica" button.

    Empty list = lead is eligible. Errors:
      * subject_anagrafica_incomplete — missing business_name, vat,
        decision_maker_email
      * roof_specs_missing — no estimated_kwp / area_sqm
      * bolletta_missing — no upload row for this lead

    Best-effort: any DB error during the check returns "no errors"
    (graceful degradation; the route layer trusts existing 4xx/5xx
    handling for the actual create_practice call).
    """
    from ..core.supabase_client import get_service_client

    sb = get_service_client()
    errors: list[dict[str, str]] = []

    try:
        res = (
            sb.table("leads")
            .select(
                "id, "
                "subjects(business_name, vat_number, decision_maker_email), "
                "roofs(estimated_kwp, area_sqm)"
            )
            .eq("id", str(lead_id))
            .eq("tenant_id", str(tenant_id))
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return [{"code": "lead_not_found", "message": "Lead non trovato."}]
        lead = rows[0]
        subj = lead.get("subjects") or {}
        roof = lead.get("roofs") or {}
        if isinstance(subj, list):
            subj = subj[0] if subj else {}
        if isinstance(roof, list):
            roof = roof[0] if roof else {}

        # Subject completeness
        subj_missing: list[str] = []
        if not (subj.get("business_name") or "").strip():
            subj_missing.append("business_name")
        if not (subj.get("vat_number") or "").strip():
            subj_missing.append("vat_number")
        if not (subj.get("decision_maker_email") or "").strip():
            subj_missing.append("decision_maker_email")
        if subj_missing:
            errors.append({
                "code": "subject_anagrafica_incomplete",
                "message": (
                    "Anagrafica cliente incompleta. Mancano: "
                    + ", ".join(subj_missing)
                ),
            })

        # Roof technical specs
        roof_missing: list[str] = []
        if not roof.get("estimated_kwp"):
            roof_missing.append("estimated_kwp")
        if not roof.get("area_sqm"):
            roof_missing.append("area_sqm")
        if roof_missing:
            errors.append({
                "code": "roof_specs_missing",
                "message": (
                    "Dati tecnici tetto mancanti. Eseguire l'analisi "
                    "Solar API prima di creare la pratica."
                ),
            })

        # At least one bolletta upload row for this lead
        bolletta_res = (
            sb.table("bolletta_uploads")
            .select("id")
            .eq("lead_id", str(lead_id))
            .limit(1)
            .execute()
        )
        if not (bolletta_res.data or []):
            errors.append({
                "code": "bolletta_missing",
                "message": (
                    "Bolletta non caricata. Il cliente deve caricare "
                    "almeno una bolletta sul portale, oppure inserisci i "
                    "consumi manualmente."
                ),
            })
    except Exception as exc:  # noqa: BLE001 — soft-fail to allow create
        log.warning(
            "practice.eligibility_check_failed",
            lead_id=str(lead_id),
            err=str(exc)[:200],
        )
        return []

    return errors


@router.get(
    "/leads/{lead_id}/practice/draft",
    response_model=DraftPreviewResponse,
)
async def practice_draft(
    ctx: CurrentUser, lead_id: UUID
) -> DraftPreviewResponse:
    """Pre-populate the "Crea pratica" modal.

    Read-only — does NOT consume the per-tenant practice counter. The
    suggested number is a UI hint; the actual atomic allocation happens
    inside ``create_practice``.
    """
    tenant_id = require_tenant(ctx)
    try:
        preview = get_draft_preview(lead_id=lead_id, tenant_id=tenant_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="lead not found"
        )
    return DraftPreviewResponse(**preview)


@router.post(
    "/leads/{lead_id}/practice",
    response_model=CreatePracticeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_practice(
    ctx: CurrentUser,
    lead_id: UUID,
    body: CreatePracticeRequest,
) -> CreatePracticeResponse:
    """Create a practice and enqueue document generation.

    409 when a practice already exists for this lead (UNIQUE(lead_id)).
    The dashboard handles 409 by redirecting to the existing practice,
    which it learns about from the draft endpoint's
    ``existing_practice_id``.
    """
    tenant_id = require_tenant(ctx)

    # Sprint 2.3 — eligibility gate. Refuse to create a practice when
    # the lead is missing prerequisite data (incomplete subject, no
    # roof technical specs, no bolletta). Without this gate the
    # operator could generate PDFs with empty fields on demo VATs or
    # half-enriched leads, then waste time figuring out why the GSE
    # form is rejected on submission.
    eligibility_errors = await _check_practice_eligibility(
        tenant_id=tenant_id,
        lead_id=lead_id,
    )
    if eligibility_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": (
                    "Lead non idoneo alla creazione di pratica GSE: dati "
                    "mancanti. Completa i campi indicati e riprova."
                ),
                "eligibility_errors": eligibility_errors,
            },
        )

    payload: dict[str, Any] = body.model_dump(exclude={"template_codes"})
    if body.quote_id is not None:
        payload["quote_id"] = str(body.quote_id)

    try:
        practice = await create_practice(
            tenant_id=tenant_id,
            lead_id=lead_id,
            payload=payload,
            template_codes=body.template_codes or list(DEFAULT_TEMPLATE_CODES),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    except Exception as exc:  # noqa: BLE001
        # Postgres UNIQUE violation → 409. The supabase-py client raises a
        # generic exception with the duplicate key name in str(exc); we
        # match on the constraint name we know is the lead-uniqueness one.
        msg = str(exc)
        if "practices_lead_id_key" in msg or "duplicate key" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="practice already exists for this lead",
            )
        log.exception(
            "practice.create.failed",
            tenant_id=str(tenant_id),
            lead_id=str(lead_id),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to create practice",
        )

    # Re-read the practice with documents so we can return them in the 201.
    detail = get_practice(practice_id=practice.id, tenant_id=tenant_id) or {}
    docs = detail.get("practice_documents") or []

    return CreatePracticeResponse(
        practice=PracticeResponse.from_dataclass(practice),
        documents=[
            PracticeDocumentResponse(
                id=str(d["id"]),
                practice_id=str(d["practice_id"]),
                tenant_id=str(d["tenant_id"]),
                template_code=d["template_code"],
                template_version=d["template_version"],
                status=d["status"],
                pdf_url=d.get("pdf_url"),
                generation_error=d.get("generation_error"),
                generated_at=d.get("generated_at"),
                sent_at=d.get("sent_at"),
                accepted_at=d.get("accepted_at"),
                rejected_at=d.get("rejected_at"),
                rejection_reason=d.get("rejection_reason"),
                created_at=d["created_at"],
                updated_at=d["updated_at"],
            )
            for d in docs
        ],
    )


# ---------------------------------------------------------------------------
# Routes — list + detail
# ---------------------------------------------------------------------------


@router.get("/practices", response_model=list[PracticeListItem])
async def list_practices_route(
    ctx: CurrentUser,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[PracticeListItem]:
    """Tenant-scoped list with derived counts.

    The dashboard table renders directly from this — keep the row
    shape compact. Heavy fields (data_snapshot, componenti_data,
    auto_data_snapshot) live on the detail endpoint.
    """
    tenant_id = require_tenant(ctx)
    rows = list_practices(
        tenant_id=tenant_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    out: list[PracticeListItem] = []
    for r in rows:
        docs = r.get("practice_documents") or []
        ready = sum(1 for d in docs if d.get("pdf_url"))
        # Lead/subject embed: B2B uses business_name; B2C falls back to
        # the owner's first+last. Defensive .get() chain because the
        # embed may be partial when the lead is missing a subject.
        subj = ((r.get("leads") or {}).get("subjects") or {})
        cliente_label = (
            subj.get("business_name")
            or " ".join(
                p
                for p in [subj.get("owner_first_name"), subj.get("owner_last_name")]
                if p
            ).strip()
            or "—"
        )
        out.append(
            PracticeListItem(
                id=str(r["id"]),
                practice_number=r["practice_number"],
                practice_seq=int(r["practice_seq"]),
                status=r["status"],
                impianto_potenza_kw=float(r["impianto_potenza_kw"] or 0),
                impianto_distributore=r["impianto_distributore"],
                cliente_label=cliente_label,
                documenti_totali=len(docs),
                documenti_pronti=ready,
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
        )
    return out


@router.get("/practices/{practice_id}")
async def get_practice_detail(
    ctx: CurrentUser, practice_id: UUID
) -> dict[str, Any]:
    """Detail with all sub-resources joined.

    Returns the raw PostgREST row dict (with embedded leads/subjects
    /practice_documents) — the dashboard uses many of the JSONB fields
    directly, so type-erasing them through Pydantic would just bloat
    the schema for marginal gain.
    """
    tenant_id = require_tenant(ctx)
    detail = get_practice(practice_id=practice_id, tenant_id=tenant_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="practice not found"
        )
    return detail


# ---------------------------------------------------------------------------
# Routes — document download / regenerate / patch
# ---------------------------------------------------------------------------


@router.get(
    "/practices/{practice_id}/documents/{template_code}/download",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
)
async def download_document(
    ctx: CurrentUser, practice_id: UUID, template_code: str
) -> RedirectResponse:
    """302 redirect to a fresh signed URL.

    The renderings bucket is private in production; the public URL
    stored on ``pdf_url`` only works for public buckets. We mint a new
    1 h signed URL on each click — short enough not to be abused, long
    enough for a slow download / re-share.
    """
    tenant_id = require_tenant(ctx)
    doc = get_document(
        practice_id=practice_id, template_code=template_code, tenant_id=tenant_id
    )
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="document not found"
        )
    if not doc.pdf_storage_path:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="document not yet rendered",
        )
    try:
        signed = sign_url(RENDERINGS_BUCKET, doc.pdf_storage_path, expires_in=3600)
    except Exception:
        log.exception(
            "practice.document.sign_failed",
            practice_id=str(practice_id),
            template_code=template_code,
        )
        # Fall back to the public URL (works on public buckets, 403s on
        # private — user sees a clear browser error rather than a hang).
        if doc.pdf_url:
            return RedirectResponse(url=doc.pdf_url, status_code=302)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to sign document URL",
        )
    return RedirectResponse(url=signed, status_code=302)


@router.post(
    "/practices/{practice_id}/documents/{template_code}/regenerate",
    response_model=PracticeDocumentResponse,
)
async def regenerate(
    ctx: CurrentUser, practice_id: UUID, template_code: str
) -> PracticeDocumentResponse:
    """Re-render a single document synchronously.

    For a 1-2 s render this is acceptable; the alternative (enqueue +
    poll) is more robust but worse UX. If render times grow we'll move
    to enqueue-and-202 and let the dashboard poll.
    """
    tenant_id = require_tenant(ctx)
    try:
        doc = await regenerate_document(
            practice_id=practice_id,
            template_code=template_code,
            tenant_id=tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    except Exception:
        log.exception(
            "practice.document.regenerate.failed",
            practice_id=str(practice_id),
            template_code=template_code,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to regenerate document",
        )
    return PracticeDocumentResponse.from_dataclass(doc)


@router.patch(
    "/practices/{practice_id}/documents/{template_code}",
    response_model=PracticeDocumentResponse,
)
async def patch_document(
    ctx: CurrentUser,
    practice_id: UUID,
    template_code: str,
    body: UpdateDocumentRequest,
) -> PracticeDocumentResponse:
    """Status transitions + manual_data edits.

    The CHECK constraint on practice_documents.status takes care of
    validating the enum — anything not in ('draft', 'reviewed', 'sent',
    'accepted', 'rejected', 'amended', 'completed') comes back as a 400.
    """
    tenant_id = require_tenant(ctx)
    doc = update_document_status(
        practice_id=practice_id,
        template_code=template_code,
        tenant_id=tenant_id,
        status=body.status,
        manual_data=body.manual_data,
        rejection_reason=body.rejection_reason,
    )
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="document not found"
        )
    return PracticeDocumentResponse.from_dataclass(doc)


# ---------------------------------------------------------------------------
# Routes — Livello 2: events + deadlines
# ---------------------------------------------------------------------------


class PracticeEventResponse(BaseModel):
    """Append-only event log row (timeline UI)."""

    id: str
    practice_id: str
    document_id: str | None
    event_type: str
    payload: dict[str, Any]
    actor_user_id: str | None
    occurred_at: str
    created_at: str


class PracticeDeadlineResponse(BaseModel):
    id: str
    practice_id: str
    document_id: str | None
    deadline_kind: str
    due_at: str
    status: str
    satisfied_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    # Convenience fields for the tenant-wide list endpoint (the
    # detail endpoint leaves these None — the practice context is
    # already implied by the URL).
    practice_number: str | None = None
    practice_status: str | None = None


@router.get(
    "/practices/{practice_id}/events",
    response_model=list[PracticeEventResponse],
)
async def list_practice_events_route(
    ctx: CurrentUser, practice_id: UUID
) -> list[PracticeEventResponse]:
    """Chronological event log for one practice (oldest → newest).

    The dashboard timeline reverses client-side so the most recent
    event sits at the top.  Capped at 200 events per call — practices
    that emit more than that are an outlier (we'd add pagination if
    we ever see one).
    """
    tenant_id = require_tenant(ctx)
    events = list_events(tenant_id=tenant_id, practice_id=practice_id)
    return [
        PracticeEventResponse(
            id=e.id,
            practice_id=e.practice_id,
            document_id=e.document_id,
            event_type=e.event_type,
            payload=e.payload,
            actor_user_id=e.actor_user_id,
            occurred_at=e.occurred_at,
            created_at=e.created_at,
        )
        for e in events
    ]


@router.get(
    "/practices/{practice_id}/deadlines",
    response_model=list[PracticeDeadlineResponse],
)
async def list_practice_deadlines_route(
    ctx: CurrentUser, practice_id: UUID
) -> list[PracticeDeadlineResponse]:
    """All deadlines (open / satisfied / overdue / cancelled) for one practice."""
    tenant_id = require_tenant(ctx)
    rows = list_deadlines_for_practice(
        tenant_id=tenant_id, practice_id=practice_id
    )
    return [_deadline_row_to_response(r) for r in rows]


@router.get(
    "/practice-deadlines",
    response_model=list[PracticeDeadlineResponse],
)
async def list_open_practice_deadlines_route(
    ctx: CurrentUser,
    limit: int = Query(50, ge=1, le=200),
) -> list[PracticeDeadlineResponse]:
    """Tenant-wide open + overdue deadlines, ordered by urgency.

    Powers the home-page "Scadenze imminenti" panel and the dashboard
    bell summary.  Excludes satisfied/cancelled by design — closed
    deadlines aren't actionable.
    """
    tenant_id = require_tenant(ctx)
    rows = list_open_deadlines_for_tenant(tenant_id=tenant_id, limit=limit)
    return [_deadline_row_to_response(r) for r in rows]


# ---------------------------------------------------------------------------
# Routes — Livello 2 Sprint 3: missing-fields report + practice PATCH
# ---------------------------------------------------------------------------


@router.get("/practices/{practice_id}/missing-fields")
async def get_missing_fields(
    ctx: CurrentUser, practice_id: UUID
) -> dict[str, Any]:
    """Structured gap report for every registered template_code.

    Groups missing fields by source so the dashboard can show a form
    with three targeted sections:
      • "Dati installatore" → PATCH /v1/tenants/me
      • "Dati pratica"      → PATCH /v1/practices/{id}
      • "Dati cliente"      → read-only, link to lead edit

    Typical use: the practice detail page calls this on load and renders
    the MissingDataPanel only when ``all_ready == false``.
    """
    tenant_id = require_tenant(ctx)
    try:
        mapper = PracticeDataMapper(practice_id, tenant_id)
        report = mapper.get_missing_fields_report()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="practice not found"
        )
    return report


class PatchPracticeRequest(BaseModel):
    """Fields the installer can update after the practice is created.

    Everything is optional — the PATCH is sparse (only provided fields
    are written).  Validation of the ``impianto_distributore`` enum still
    runs Postgres-side via the CHECK constraint; a typo surfaces as 400.
    """

    # Impianto
    impianto_potenza_kw: float | None = None
    impianto_pannelli_count: int | None = None
    impianto_pod: str | None = None
    impianto_distributore: str | None = None
    impianto_data_inizio_lavori: str | None = None
    impianto_data_fine_lavori: str | None = None
    # Catastali
    catastale_foglio: str | None = None
    catastale_particella: str | None = None
    catastale_subalterno: str | None = None
    # Components (full replace of componenti_data JSONB)
    componenti_data: dict[str, Any] | None = None
    # Extras (merged into existing extras, not replaced wholesale)
    extras_patch: dict[str, Any] | None = None


@router.patch(
    "/practices/{practice_id}",
    response_model=PracticeResponse,
)
async def patch_practice(
    ctx: CurrentUser,
    practice_id: UUID,
    body: PatchPracticeRequest,
    regenerate: bool = Query(
        False,
        description="When true, re-enqueue all documents that had generation_error (missing fields fixed).",
    ),
) -> PracticeResponse:
    """Sparse-update practice fields and optionally re-trigger generation.

    Designed for the "Dati mancanti" form on the practice detail page:
    the installer fills in POD, catastali, dates, or component details
    they skipped at creation time, then we emit a ``data_collected``
    event and (when ``?regenerate=true``) re-enqueue all draft-with-error
    documents for a fresh render.
    """
    tenant_id = require_tenant(ctx)

    sb = __import__(
        "src.core.supabase_client", fromlist=["get_service_client"]
    ).get_service_client()

    # 1. Verify practice belongs to tenant.
    existing_res = (
        sb.table("practices")
        .select("id, extras, status")
        .eq("id", str(practice_id))
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not existing_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="practice not found"
        )
    existing = existing_res.data[0]

    # 2. Build sparse update dict.
    update: dict[str, Any] = {}
    for field in (
        "impianto_potenza_kw",
        "impianto_pannelli_count",
        "impianto_pod",
        "impianto_distributore",
        "impianto_data_inizio_lavori",
        "impianto_data_fine_lavori",
        "catastale_foglio",
        "catastale_particella",
        "catastale_subalterno",
    ):
        val = getattr(body, field, None)
        if val is not None:
            update[field] = val
    if body.componenti_data is not None:
        update["componenti_data"] = body.componenti_data
    if body.extras_patch is not None:
        # Deep-merge extras: existing JSONB + patch dict (patch wins on key conflict).
        merged = dict(existing.get("extras") or {})
        merged.update(body.extras_patch)
        update["extras"] = merged

    if not update:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no updatable fields provided",
        )

    # 3. Apply.
    res = (
        sb.table("practices")
        .update(update)
        .eq("id", str(practice_id))
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="update failed",
        )

    # 4. Emit data_collected event.
    _emit_practice_event(
        tenant_id=tenant_id,
        practice_id=practice_id,
        event_type=EVT_DATA_COLLECTED,
        payload={"updated_fields": list(update.keys())},
        actor_user_id=ctx.user.id if ctx.user else None,
    )

    # 5. Re-enqueue documents that previously failed due to missing fields.
    if regenerate:
        failed_res = (
            sb.table("practice_documents")
            .select("template_code")
            .eq("practice_id", str(practice_id))
            .eq("tenant_id", tenant_id)
            .not_.is_("generation_error", "null")
            .execute()
        )
        codes = [r["template_code"] for r in (failed_res.data or [])]
        if codes:
            from ..core.queue import enqueue

            await enqueue(
                "practice_generation_task",
                {
                    "practice_id": str(practice_id),
                    "tenant_id": tenant_id,
                    "template_codes": codes,
                },
                job_id=f"practice-gen-retry:{practice_id}",
            )
            log.info(
                "practice.data_collected.regenerate_enqueued",
                practice_id=str(practice_id),
                codes=codes,
            )

    practice = (
        sb.table("practices")
        .select("*")
        .eq("id", str(practice_id))
        .limit(1)
        .execute()
    )
    from ..services.practice_service import Practice as PracticeDataclass

    return PracticeResponse.from_dataclass(
        PracticeDataclass.from_row(practice.data[0])
    )


# ---------------------------------------------------------------------------
# Practice document uploads (Sprint 4 — Claude Vision OCR)
# ---------------------------------------------------------------------------
#
# Surface:
#   POST   /v1/practices/{id}/uploads          (multipart: file + upload_kind)
#   GET    /v1/practices/{id}/uploads          list rows for the practice
#   GET    /v1/practices/{id}/uploads/{up_id}/download → 302 signed URL
#   POST   /v1/practices/{id}/uploads/{up_id}/apply    → write extracted
#                                                       fields to tenant /
#                                                       subject / practice
#   DELETE /v1/practices/{id}/uploads/{up_id}          (storage + row)
#
# Pattern: same as bolletta_uploads (public.py:350) but tenant-scoped via
# CurrentUser and triggered by the operator inside the dashboard, not by
# the lead from a public slug.

UPLOAD_BUCKET = "practice-uploads"
ALLOWED_UPLOAD_MIMES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "application/pdf"}
)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB — matches bucket policy

VALID_UPLOAD_KINDS = (
    "visura_cciaa",
    "visura_catastale",
    "documento_identita",
    "bolletta_pod",
    "altro",
)


def _ext_from_mime(mime: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "application/pdf": "pdf",
    }.get(mime, "bin")


class PracticeUploadResponse(BaseModel):
    id: str
    practice_id: str
    upload_kind: str
    storage_path: str
    original_name: str
    mime_type: str
    file_size_bytes: int
    extraction_status: str
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    extraction_error: str | None = None
    extracted_at: str | None = None
    applied_at: str | None = None
    applied_targets: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> PracticeUploadResponse:
        return cls(
            id=str(row["id"]),
            practice_id=str(row["practice_id"]),
            upload_kind=row["upload_kind"],
            storage_path=row["storage_path"],
            original_name=row["original_name"],
            mime_type=row["mime_type"],
            file_size_bytes=int(row["file_size_bytes"]),
            extraction_status=row["extraction_status"],
            extracted_data=row.get("extracted_data") or {},
            confidence=(
                float(row["confidence"]) if row.get("confidence") is not None else None
            ),
            extraction_error=row.get("extraction_error"),
            extracted_at=row.get("extracted_at"),
            applied_at=row.get("applied_at"),
            applied_targets=row.get("applied_targets") or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@router.post(
    "/practices/{practice_id}/uploads",
    response_model=PracticeUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_practice_document(
    ctx: CurrentUser,
    practice_id: UUID,
    file: UploadFile = File(..., description="Documento (PDF o immagine)"),
    upload_kind: str = Form(..., description="Tipo di documento"),
) -> PracticeUploadResponse:
    """Persist a customer-supplied document and queue Claude Vision OCR.

    Returns immediately with extraction_status='pending'.  The OCR
    runs in arq; the dashboard polls /uploads to surface results.
    """
    tenant_id = require_tenant(ctx)
    if upload_kind not in VALID_UPLOAD_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"upload_kind must be one of: {', '.join(VALID_UPLOAD_KINDS)}",
        )

    # ---- 1. Validate practice ownership (RLS would catch a hostile JWT,
    #         but a 404 here gives a clean error before the storage write).
    from ..core.supabase_client import get_service_client

    sb = get_service_client()
    practice_res = (
        sb.table("practices")
        .select("id, tenant_id")
        .eq("id", str(practice_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    if not practice_res.data:
        raise HTTPException(status_code=404, detail="Practice not found")

    # ---- 2. Validate file
    body = await file.read()
    if len(body) == 0:
        raise HTTPException(status_code=400, detail="File vuoto")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File troppo grande (max {MAX_UPLOAD_BYTES // 1024 // 1024} MB)",
        )

    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_UPLOAD_MIMES:
        raise HTTPException(
            status_code=415,
            detail=f"Tipo file non supportato: {mime or 'unknown'}",
        )

    # ---- 3. Insert row first (so we have an id) then upload to storage,
    #         finally enqueue. If storage fails we delete the row to avoid
    #         orphan rows pointing at non-existent bytes.
    from uuid import uuid4

    upload_id = str(uuid4())
    ext = _ext_from_mime(mime)
    storage_path = f"{tenant_id}/{practice_id}/{upload_id}.{ext}"

    insert_row = {
        "id": upload_id,
        "tenant_id": str(tenant_id),
        "practice_id": str(practice_id),
        "uploaded_by": str(ctx["sub"]) if ctx.get("sub") else None,
        "storage_path": storage_path,
        "original_name": file.filename or "upload",
        "mime_type": mime,
        "file_size_bytes": len(body),
        "upload_kind": upload_kind,
        "extraction_status": "pending",
    }
    sb.table("practice_uploads").insert(insert_row).execute()

    try:
        sb.storage.from_(UPLOAD_BUCKET).upload(
            storage_path,
            body,
            {"content-type": mime, "upsert": "false"},
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "practice.upload.storage_failed",
            practice_id=str(practice_id),
            err=str(exc),
        )
        # Best-effort cleanup of the row.
        sb.table("practice_uploads").delete().eq("id", upload_id).execute()
        raise HTTPException(
            status_code=502, detail="Upload su storage fallito — riprova"
        ) from exc

    # ---- 4. Enqueue OCR
    try:
        from ..core.queue import enqueue

        await enqueue(
            "extract_practice_upload_task",
            {"upload_id": upload_id, "tenant_id": str(tenant_id)},
            job_id=f"practice-extract:{upload_id}",
        )
    except Exception as exc:  # noqa: BLE001
        # Don't fail the request — the row exists and the operator can
        # re-trigger extraction from the dashboard.
        log.warning(
            "practice.upload.enqueue_failed",
            upload_id=upload_id,
            err=str(exc),
        )

    # Re-read so we return canonical row (timestamps).
    fresh = (
        sb.table("practice_uploads")
        .select("*")
        .eq("id", upload_id)
        .limit(1)
        .execute()
    )
    return PracticeUploadResponse.from_row(fresh.data[0])


@router.get(
    "/practices/{practice_id}/uploads",
    response_model=list[PracticeUploadResponse],
)
async def list_practice_uploads(
    ctx: CurrentUser, practice_id: UUID
) -> list[PracticeUploadResponse]:
    """List uploads for a practice, newest first."""
    tenant_id = require_tenant(ctx)
    from ..core.supabase_client import get_service_client

    sb = get_service_client()
    res = (
        sb.table("practice_uploads")
        .select("*")
        .eq("practice_id", str(practice_id))
        .eq("tenant_id", str(tenant_id))
        .order("created_at", desc=True)
        .execute()
    )
    return [PracticeUploadResponse.from_row(r) for r in (res.data or [])]


@router.get("/practices/{practice_id}/uploads/{upload_id}/download")
async def download_practice_upload(
    ctx: CurrentUser, practice_id: UUID, upload_id: UUID
) -> RedirectResponse:
    """Redirect to a 1-hour signed URL for the original uploaded file."""
    tenant_id = require_tenant(ctx)
    from ..core.supabase_client import get_service_client

    sb = get_service_client()
    res = (
        sb.table("practice_uploads")
        .select("storage_path, tenant_id, practice_id")
        .eq("id", str(upload_id))
        .eq("practice_id", str(practice_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Upload not found")
    signed = sign_url(UPLOAD_BUCKET, rows[0]["storage_path"], expires_in=3600)
    return RedirectResponse(url=signed, status_code=302)


@router.delete(
    "/practices/{practice_id}/uploads/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_practice_upload(
    ctx: CurrentUser, practice_id: UUID, upload_id: UUID
) -> Response:
    """Remove the row + the storage object."""
    tenant_id = require_tenant(ctx)
    from ..core.supabase_client import get_service_client

    sb = get_service_client()
    res = (
        sb.table("practice_uploads")
        .select("storage_path")
        .eq("id", str(upload_id))
        .eq("practice_id", str(practice_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Upload not found")
    try:
        sb.storage.from_(UPLOAD_BUCKET).remove([rows[0]["storage_path"]])
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "practice.upload.storage_remove_failed",
            upload_id=str(upload_id),
            err=str(exc),
        )
    sb.table("practice_uploads").delete().eq("id", str(upload_id)).execute()
    # Bare 204 — return Response so FastAPI doesn't try to serialise None
    # against the (default) response_model of NoneType.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ApplyUploadRequest(BaseModel):
    """Optional knobs for the apply endpoint.

    visura_target controls where a visura_cciaa lands:
      'subject' (default) → cliente fields, 'tenant' → installer's own.
    """

    visura_target: str = Field(default="subject", pattern="^(subject|tenant)$")


class ApplyUploadResponse(BaseModel):
    upload_id: str
    applied_targets: dict[str, list[str]]
    practice_id: str
    subject_id: str | None = None


@router.post(
    "/practices/{practice_id}/uploads/{upload_id}/apply",
    response_model=ApplyUploadResponse,
)
async def apply_practice_upload(
    ctx: CurrentUser,
    practice_id: UUID,
    upload_id: UUID,
    body: ApplyUploadRequest | None = None,
) -> ApplyUploadResponse:
    """Write the extracted fields to tenant / subject / practice.

    Idempotent on `applied_at`: re-applying the same row updates the
    targets again (caller's choice — useful if the operator manually
    edited the practice after the first apply, then wants to re-apply
    the OCR values).

    Field routing is driven by service-level maps
    (practice_extraction_service.py:VISURA_*_TARGETS / etc.) so this
    handler stays a thin orchestrator.
    """
    tenant_id = require_tenant(ctx)
    from ..core.supabase_client import get_service_client
    from ..services.practice_extraction_service import build_apply_payload

    sb = get_service_client()
    knobs = body or ApplyUploadRequest()

    # ---- 1. Load the upload row
    res = (
        sb.table("practice_uploads")
        .select("*")
        .eq("id", str(upload_id))
        .eq("practice_id", str(practice_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Upload not found")
    upload = rows[0]

    if upload["extraction_status"] not in {"success", "manual_required"}:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot apply: extraction_status={upload['extraction_status']}",
        )

    # ---- 2. Build the per-target payloads
    payload = build_apply_payload(
        upload["upload_kind"],
        upload.get("extracted_data") or {},
        visura_target=knobs.visura_target,  # type: ignore[arg-type]
    )

    applied_targets: dict[str, list[str]] = {}

    # ---- 3. Apply to tenant
    if "tenant" in payload:
        sb.table("tenants").update(payload["tenant"]).eq(
            "id", str(tenant_id)
        ).execute()
        applied_targets["tenant"] = list(payload["tenant"].keys())

    # ---- 4. Apply to subject (resolve subject_id via lead → practice)
    subject_id: str | None = None
    if "subject" in payload:
        prac = (
            sb.table("practices")
            .select("lead_id")
            .eq("id", str(practice_id))
            .limit(1)
            .execute()
        )
        if prac.data:
            lead_id = prac.data[0]["lead_id"]
            lead = (
                sb.table("leads")
                .select("subject_id")
                .eq("id", lead_id)
                .limit(1)
                .execute()
            )
            if lead.data and lead.data[0].get("subject_id"):
                subject_id = lead.data[0]["subject_id"]
                # Whitelist: only update columns that exist on subjects.
                # The map already targets real columns — but we filter
                # anything that the schema rejects to a safe no-op rather
                # than raising 500.
                try:
                    sb.table("subjects").update(payload["subject"]).eq(
                        "id", subject_id
                    ).execute()
                    applied_targets["subject"] = list(payload["subject"].keys())
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "practice.upload.apply.subject_partial",
                        upload_id=str(upload_id),
                        err=str(exc),
                    )

    # ---- 5. Apply to practice (direct cols + extras deep-merge)
    practice_update: dict[str, Any] = {}
    if "practice" in payload:
        practice_update.update(payload["practice"])

    if "extras" in payload:
        # Deep-merge into practices.extras JSONB.
        cur = (
            sb.table("practices")
            .select("extras")
            .eq("id", str(practice_id))
            .limit(1)
            .execute()
        )
        cur_extras = (cur.data[0].get("extras") if cur.data else {}) or {}
        cur_extras = {**cur_extras, **payload["extras"]}
        practice_update["extras"] = cur_extras

    if practice_update:
        sb.table("practices").update(practice_update).eq(
            "id", str(practice_id)
        ).execute()
        applied_targets["practice"] = list(practice_update.keys())

    # ---- 6. Mark upload as applied + emit event
    sb.table("practice_uploads").update(
        {
            "applied_at": "now()",
            "applied_by": str(ctx["sub"]) if ctx.get("sub") else None,
            "applied_targets": applied_targets,
        }
    ).eq("id", str(upload_id)).execute()

    _emit_practice_event(
        tenant_id=str(tenant_id),
        practice_id=str(practice_id),
        event_type=EVT_DATA_COLLECTED,
        payload={
            "source": "ocr_upload",
            "upload_id": str(upload_id),
            "upload_kind": upload["upload_kind"],
            "applied_targets": applied_targets,
        },
        actor_user_id=str(ctx["sub"]) if ctx.get("sub") else None,
    )

    return ApplyUploadResponse(
        upload_id=str(upload_id),
        applied_targets=applied_targets,
        practice_id=str(practice_id),
        subject_id=subject_id,
    )


def _deadline_row_to_response(row: dict[str, Any]) -> PracticeDeadlineResponse:
    """Project a raw practice_deadlines row (optionally embedded with
    practices(*) for the tenant-wide endpoint) into the API shape."""
    embedded = row.get("practices") or {}
    return PracticeDeadlineResponse(
        id=str(row["id"]),
        practice_id=str(row["practice_id"]),
        document_id=(str(row["document_id"]) if row.get("document_id") else None),
        deadline_kind=row["deadline_kind"],
        due_at=row["due_at"],
        status=row["status"],
        satisfied_at=row.get("satisfied_at"),
        metadata=row.get("metadata") or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        practice_number=embedded.get("practice_number"),
        practice_status=embedded.get("status"),
    )
