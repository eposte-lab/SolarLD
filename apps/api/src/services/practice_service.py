"""GSE Practice orchestration service.

Sits between the route layer and the storage/queue/mapper components:

  * ``next_practice_number`` — atomic per-tenant counter, formatted as
    ``{TENANT_ABBR}/{YEAR}/{NNNN}`` (e.g. ``SOLE/2026/0042``).
    Backed by the ``next_practice_seq(uuid)`` RPC defined in 0083.

  * ``create_practice`` — the heavy hitter: validates the lead is
    eligible, snapshots the mapper context, INSERTs the practices row,
    UPSERTs one ``practice_documents`` row per requested template_code
    in ``draft`` state with no PDF yet, then enqueues the fan-out arq
    job. Returns immediately — the worker fills in pdf_url asynchronously.

  * ``regenerate_document`` — re-render a single document. Used after
    the installer fixes data on the practice or when a template_version
    bump invalidates older PDFs.

  * ``render_practice_document`` — the actual sync render path. Called
    from inside the arq worker (which runs in a thread pool); kept here
    so it can also be called inline from tests / one-off scripts
    without spinning up a worker.

The ``practices.UNIQUE(lead_id)`` constraint is what makes the "Crea
pratica" button idempotent: a second click while the first request is
in flight surfaces as a 409 in the route layer, which redirects to the
existing practice instead of creating a duplicate.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client
from .practice_data_mapper import PracticeDataMapper
from .practice_deadlines_service import project_event_to_deadlines
from .practice_events_service import (
    EVT_DOCUMENT_ACCEPTED,
    EVT_DOCUMENT_AMENDED,
    EVT_DOCUMENT_COMPLETED,
    EVT_DOCUMENT_GENERATED,
    EVT_DOCUMENT_GENERATION_FAILED,
    EVT_DOCUMENT_REJECTED,
    EVT_DOCUMENT_REVIEWED,
    EVT_DOCUMENT_SENT,
    EVT_PRACTICE_CREATED,
    record_event,
)
from .practice_pdf_renderer import SUPPORTED_TEMPLATE_CODES, render_practice_pdf
from .storage_service import upload_bytes

log = get_logger(__name__)

# Same bucket as quotes / creative renderings; the dashboard already
# has signed-URL plumbing for it.
RENDERINGS_BUCKET = "renderings"

# Default templates produced when the installer clicks "Crea pratica"
# without selecting custom ones. Sprint 1 ships these two; Sprint 2
# will add tica_*, modello_unico, schema_unifilare, transizione_50.
DEFAULT_TEMPLATE_CODES: tuple[str, ...] = ("dm_37_08", "comunicazione_comune")


# Map document.status values → the event_type to emit on transition.
# 'draft' is intentionally absent — going *back* to draft is what
# regenerate / record_generation_failure produce, both of which already
# emit specific events themselves.
_DOC_STATUS_TO_EVENT: dict[str, str] = {
    "reviewed": EVT_DOCUMENT_REVIEWED,
    "sent": EVT_DOCUMENT_SENT,
    "accepted": EVT_DOCUMENT_ACCEPTED,
    "rejected": EVT_DOCUMENT_REJECTED,
    "amended": EVT_DOCUMENT_AMENDED,
    "completed": EVT_DOCUMENT_COMPLETED,
}


def _emit_practice_event(
    *,
    tenant_id: str | UUID,
    practice_id: str | UUID,
    event_type: str,
    document_id: str | UUID | None = None,
    payload: dict[str, Any] | None = None,
    actor_user_id: str | UUID | None = None,
) -> None:
    """Record an event AND project it onto deadlines.

    Wraps record_event so callers don't have to remember to call the
    deadlines projection.  Best-effort — failures are logged inside
    record_event / project_event_to_deadlines and never bubble up.
    """
    event = record_event(
        tenant_id=tenant_id,
        practice_id=practice_id,
        event_type=event_type,
        document_id=document_id,
        payload=payload,
        actor_user_id=actor_user_id,
    )
    if event is None:
        return
    try:
        project_event_to_deadlines(event)
    except Exception:
        log.exception(
            "practice.deadline_projection_failed",
            practice_id=str(practice_id),
            event_type=event_type,
        )


# ---------------------------------------------------------------------------
# Public dataclasses — what the API returns to the dashboard.
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Practice:
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
    data_snapshot: dict[str, Any]
    # Sprint 2: template-specific JSONB (IBAN, regime ritiro, codice
    # identificativo connessione, transizione50.tep_anno, ...).
    extras: dict[str, Any]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Practice":
        return cls(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            lead_id=str(row["lead_id"]),
            quote_id=(str(row["quote_id"]) if row.get("quote_id") else None),
            practice_number=row["practice_number"],
            practice_seq=int(row["practice_seq"]),
            status=row["status"],
            impianto_potenza_kw=float(row["impianto_potenza_kw"] or 0),
            impianto_pannelli_count=row.get("impianto_pannelli_count"),
            impianto_pod=row.get("impianto_pod"),
            impianto_distributore=row["impianto_distributore"],
            impianto_data_inizio_lavori=row.get("impianto_data_inizio_lavori"),
            impianto_data_fine_lavori=row.get("impianto_data_fine_lavori"),
            catastale_foglio=row.get("catastale_foglio"),
            catastale_particella=row.get("catastale_particella"),
            catastale_subalterno=row.get("catastale_subalterno"),
            componenti_data=row.get("componenti_data") or {},
            data_snapshot=row.get("data_snapshot") or {},
            extras=row.get("extras") or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(slots=True, frozen=True)
class PracticeDocument:
    id: str
    practice_id: str
    tenant_id: str
    template_code: str
    template_version: str
    status: str
    pdf_url: str | None
    pdf_storage_path: str | None
    auto_data_snapshot: dict[str, Any]
    manual_data: dict[str, Any]
    generation_error: str | None
    generated_at: str | None
    sent_at: str | None
    accepted_at: str | None
    rejected_at: str | None
    rejection_reason: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PracticeDocument":
        return cls(
            id=str(row["id"]),
            practice_id=str(row["practice_id"]),
            tenant_id=str(row["tenant_id"]),
            template_code=row["template_code"],
            template_version=row["template_version"],
            status=row["status"],
            pdf_url=row.get("pdf_url"),
            pdf_storage_path=row.get("pdf_storage_path"),
            auto_data_snapshot=row.get("auto_data_snapshot") or {},
            manual_data=row.get("manual_data") or {},
            generation_error=row.get("generation_error"),
            generated_at=row.get("generated_at"),
            sent_at=row.get("sent_at"),
            accepted_at=row.get("accepted_at"),
            rejected_at=row.get("rejected_at"),
            rejection_reason=row.get("rejection_reason"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# Numbering — RPC backed
# ---------------------------------------------------------------------------


def next_practice_number(tenant_id: str | UUID) -> tuple[str, int]:
    """Allocate the next ``{ABBR}/{YEAR}/{NNNN}`` for the tenant.

    ``ABBR`` is the first 4 sanitized uppercase characters of the tenant's
    business_name (or ``PRA`` as a hard fallback when the name has fewer
    than 4 alphanumeric chars — vanishingly rare in production, but we
    don't want a NULL or "" ABBR causing UNIQUE constraint surprises).
    """
    sb = get_service_client()

    # 1. Resolve abbreviation. We re-read business_name on every call
    #    rather than caching — tenant renames are rare but we want the
    #    next number minted *after* a rename to use the new abbr.
    tenant_res = (
        sb.table("tenants")
        .select("business_name")
        .eq("id", str(tenant_id))
        .limit(1)
        .execute()
    )
    business_name = (tenant_res.data or [{}])[0].get("business_name") or ""
    abbr = _tenant_abbr(business_name)

    # 2. Atomic increment via RPC (defined in 0083).
    res = sb.rpc("next_practice_seq", {"p_tenant_id": str(tenant_id)}).execute()
    seq = int(res.data) if isinstance(res.data, int) else int(res.data or 0)
    if seq <= 0:
        # Defensive: 0 would silently mint duplicates. Fail loud.
        raise RuntimeError(
            f"next_practice_seq returned non-positive seq: {res.data!r}"
        )

    year = datetime.now(timezone.utc).year
    return f"{abbr}/{year}/{seq:04d}", seq


# ---------------------------------------------------------------------------
# Eligibility / draft preview
# ---------------------------------------------------------------------------


def get_draft_preview(
    *, lead_id: str | UUID, tenant_id: str | UUID
) -> dict[str, Any]:
    """Return everything the "Crea pratica" modal needs to pre-populate.

    Shape:
        {
            "eligible": bool,           # lead.feedback == 'contract_signed'
            "has_existing": bool,       # 1-pratica-per-lead enforced
            "existing_practice_id": str | None,
            "missing_tenant_fields": list[str],   # required for DM 37/08
            "suggested_practice_number": str,
            "prefill": {
                "impianto_potenza_kw": float,
                "impianto_pannelli_count": int | None,
                "componenti": {...},     # from lead_quotes.manual_fields.tech_*
                "ubicazione": {...},     # from roof
            },
        }

    Doesn't allocate a seq counter — the suggestion is a UI hint, the
    actual number is allocated at create time. This keeps draft cheap
    (no RPC) and idempotent.
    """
    sb = get_service_client()

    # 1. Lead + joined subject/roof/most-recent quote.
    lead_res = (
        sb.table("leads")
        .select("*, subjects(*), roofs(*)")
        .eq("id", str(lead_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    if not lead_res.data:
        raise ValueError(f"lead {lead_id} not found for tenant {tenant_id}")
    lead = lead_res.data[0]
    roof = lead.get("roofs") or {}

    eligible = (lead.get("feedback") or "") == "contract_signed"

    # 2. Existing practice (1-per-lead enforced by UNIQUE constraint).
    existing_res = (
        sb.table("practices")
        .select("id, practice_number, status")
        .eq("lead_id", str(lead_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    existing = existing_res.data[0] if existing_res.data else None

    # 3. Most recent issued quote (for tech_* prefill).
    quote_res = (
        sb.table("lead_quotes")
        .select("id, manual_fields")
        .eq("lead_id", str(lead_id))
        .eq("tenant_id", str(tenant_id))
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    quote = quote_res.data[0] if quote_res.data else None
    manual = (quote or {}).get("manual_fields") or {}

    # 4. Tenant legal-fields gap (for the DM 37/08 banner).
    tenant_res = (
        sb.table("tenants")
        .select(
            "business_name, codice_fiscale, numero_cciaa, "
            "responsabile_tecnico_nome, responsabile_tecnico_cognome, "
            "responsabile_tecnico_qualifica, responsabile_tecnico_iscrizione_albo"
        )
        .eq("id", str(tenant_id))
        .limit(1)
        .execute()
    )
    t = tenant_res.data[0] if tenant_res.data else {}
    missing_tenant_fields: list[str] = []
    if not t.get("codice_fiscale"):
        missing_tenant_fields.append("Codice fiscale azienda")
    if not t.get("numero_cciaa"):
        missing_tenant_fields.append("Numero CCIAA")
    if not t.get("responsabile_tecnico_nome"):
        missing_tenant_fields.append("Nome responsabile tecnico")
    if not t.get("responsabile_tecnico_cognome"):
        missing_tenant_fields.append("Cognome responsabile tecnico")
    if not t.get("responsabile_tecnico_qualifica"):
        missing_tenant_fields.append("Qualifica responsabile tecnico")
    if not t.get("responsabile_tecnico_iscrizione_albo"):
        missing_tenant_fields.append("Iscrizione albo responsabile tecnico")

    # 5. Suggested practice number (read-only — we peek at last_seq + 1
    #    but DON'T increment). This means two simultaneous drafts could
    #    see the same suggestion; the actual number on save is always
    #    distinct because of the RPC's atomic increment.
    counter_res = (
        sb.table("tenant_practice_counters")
        .select("last_seq")
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    last_seq = (counter_res.data or [{"last_seq": 0}])[0]["last_seq"] or 0
    abbr = _tenant_abbr(t.get("business_name") or "")
    year = datetime.now(timezone.utc).year
    suggested_number = f"{abbr}/{year}/{(last_seq + 1):04d}"

    # 6. Prefill from quote.manual_fields (tech_*) when available; the
    #    form in the dashboard merges this with whatever the user types.
    prefill_componenti = {
        "pannelli": {
            "marca": manual.get("tech_marca_pannelli"),
            "modello": manual.get("tech_modello_pannelli"),
            "potenza_w": manual.get("tech_potenza_singolo_pannello"),
            "garanzia_anni": manual.get("tech_garanzia_pannelli_anni"),
        },
        "inverter": {
            "marca": manual.get("tech_marca_inverter"),
            "modello": manual.get("tech_modello_inverter"),
            "garanzia_anni": manual.get("tech_garanzia_inverter_anni"),
        },
        "accumulo": {
            "presente": bool(manual.get("tech_accumulo_incluso")),
        },
    }

    # 7. Distributore guess from CAP (Sprint 1 mini-table; Sprint 2 will
    #    expand). Roma=areti, Milano=unareti, fallback=e_distribuzione.
    cap = (roof.get("cap") or "").strip()
    suggested_distributore = _guess_distributore(cap)

    return {
        "eligible": eligible,
        "has_existing": existing is not None,
        "existing_practice_id": existing["id"] if existing else None,
        "existing_practice_number": existing["practice_number"]
        if existing
        else None,
        "missing_tenant_fields": missing_tenant_fields,
        "suggested_practice_number": suggested_number,
        "prefill": {
            "impianto_potenza_kw": _to_float(roof.get("estimated_kwp")),
            "impianto_pannelli_count": roof.get("estimated_panel_count")
            or manual.get("tech_pannelli_quantita"),
            "impianto_distributore": suggested_distributore,
            "componenti": prefill_componenti,
            "ubicazione": {
                "indirizzo": roof.get("address") or "",
                "cap": roof.get("cap") or "",
                "comune": roof.get("comune") or "",
                "provincia": roof.get("provincia") or "",
            },
        },
        "quote_id": (quote or {}).get("id"),
    }


# ---------------------------------------------------------------------------
# Create / list / get
# ---------------------------------------------------------------------------


async def create_practice(
    *,
    tenant_id: str | UUID,
    lead_id: str | UUID,
    payload: dict[str, Any],
    template_codes: list[str] | None = None,
) -> Practice:
    """Create a practice + N draft documents and enqueue the fan-out job.

    ``payload`` carries the form data: impianto_*, catastale_*,
    componenti, optional quote_id. Validated minimally here — the
    template-time validate_for_template gate is what gates DM 37/08.

    Idempotency: ``practices.UNIQUE(lead_id)`` raises an integrity error
    on the second concurrent request. The route layer turns that into a
    409 "practice already exists, see /practices/{id}".

    Async because we await ``enqueue()``. The DB writes are sync (Supabase
    SDK) but cheap (sub-200 ms p95) — running them inline keeps the
    function easy to reason about.
    """
    template_codes = template_codes or list(DEFAULT_TEMPLATE_CODES)
    # Reject unknown codes early — don't INSERT a row whose worker would
    # only fail.
    for code in template_codes:
        if code not in SUPPORTED_TEMPLATE_CODES:
            raise ValueError(f"unsupported template_code: {code!r}")

    sb = get_service_client()

    # 1. Verify lead exists and belongs to tenant. We don't gate on
    #    feedback='contract_signed' here — the dashboard already does
    #    that, and a future "Crea pratica anticipata" flow may want to
    #    bypass it.
    lead_res = (
        sb.table("leads")
        .select("id, feedback")
        .eq("id", str(lead_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    if not lead_res.data:
        raise ValueError(f"lead {lead_id} not found for tenant {tenant_id}")

    # 2. Allocate number BEFORE the practices INSERT so the tenant's
    #    counter increments even if the INSERT fails the lead-uniqueness
    #    check. Wasting a seq number is fine — gaps are expected and
    #    have no semantic meaning.
    practice_number, seq = next_practice_number(tenant_id)

    # 3. INSERT the practices row. Pull the keys from payload and let
    #    Postgres validate against the CHECK constraints (distributore,
    #    status enum). The data_snapshot is filled later (after we have
    #    the practice id and can run the mapper).
    insert_payload: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "lead_id": str(lead_id),
        "quote_id": payload.get("quote_id"),
        "practice_number": practice_number,
        "practice_seq": seq,
        "status": "in_preparation",
        "impianto_potenza_kw": payload.get("impianto_potenza_kw") or 0,
        "impianto_pannelli_count": payload.get("impianto_pannelli_count"),
        "impianto_pod": payload.get("impianto_pod"),
        # Default to e_distribuzione — the form should send a value, but
        # default keeps the CHECK constraint happy in API misuse cases.
        "impianto_distributore": payload.get("impianto_distributore")
        or "e_distribuzione",
        "impianto_data_inizio_lavori": payload.get("impianto_data_inizio_lavori"),
        "impianto_data_fine_lavori": payload.get("impianto_data_fine_lavori"),
        "catastale_foglio": payload.get("catastale_foglio"),
        "catastale_particella": payload.get("catastale_particella"),
        "catastale_subalterno": payload.get("catastale_subalterno"),
        "componenti_data": payload.get("componenti_data") or {},
        # Sprint 2: template-specific JSONB (IBAN, regime ritiro, codice
        # identificativo connessione, qualita richiedente, transizione50.*).
        # Schema documented in practice_data_mapper.py EXTRAS_SHAPE.
        "extras": payload.get("extras") or {},
        # data_snapshot populated below once we can build the mapper.
    }
    insert_res = sb.table("practices").insert(insert_payload).execute()
    if not insert_res.data:
        raise RuntimeError("practices insert returned no rows")
    practice_row = insert_res.data[0]
    practice_id = practice_row["id"]

    # 4. Snapshot the full mapper context. Done AFTER the INSERT so the
    #    mapper can find the practice row. We update with the snapshot
    #    rather than including it in the INSERT — minor extra round-trip,
    #    but avoids running the mapper twice.
    try:
        mapper = PracticeDataMapper(practice_id, tenant_id)
        snapshot = mapper.get_full_context()
        sb.table("practices").update({"data_snapshot": snapshot}).eq(
            "id", practice_id
        ).execute()
        practice_row["data_snapshot"] = snapshot
    except Exception:
        # Snapshot failure shouldn't block creation — the worker will
        # rebuild the context from scratch when it renders. Log loud
        # because it indicates a JOIN/data issue we want to investigate.
        log.exception(
            "practice.snapshot_failed",
            practice_id=str(practice_id),
            tenant_id=str(tenant_id),
        )

    # 5. Pre-create the practice_documents rows in 'draft' so the
    #    dashboard can render the document list immediately — even
    #    before the worker has produced any PDFs.
    for code in template_codes:
        sb.table("practice_documents").upsert(
            {
                "practice_id": practice_id,
                "tenant_id": str(tenant_id),
                "template_code": code,
                "template_version": "v1",
                "status": "draft",
            },
            on_conflict="practice_id,template_code",
        ).execute()

    # 6. Enqueue the fan-out job. The job_id is stable (practice + commit
    #    burst) so a duplicate POST collapses into one queue entry.
    await enqueue(
        "practice_generation_task",
        {
            "practice_id": str(practice_id),
            "tenant_id": str(tenant_id),
            "template_codes": template_codes,
        },
        job_id=f"practice-gen:{practice_id}",
    )

    # 7. Audit-log: practice creation goes on the event log.  Triggers
    #    no deadlines (those open on document_sent) but populates the
    #    timeline UI from t=0.
    _emit_practice_event(
        tenant_id=tenant_id,
        practice_id=practice_id,
        event_type=EVT_PRACTICE_CREATED,
        payload={
            "practice_number": practice_number,
            "template_codes": template_codes,
            "lead_id": str(lead_id),
        },
    )

    log.info(
        "practice.created",
        tenant_id=str(tenant_id),
        lead_id=str(lead_id),
        practice_id=str(practice_id),
        practice_number=practice_number,
        template_codes=template_codes,
    )
    return Practice.from_row(practice_row)


def list_practices(
    *,
    tenant_id: str | UUID,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Tenant-scoped list with embedded document summaries.

    Returns row dicts (not Practice dataclasses) because the dashboard
    list view needs a few extras the dataclass doesn't carry — like
    counts of (ready / total) documents — and constructing those on
    every from_row would be wasteful.
    """
    sb = get_service_client()
    q = (
        sb.table("practices")
        .select(
            "id, practice_number, practice_seq, status, lead_id, "
            "impianto_potenza_kw, impianto_distributore, created_at, updated_at, "
            "leads:lead_id(id, subjects(business_name, owner_first_name, owner_last_name)), "
            "practice_documents(id, template_code, status, pdf_url, sent_at, generated_at)"
        )
        .eq("tenant_id", str(tenant_id))
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
    )
    if status_filter:
        q = q.eq("status", status_filter)
    res = q.execute()
    return res.data or []


def get_practice(
    *, practice_id: str | UUID, tenant_id: str | UUID
) -> dict[str, Any] | None:
    """Detail view: practice + documents + lead + subject. Returns row dict."""
    sb = get_service_client()
    res = (
        sb.table("practices")
        .select(
            "*, "
            "practice_documents(*), "
            "leads:lead_id(id, public_slug, feedback, "
            "subjects(business_name, owner_first_name, owner_last_name, vat_number))"
        )
        .eq("id", str(practice_id))
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0]


def get_document(
    *, practice_id: str | UUID, template_code: str, tenant_id: str | UUID
) -> PracticeDocument | None:
    sb = get_service_client()
    res = (
        sb.table("practice_documents")
        .select("*")
        .eq("practice_id", str(practice_id))
        .eq("template_code", template_code)
        .eq("tenant_id", str(tenant_id))
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return PracticeDocument.from_row(res.data[0])


# ---------------------------------------------------------------------------
# Render — sync core, async wrapper for the worker
# ---------------------------------------------------------------------------


def render_practice_document(
    *, practice_id: str | UUID, template_code: str, tenant_id: str | UUID
) -> PracticeDocument:
    """Sync render path: build context, render PDF, upload, UPSERT row.

    The arq task wraps this in ``asyncio.to_thread``. Tests can call
    it directly — no Redis or worker needed.

    Validation: if the mapper reports missing required fields, we still
    persist a document row but mark ``status='draft'`` with a populated
    ``generation_error`` so the dashboard can surface "compila i campi
    legali tenant" without a special case path.
    """
    sb = get_service_client()

    # 1. Build context (read-side via mapper).
    mapper = PracticeDataMapper(practice_id, tenant_id)
    missing = mapper.validate_for_template(template_code)
    if missing:
        # Don't render — store the gap as the error and bail.
        error = "Campi mancanti: " + ", ".join(missing)
        upsert_payload = {
            "practice_id": str(practice_id),
            "tenant_id": str(tenant_id),
            "template_code": template_code,
            "template_version": "v1",
            "status": "draft",
            "generation_error": error,
        }
        res = (
            sb.table("practice_documents")
            .upsert(upsert_payload, on_conflict="practice_id,template_code")
            .execute()
        )
        return PracticeDocument.from_row(res.data[0])

    context = mapper.get_full_context()

    # 2. Render. WeasyPrint is sync + CPU-heavy; the arq task already
    #    runs us inside a thread, so we can call it directly.
    pdf_bytes = render_practice_pdf(template_code, context)

    # 3. Upload to renderings/{tenant}/{practice}/{template_code}.pdf.
    #    Stable path → re-renders overwrite (upsert=True) so the signed
    #    URL the dashboard already has stays valid.
    pdf_path = f"practices/{tenant_id}/{practice_id}/{template_code}.pdf"
    pdf_url = upload_bytes(
        RENDERINGS_BUCKET,
        pdf_path,
        pdf_bytes,
        content_type="application/pdf",
        upsert=True,
    )

    # 4. UPSERT the document row. ``generated_at`` set fresh on each
    #    render so re-generation timestamps are visible in the UI.
    now_iso = datetime.now(timezone.utc).isoformat()
    upsert_payload = {
        "practice_id": str(practice_id),
        "tenant_id": str(tenant_id),
        "template_code": template_code,
        "template_version": "v1",
        "status": "draft",  # awaiting installer review
        "pdf_url": pdf_url,
        "pdf_storage_path": pdf_path,
        "auto_data_snapshot": context,
        "generation_error": None,
        "generated_at": now_iso,
    }
    res = (
        sb.table("practice_documents")
        .upsert(upsert_payload, on_conflict="practice_id,template_code")
        .execute()
    )
    log.info(
        "practice_document.rendered",
        practice_id=str(practice_id),
        tenant_id=str(tenant_id),
        template_code=template_code,
        pdf_size=len(pdf_bytes),
    )

    # Event log + deadline projection.  document_generated doesn't
    # currently trigger any deadlines (those fire on `sent`), but it
    # populates the timeline so the user sees the PDF appear.
    document_row = res.data[0]
    _emit_practice_event(
        tenant_id=tenant_id,
        practice_id=practice_id,
        event_type=EVT_DOCUMENT_GENERATED,
        document_id=document_row["id"],
        payload={
            "template_code": template_code,
            "template_version": "v1",
            "pdf_size_bytes": len(pdf_bytes),
        },
    )
    return PracticeDocument.from_row(document_row)


async def regenerate_document(
    *, practice_id: str | UUID, template_code: str, tenant_id: str | UUID
) -> PracticeDocument:
    """Async wrapper: kick the render off the event loop.

    Returns the (possibly partially-updated) row. If the worker is busy
    or Redis is slow, the route can fall back to enqueueing — but for
    the common case we render inline so the user gets the new PDF as
    soon as the request completes.
    """
    if template_code not in SUPPORTED_TEMPLATE_CODES:
        raise ValueError(f"unsupported template_code: {template_code!r}")
    return await asyncio.to_thread(
        render_practice_document,
        practice_id=practice_id,
        template_code=template_code,
        tenant_id=tenant_id,
    )


def update_document_status(
    *,
    practice_id: str | UUID,
    template_code: str,
    tenant_id: str | UUID,
    status: str | None = None,
    manual_data: dict[str, Any] | None = None,
    rejection_reason: str | None = None,
) -> PracticeDocument | None:
    """Patch endpoint backend: status transition + free-form manual_data.

    Status transitions are enforced at API level via the CHECK enum on
    practice_documents.status. We don't enforce a state machine here
    (e.g. draft → reviewed → sent → accepted) — the dashboard buttons
    determine what's reachable from each state.
    """
    sb = get_service_client()
    update: dict[str, Any] = {}
    if status:
        update["status"] = status
        # Set the appropriate timestamp column when transitioning.
        now_iso = datetime.now(timezone.utc).isoformat()
        if status == "sent":
            update["sent_at"] = now_iso
        elif status == "accepted":
            update["accepted_at"] = now_iso
        elif status == "rejected":
            update["rejected_at"] = now_iso
            if rejection_reason:
                update["rejection_reason"] = rejection_reason
    if manual_data is not None:
        update["manual_data"] = manual_data

    if not update:
        # No-op call — return the existing row.
        return get_document(
            practice_id=practice_id,
            template_code=template_code,
            tenant_id=tenant_id,
        )

    res = (
        sb.table("practice_documents")
        .update(update)
        .eq("practice_id", str(practice_id))
        .eq("template_code", template_code)
        .eq("tenant_id", str(tenant_id))
        .execute()
    )
    if not res.data:
        return None

    # Event log + deadline projection.  Only emit when the status
    # actually changed to a known event-emitting value — manual_data-only
    # patches don't add timeline noise.
    if status and status in _DOC_STATUS_TO_EVENT:
        document_id = res.data[0]["id"]
        event_payload: dict[str, Any] = {"template_code": template_code}
        if status == "rejected" and rejection_reason:
            event_payload["rejection_reason"] = rejection_reason
        _emit_practice_event(
            tenant_id=tenant_id,
            practice_id=practice_id,
            event_type=_DOC_STATUS_TO_EVENT[status],
            document_id=document_id,
            payload=event_payload,
        )
    return PracticeDocument.from_row(res.data[0])


def record_generation_failure(
    *,
    practice_id: str | UUID,
    tenant_id: str | UUID,
    template_code: str,
    error: str,
) -> None:
    """Persist a worker failure on the document row.

    Called from the worker's except path so the dashboard can show the
    error and offer "Rigenera". Best-effort — if the UPSERT itself
    fails, the worker logs and moves on (the next "Rigenera" click will
    overwrite either way).
    """
    sb = get_service_client()
    try:
        sb.table("practice_documents").upsert(
            {
                "practice_id": str(practice_id),
                "tenant_id": str(tenant_id),
                "template_code": template_code,
                "template_version": "v1",
                "status": "draft",
                "generation_error": error[:1000],  # cap to avoid runaway rows
            },
            on_conflict="practice_id,template_code",
        ).execute()
    except Exception:
        log.exception(
            "practice_document.failure_persistence_failed",
            practice_id=str(practice_id),
            template_code=template_code,
        )

    # Event log — keep going even if the persist above failed; the
    # event itself is independent of the document row state.
    _emit_practice_event(
        tenant_id=tenant_id,
        practice_id=practice_id,
        event_type=EVT_DOCUMENT_GENERATION_FAILED,
        payload={
            "template_code": template_code,
            "error": error[:500],
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tenant_abbr(business_name: str) -> str:
    """First 4 alphanumeric uppercase chars of the tenant name.

    "Sole Energy SRL" → "SOLE". "AB" → "ABPR" (padded with the safe
    fallback). Empty / all-symbols → "PRA" (= practice, distinguishable
    from quote's no-fallback path).
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "", business_name or "").upper()
    if not cleaned:
        return "PRA"
    if len(cleaned) >= 4:
        return cleaned[:4]
    # Pad short names with PRA suffix to always reach 4 chars.
    return (cleaned + "PRA")[:4]


def _guess_distributore(cap: str) -> str:
    """Sprint 1 mini-table for distributor inference from postal code.

    Sprint 2 will load a real CAP→distributore dataset. For now this is
    the 80/20 covering Roma + Milano + the rest defaulting to E-Distribuzione
    (the national incumbent that still owns ~85% of low-voltage POD).
    """
    if not cap:
        return "e_distribuzione"
    # Roma area: 00010-00199.
    if cap.startswith("00") and len(cap) == 5:
        return "areti"
    # Milano area: 20100-20162.
    if cap.startswith("201") and len(cap) == 5:
        return "unareti"
    return "e_distribuzione"


def _to_float(val: Any) -> float:
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
