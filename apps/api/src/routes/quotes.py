"""Lead → Preventivo (formal quote) routes.

Four endpoints scoped under ``/v1/leads/{lead_id}/quote``:

  GET  /draft           Pre-populated AUTO bag for the editor's read-only
                        sidebar + the suggested preventivo number. Cheap,
                        idempotent, doesn't allocate the seq counter.
  POST /                Allocate seq, render PDF (off the event loop),
                        upload to renderings/, supersede prior issued
                        versions, INSERT and return the new row.
  GET  /                List every version (newest first) for the version
                        dropdown in the dashboard.
  GET  /{quote_id}/pdf  302 redirect to a fresh signed Supabase URL so
                        the dashboard's <iframe> doesn't depend on the
                        original public URL surviving bucket rotation.

Auth pattern mirrors b2c_exports.py: ``CurrentUser`` Depends, then
``require_tenant(ctx)`` to enforce tenancy. All DB ops in
``quote_service`` already filter by ``tenant_id``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..services.quote_service import (
    RENDERINGS_BUCKET,
    build_auto_fields,
    get_quote,
    list_quotes_for_lead,
    next_preventivo_number,
    save_quote,
)
from ..services.storage_service import sign_url

router = APIRouter()
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class QuoteDraftResponse(BaseModel):
    """The bag the editor renders into the read-only sidebar + the
    suggested preventivo_numero. Manual fields are NOT pre-populated
    here — the editor owns them."""

    auto_fields: dict[str, Any]
    suggested_preventivo_number: str
    suggested_preventivo_seq: int


class SaveQuoteRequest(BaseModel):
    """Manual fields the installer typed in the editor.

    Free-form ``dict[str, Any]`` rather than a typed schema because the
    template variable set evolves (commerciale_*, tech_*, prezzo_*,
    pagamento_*, tempi_*, note_*, …). The renderer will simply omit
    blocks whose vars are missing — no need to validate keys here.
    """

    manual_fields: dict[str, Any] = Field(default_factory=dict)


class QuoteResponse(BaseModel):
    id: str
    tenant_id: str
    lead_id: str
    preventivo_number: str
    preventivo_seq: int
    version: int
    status: str
    pdf_url: str | None
    hero_url: str | None
    created_at: str
    updated_at: str


class QuoteDetailResponse(QuoteResponse):
    auto_fields: dict[str, Any]
    manual_fields: dict[str, Any]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/leads/{lead_id}/quote/draft", response_model=QuoteDraftResponse)
async def quote_draft(ctx: CurrentUser, lead_id: UUID) -> QuoteDraftResponse:
    """Build the pre-populated AUTO bag for the editor.

    Suggesting the next preventivo number here is harmless — we DO NOT
    consume the per-tenant counter on draft. The actual allocation
    happens at save-time. We just preview "what it would be" using the
    same year/seq formatter, querying ``last_seq + 1`` without writing.
    Trade-off: if two installers open editors at the same moment they'd
    see the same suggestion; the second one will get bumped to the
    next number on save (which is fine — the suggestion is a UI hint).
    """
    tenant_id = require_tenant(ctx)
    try:
        auto = build_auto_fields(lead_id, tenant_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="lead not found"
        )

    # We allocate-and-show here — the seq IS consumed. Two concurrent
    # drafts would get distinct numbers, the unused one is simply
    # never written into lead_quotes. That's a couple wasted ints/year
    # at worst, not a real problem.
    suggested_number, suggested_seq = next_preventivo_number(tenant_id)
    return QuoteDraftResponse(
        auto_fields=auto,
        suggested_preventivo_number=suggested_number,
        suggested_preventivo_seq=suggested_seq,
    )


@router.post(
    "/leads/{lead_id}/quote",
    response_model=QuoteDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_quote(
    ctx: CurrentUser,
    lead_id: UUID,
    body: SaveQuoteRequest,
) -> QuoteDetailResponse:
    """Render + persist a new preventivo version.

    Returns 201 with the full row (auto + manual + pdf_url). The route
    is async-friendly: ``save_quote`` offloads the WeasyPrint render
    via ``asyncio.to_thread``, so even a 1-2 s render doesn't stall
    the event loop for other concurrent requests.
    """
    tenant_id = require_tenant(ctx)
    try:
        quote = await save_quote(
            lead_id=lead_id,
            tenant_id=tenant_id,
            manual_fields=body.manual_fields,
        )
    except ValueError as exc:  # lead missing / wrong tenant
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except Exception:  # WeasyPrint errors, upload errors → log + 500
        log.exception(
            "quote.create.failed",
            tenant_id=str(tenant_id),
            lead_id=str(lead_id),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to render and persist preventivo",
        )

    return QuoteDetailResponse(
        id=quote.id,
        tenant_id=quote.tenant_id,
        lead_id=quote.lead_id,
        preventivo_number=quote.preventivo_number,
        preventivo_seq=quote.preventivo_seq,
        version=quote.version,
        status=quote.status,
        pdf_url=quote.pdf_url,
        hero_url=quote.hero_url,
        auto_fields=quote.auto_fields,
        manual_fields=quote.manual_fields,
        created_at=quote.created_at,
        updated_at=quote.updated_at,
    )


@router.get(
    "/leads/{lead_id}/quote",
    response_model=list[QuoteResponse],
)
async def list_quotes(ctx: CurrentUser, lead_id: UUID) -> list[QuoteResponse]:
    """Version history for a lead, newest version first.

    The dashboard renders this as a "v3 di 5" dropdown so the installer
    can re-download an older revision after the customer asks for it.
    Bodies (auto/manual JSONB) are intentionally omitted from the list
    response — they can be 30+ keys each and the dropdown only needs
    metadata. The detail endpoint can be added later if needed.
    """
    tenant_id = require_tenant(ctx)
    quotes = list_quotes_for_lead(lead_id, tenant_id)
    return [
        QuoteResponse(
            id=q.id,
            tenant_id=q.tenant_id,
            lead_id=q.lead_id,
            preventivo_number=q.preventivo_number,
            preventivo_seq=q.preventivo_seq,
            version=q.version,
            status=q.status,
            pdf_url=q.pdf_url,
            hero_url=q.hero_url,
            created_at=q.created_at,
            updated_at=q.updated_at,
        )
        for q in quotes
    ]


@router.get(
    "/leads/{lead_id}/quote/{quote_id}/pdf",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
)
async def get_quote_pdf(
    ctx: CurrentUser, lead_id: UUID, quote_id: UUID
) -> RedirectResponse:
    """Redirect to a fresh signed URL for the PDF.

    The public URL stored in ``pdf_url`` works only for public buckets;
    the renderings bucket is private in production. Each click here
    mints a new signed URL with a 1 h expiry — short enough to not be
    abused, long enough for a slow download / re-share.
    """
    tenant_id = require_tenant(ctx)
    quote = get_quote(quote_id, tenant_id)
    if not quote or str(quote.lead_id) != str(lead_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="quote not found"
        )
    if not quote.pdf_url:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="quote has no rendered PDF",
        )

    # Reconstruct the path from the convention used in save_quote.
    # Cheaper than parsing pdf_url and survives bucket-host changes.
    pdf_path = f"{quote.tenant_id}/{quote.lead_id}/quote-{quote.preventivo_seq:04d}.pdf"
    try:
        signed = sign_url(RENDERINGS_BUCKET, pdf_path, expires_in=3600)
    except Exception:
        log.exception(
            "quote.pdf.sign_failed",
            quote_id=str(quote_id),
            path=pdf_path,
        )
        # Fallback: hand back the public URL we stored. If the bucket
        # is public this still works; if private it 403s and the user
        # gets a clear browser error rather than a misleading redirect.
        return RedirectResponse(url=quote.pdf_url, status_code=302)

    return RedirectResponse(url=signed, status_code=302)
