"""Acquisition campaigns — CRUD for strategic targeting entities.

Each acquisition campaign bundles the five wizard module configs
(sorgente, tecnico, economico, outreach, crm) plus inbox restrictions,
schedule, and budget into a named, reusable targeting strategy.

Endpoints
---------
GET    /v1/acquisition-campaigns            List all campaigns for the tenant
POST   /v1/acquisition-campaigns            Create a new campaign
GET    /v1/acquisition-campaigns/{id}       Get a single campaign
PATCH  /v1/acquisition-campaigns/{id}       Update campaign fields
DELETE /v1/acquisition-campaigns/{id}       Archive (soft-delete) a campaign
POST   /v1/acquisition-campaigns/{id}/activate  → status='active'
POST   /v1/acquisition-campaigns/{id}/pause     → status='paused'
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()
log = get_logger(__name__)

_SELECT_FIELDS = (
    "id, tenant_id, name, description, is_default, status, "
    "sorgente_config, tecnico_config, economico_config, outreach_config, crm_config, "
    "inbox_ids, schedule_cron, budget_cap_cents, "
    "created_at, updated_at"
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class AcquisitionCampaignCreate(BaseModel):
    name: str = Field(default="Nuova campagna", min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    sorgente_config: dict[str, Any] = Field(default_factory=dict)
    tecnico_config: dict[str, Any] = Field(default_factory=dict)
    economico_config: dict[str, Any] = Field(default_factory=dict)
    outreach_config: dict[str, Any] = Field(default_factory=dict)
    crm_config: dict[str, Any] = Field(default_factory=dict)
    inbox_ids: list[str] | None = None
    schedule_cron: str | None = None
    budget_cap_cents: int | None = Field(default=None, ge=1)


class AcquisitionCampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    sorgente_config: dict[str, Any] | None = None
    tecnico_config: dict[str, Any] | None = None
    economico_config: dict[str, Any] | None = None
    outreach_config: dict[str, Any] | None = None
    crm_config: dict[str, Any] | None = None
    inbox_ids: list[str] | None = None
    schedule_cron: str | None = None
    budget_cap_cents: int | None = Field(default=None, ge=1)


# ---------------------------------------------------------------------------
# GET /v1/acquisition-campaigns
# ---------------------------------------------------------------------------


@router.get("")
async def list_acquisition_campaigns(ctx: CurrentUser) -> dict[str, Any]:
    """List all acquisition campaigns for the current tenant."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    try:
        res = (
            sb.table("acquisition_campaigns")
            .select(_SELECT_FIELDS)
            .eq("tenant_id", tenant_id)
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("acq_campaigns.list_failed", tenant_id=tenant_id, err=str(exc))
        return {"campaigns": [], "total": 0}
    rows = res.data or []
    return {"campaigns": rows, "total": len(rows)}


# ---------------------------------------------------------------------------
# POST /v1/acquisition-campaigns
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_acquisition_campaign(
    body: AcquisitionCampaignCreate,
    ctx: CurrentUser,
) -> dict[str, Any]:
    """Create a new acquisition campaign."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    insert_data: dict[str, Any] = {
        "tenant_id": tenant_id,
        "name": body.name.strip(),
        "status": "draft",
        "sorgente_config": body.sorgente_config,
        "tecnico_config": body.tecnico_config,
        "economico_config": body.economico_config,
        "outreach_config": body.outreach_config,
        "crm_config": body.crm_config,
    }
    if body.description is not None:
        insert_data["description"] = body.description.strip()
    if body.inbox_ids is not None:
        insert_data["inbox_ids"] = body.inbox_ids
    if body.schedule_cron is not None:
        insert_data["schedule_cron"] = body.schedule_cron
    if body.budget_cap_cents is not None:
        insert_data["budget_cap_cents"] = body.budget_cap_cents

    try:
        res = sb.table("acquisition_campaigns").insert(insert_data).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("acq_campaigns.create_failed", tenant_id=tenant_id, err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create campaign",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Insert returned no data",
        )
    log.info("acq_campaigns.created", tenant_id=tenant_id, campaign_id=res.data[0]["id"])
    return res.data[0]


# ---------------------------------------------------------------------------
# GET /v1/acquisition-campaigns/{campaign_id}
# ---------------------------------------------------------------------------


@router.get("/{campaign_id}")
async def get_acquisition_campaign(
    campaign_id: str,
    ctx: CurrentUser,
) -> dict[str, Any]:
    """Fetch a single acquisition campaign."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("acquisition_campaigns")
        .select(_SELECT_FIELDS)
        .eq("id", campaign_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    row = (res.data or [None])[0]
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )
    return row


# ---------------------------------------------------------------------------
# PATCH /v1/acquisition-campaigns/{campaign_id}
# ---------------------------------------------------------------------------


@router.patch("/{campaign_id}")
async def update_acquisition_campaign(
    campaign_id: str,
    body: AcquisitionCampaignUpdate,
    ctx: CurrentUser,
) -> dict[str, Any]:
    """Partially update an acquisition campaign."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    update_data: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if body.name is not None:
        update_data["name"] = body.name.strip()
    if body.description is not None:
        update_data["description"] = body.description.strip()
    if body.sorgente_config is not None:
        update_data["sorgente_config"] = body.sorgente_config
    if body.tecnico_config is not None:
        update_data["tecnico_config"] = body.tecnico_config
    if body.economico_config is not None:
        update_data["economico_config"] = body.economico_config
    if body.outreach_config is not None:
        update_data["outreach_config"] = body.outreach_config
    if body.crm_config is not None:
        update_data["crm_config"] = body.crm_config
    if body.inbox_ids is not None:
        update_data["inbox_ids"] = body.inbox_ids
    if body.schedule_cron is not None:
        update_data["schedule_cron"] = body.schedule_cron
    if body.budget_cap_cents is not None:
        update_data["budget_cap_cents"] = body.budget_cap_cents

    if len(update_data) == 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update",
        )

    try:
        res = (
            sb.table("acquisition_campaigns")
            .update(update_data)
            .eq("id", campaign_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("acq_campaigns.update_failed", campaign_id=campaign_id, err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Update failed",
        ) from exc

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found or not owned by this tenant",
        )
    return res.data[0]


# ---------------------------------------------------------------------------
# DELETE /v1/acquisition-campaigns/{campaign_id}
# ---------------------------------------------------------------------------


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def archive_acquisition_campaign(
    campaign_id: str,
    ctx: CurrentUser,
) -> Response:
    """Archive an acquisition campaign (soft-delete: status → 'archived').

    Hard delete is intentionally not exposed: outreach_sends rows that
    reference this campaign would become orphans (acquisition_campaign_id
    goes to NULL via ON DELETE SET NULL), losing attribution history.
    Archiving preserves the relationship while hiding the campaign from
    active lists.

    The default campaign (is_default=true) cannot be archived.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Prevent archiving the default campaign
    row_res = (
        sb.table("acquisition_campaigns")
        .select("id, is_default")
        .eq("id", campaign_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    row = (row_res.data or [None])[0]
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found or not owned by this tenant",
        )
    if row.get("is_default"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "La campagna default non può essere archiviata. "
                "Modifica i parametri o crea una nuova campagna default."
            ),
        )

    try:
        sb.table("acquisition_campaigns").update(
            {
                "status": "archived",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", campaign_id).eq("tenant_id", tenant_id).execute()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Archive failed",
        ) from exc

    log.info("acq_campaigns.archived", tenant_id=tenant_id, campaign_id=campaign_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# POST /v1/acquisition-campaigns/{campaign_id}/activate
# ---------------------------------------------------------------------------


@router.post("/{campaign_id}/activate")
async def activate_acquisition_campaign(
    campaign_id: str,
    ctx: CurrentUser,
) -> dict[str, Any]:
    """Move a campaign from draft/paused to active."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            sb.table("acquisition_campaigns")
            .update({"status": "active", "updated_at": now_iso})
            .eq("id", campaign_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Activate failed") from exc
    if not res.data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"ok": True, "campaign_id": campaign_id, "status": "active"}


# ---------------------------------------------------------------------------
# POST /v1/acquisition-campaigns/{campaign_id}/pause
# ---------------------------------------------------------------------------


@router.post("/{campaign_id}/pause")
async def pause_acquisition_campaign(
    campaign_id: str,
    ctx: CurrentUser,
) -> dict[str, Any]:
    """Pause an active campaign."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            sb.table("acquisition_campaigns")
            .update({"status": "paused", "updated_at": now_iso})
            .eq("id", campaign_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="Pause failed") from exc
    if not res.data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"ok": True, "campaign_id": campaign_id, "status": "paused"}
