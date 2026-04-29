"""Lead warehouse read-only endpoint (Sprint 11).

Powers the dashboard "Magazzino lead" widget. Single GET that returns
both the current depth/runway and the policy knobs the orchestrator
applies, so the UI can render both the live state and "this is what
will happen tomorrow" in one card without a second round-trip.

Strictly read-only. Mutations (cap changes, manual refill triggers)
go through the admin route at ``/v1/admin/tenants/{id}``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services.warehouse_policy import policy_for

log = get_logger(__name__)
router = APIRouter()


@router.get("")
async def get_warehouse_status(ctx: CurrentUser) -> dict[str, Any]:
    """Snapshot of the lead warehouse for the current tenant.

    Response shape (consumed by ``WarehouseStatusCard``):

        {
          "ready_to_send_count":  173,
          "expiring_within_3d":     8,
          "oldest_age_days":       12,
          "runway_days":          0.7,
          "needs_refill":         true,
          "policy": {
            "daily_cap":              250,
            "daily_cap_min":           50,
            "daily_cap_max":          250,
            "warehouse_buffer_days":    7,
            "lead_expiration_days":    21,
            "atoka_survival_target":  0.80
          },
          "alerts": [
            {"code": "warehouse_low", "severity": "warning", ...}
          ]
        }
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    tenant_res = (
        sb.table("tenants")
        .select(
            "id, daily_target_send_cap, daily_send_cap_min, daily_send_cap_max, "
            "warehouse_buffer_days, lead_expiration_days, atoka_survival_target"
        )
        .eq("id", tenant_id)
        .single()
        .execute()
    )
    tenant_row = tenant_res.data or {}
    if not tenant_row:
        raise HTTPException(status_code=404, detail="tenant not found")

    policy = policy_for(tenant_row)

    health_res = (
        sb.table("warehouse_health")
        .select(
            "ready_to_send_count, expiring_within_3d, oldest_age_days, "
            "runway_days, needs_refill"
        )
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    h = (health_res.data or [{}])[0] or {}
    ready = int(h.get("ready_to_send_count") or 0)

    alerts = _derive_alerts(ready=ready, h=h, policy=policy)

    return {
        "ready_to_send_count": ready,
        "expiring_within_3d": int(h.get("expiring_within_3d") or 0),
        "oldest_age_days": int(h.get("oldest_age_days") or 0),
        "runway_days": h.get("runway_days") or policy.runway_days(ready),
        "needs_refill": bool(
            h.get("needs_refill") if "needs_refill" in h else policy.needs_refill(ready)
        ),
        "policy": {
            "daily_cap": policy.daily_send_cap,
            "daily_cap_min": policy.daily_send_cap_min,
            "daily_cap_max": policy.daily_send_cap_max,
            "warehouse_buffer_days": policy.warehouse_buffer_days,
            "lead_expiration_days": policy.lead_expiration_days,
            "atoka_survival_target": policy.atoka_survival_target,
        },
        "alerts": alerts,
    }


def _derive_alerts(
    *, ready: int, h: dict[str, Any], policy: Any
) -> list[dict[str, Any]]:
    """Translate raw warehouse state into actionable alert cards.

    Kept inline here (rather than the admin alert service in Task 37)
    because these are tenant-facing — the admin alerts are sent over
    a different channel (Slack / email) and have different severity
    thresholds.
    """
    out: list[dict[str, Any]] = []

    if ready == 0:
        out.append(
            {
                "code": "warehouse_empty",
                "severity": "critical",
                "message": "Magazzino vuoto: nessun lead pronto per il prossimo invio.",
            }
        )
    elif policy.needs_refill(ready):
        out.append(
            {
                "code": "warehouse_low",
                "severity": "warning",
                "message": (
                    f"Solo {ready} lead in magazzino — sotto la soglia "
                    f"di {policy.warehouse_min_size}. Verrà avviato un "
                    "nuovo ciclo di scoperta."
                ),
            }
        )

    expiring = int(h.get("expiring_within_3d") or 0)
    if expiring > 0:
        out.append(
            {
                "code": "warehouse_expiring",
                "severity": "info",
                "message": (
                    f"{expiring} lead scadranno entro 3 giorni — "
                    "considera di alzare il cap giornaliero."
                ),
            }
        )

    return out


__all__ = ["router"]
