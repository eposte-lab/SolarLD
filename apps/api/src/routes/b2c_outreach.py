"""B2C outreach endpoints — trigger a campaign against a
materialised audience.

Routes:

  POST /v1/b2c/audiences/{id}/mail-campaign
      → trigger Pixart letter campaign for the audience's CAP list.

  POST /v1/b2c/audiences/{id}/meta-campaign
      → trigger Meta Lead Ads (Phase 3.4 — stub today).

Exports (PDF/xlsx for door-to-door) live under
`routes/b2c_exports.py` to keep the generator dependencies (reportlab,
openpyxl) isolated.

All routes require the caller's tenant to match `b2c_audiences.tenant_id`
— defence-in-depth via the service layer's `get_audience` (which
filters by tenant) in addition to FastAPI's auth dependency.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..services.b2c_audience_service import get_audience
from ..services.pixart_service import (
    LetterCampaignRequest,
    build_copy_overrides,
    resolve_template_id,
    submit_letter_campaign,
)
from ..services.tenant_module_service import get_module

router = APIRouter()
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class MailCampaignIn(BaseModel):
    """Optional overrides on the letter campaign. Defaults read from
    the tenant's outreach module (brand name, CTA)."""

    template_id: str | None = None
    note_from_installer: str | None = None


class MailCampaignOut(BaseModel):
    pixart_job_id: str
    caps_submitted: int
    stub: bool


class MetaCampaignOut(BaseModel):
    status: str
    audience_id: UUID
    note: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/audiences/{audience_id}/mail-campaign",
    response_model=MailCampaignOut,
)
async def trigger_mail_campaign(
    ctx: CurrentUser,
    audience_id: UUID,
    payload: MailCampaignIn,
) -> MailCampaignOut:
    """Trigger a Pixart letter campaign for one audience.

    Resolves the audience's CAP + income bucket, looks up the
    tenant's outreach/brand config, and dispatches the job to
    Pixart (or stub in dev).
    """
    tenant_id = require_tenant(ctx)

    audience = await get_audience(audience_id, tenant_id)
    if not audience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="audience not found",
        )

    # A single audience == a single CAP; we still pass a list to
    # Pixart because some future flows will batch by territory_id.
    caps = [audience["cap"]]
    bucket = audience.get("reddito_bucket", "medio")

    outreach_mod = await get_module(tenant_id, "outreach")
    ocfg = outreach_mod.config or {}
    copy_overrides = build_copy_overrides(
        tenant_brand_name=None,  # brand lives in tenants.settings, not module
        cta_primary=ocfg.get("cta_primary"),
    )

    template_id = payload.template_id or resolve_template_id(
        tenant_id, bucket
    )

    result = await submit_letter_campaign(
        LetterCampaignRequest(
            tenant_id=tenant_id,
            audience_id=audience_id,
            template_id=template_id,
            caps=caps,
            copy_overrides=copy_overrides,
        )
    )

    log.info(
        "b2c_outreach.mail_campaign",
        extra={
            "tenant_id": str(tenant_id),
            "audience_id": str(audience_id),
            "pixart_job_id": result.pixart_job_id,
            "stub": result.stub,
        },
    )

    return MailCampaignOut(
        pixart_job_id=result.pixart_job_id,
        caps_submitted=result.caps_submitted,
        stub=result.stub,
    )


@router.post(
    "/audiences/{audience_id}/meta-campaign",
    response_model=MetaCampaignOut,
)
async def trigger_meta_campaign(
    ctx: CurrentUser,
    audience_id: UUID,
) -> MetaCampaignOut:
    """Trigger a Meta Lead Ads campaign for the audience.

    Phase 3.4 ships the real implementation (Meta Marketing API, OAuth
    connection, custom audience upload). Today we return a stub
    response so the frontend flow is exercisable end-to-end.
    """
    tenant_id = require_tenant(ctx)

    audience = await get_audience(audience_id, tenant_id)
    if not audience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="audience not found",
        )

    log.info(
        "b2c_outreach.meta_campaign.stub",
        extra={
            "tenant_id": str(tenant_id),
            "audience_id": str(audience_id),
            "cap": audience.get("cap"),
        },
    )
    return MetaCampaignOut(
        status="queued_stub",
        audience_id=audience_id,
        note=(
            "Meta Marketing API integration lands in Phase 3.4 — "
            "endpoint accepted the request but nothing was actually sent."
        ),
    )


@router.get("/audiences/{audience_id}")
async def read_audience(
    ctx: CurrentUser, audience_id: UUID
) -> dict[str, Any]:
    """Return one audience's full row — used by the dashboard detail page."""
    tenant_id = require_tenant(ctx)
    audience = await get_audience(audience_id, tenant_id)
    if not audience:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return audience
