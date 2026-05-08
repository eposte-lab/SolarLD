"""Lead Imminence Predictor — operator-facing API.

Reads from ``lead_imminence_predictions`` (populated nightly at 06:30
UTC by ``imminence_predictions_cron``) and lets the dashboard show a
"Lead da chiamare oggi" overlay on the existing /leads list.

Routes
------
GET  /v1/imminence/today
    Today's predictions for the current tenant. Default min_score=60
    (top candidates only). The dashboard joins this against the lead
    list to overlay an "AI" badge + reasons on the relevant rows.

POST /v1/imminence/{prediction_id}/action
    Operator logs the action they took (called/emailed/ignored).
    Used by the future weekly outcome-evaluation cron.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

log = get_logger(__name__)
router = APIRouter(prefix="/imminence", tags=["imminence"])


class ImminencePrediction(BaseModel):
    id: str
    lead_id: str
    imminence_score: int
    behavioral_score: int
    temporal_score: int
    contextual_score: int
    comparative_score: int
    primary_reasons: list[str]
    talking_points: list[str]
    suggested_action: str | None
    suggested_channel: str | None
    best_time_to_contact: str | None
    actioned_at: str | None
    action_taken: str | None
    created_at: str


@router.get("/today")
async def list_today(
    user: CurrentUser,
    min_score: int = Query(default=60, ge=0, le=100),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Today's top predictions for the current tenant, ordered by score."""
    tenant_id = require_tenant(user)
    sb = get_service_client()

    today = datetime.now(UTC).date().isoformat()
    res = (
        sb.table("lead_imminence_predictions")
        .select(
            "id, lead_id, imminence_score, behavioral_score, temporal_score, "
            "contextual_score, comparative_score, primary_reasons, talking_points, "
            "suggested_action, suggested_channel, best_time_to_contact, "
            "actioned_at, action_taken, created_at"
        )
        .eq("tenant_id", tenant_id)
        .eq("prediction_date", today)
        .gte("imminence_score", min_score)
        .order("imminence_score", desc=True)
        .limit(limit)
        .execute()
    )

    rows = res.data or []
    return {
        "predictions": rows,
        "total": len(rows),
        "prediction_date": today,
        "min_score": min_score,
    }


class ActionPayload(BaseModel):
    action: Literal["called", "emailed", "whatsapped", "ignored", "marked_invalid"]


@router.post("/{prediction_id}/action")
async def record_action(
    prediction_id: str,
    payload: ActionPayload,
    user: CurrentUser,
) -> dict[str, Any]:
    """Operator logs the action taken on a prediction.

    Stamps ``actioned_at`` + ``action_taken`` + ``actioned_by_user_id``.
    Idempotent — re-clicking the same action overwrites the timestamp;
    switching action (e.g. called → emailed) updates the action_taken.
    """
    tenant_id = require_tenant(user)
    sb = get_service_client()

    # Verify the row belongs to the tenant before writing.
    existing = (
        sb.table("lead_imminence_predictions")
        .select("id, tenant_id")
        .eq("id", prediction_id)
        .maybe_single()
        .execute()
    )
    row = existing.data if existing else None
    if not row or row.get("tenant_id") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prediction not found",
        )

    sb.table("lead_imminence_predictions").update(
        {
            "actioned_at": datetime.now(UTC).isoformat(),
            "actioned_by_user_id": user.id if hasattr(user, "id") else None,
            "action_taken": payload.action,
        }
    ).eq("id", prediction_id).execute()

    return {"ok": True, "prediction_id": prediction_id, "action": payload.action}
