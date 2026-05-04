"""Daily pipeline orchestrator (Sprint 11).

Runs once per day per tenant. Two responsibilities:

  1. **Refill the warehouse if depleted.** When `runway_days` is below
     the tenant's `warehouse_buffer_days`, kick off a discovery cycle
     (Atoka via the maximised query builder) → enrich → score → qualify.
     Each survivor is upserted as a `leads` row in state
     `ready_to_send`, with `enqueued_to_warehouse_at = now()` and
     `expires_at = now() + lead_expiration_days`.

  2. **Pick today's batch and ship it.** Using the atomic `warehouse_pick`
     RPC, dequeue up to `daily_send_cap` leads in FIFO order. For each
     picked lead, enqueue a creative + outreach job — the heavy assets
     (Solar API + Kling video) are generated *here*, not pre-staged.

The two responsibilities are decoupled deliberately: most days we only
do step 2 (warehouse is fat enough), and a refill cycle runs only when
needed. That's how Atoka spend stays bounded — we don't pull a fresh
500 every day "just in case".

Concurrency
-----------
The atomic SELECT … FOR UPDATE SKIP LOCKED in `warehouse_pick` makes
this orchestrator safe even if cron fires twice or two API replicas
both process the same tenant. Stale picks (>6h in `picked` state) are
auto-recovered by `warehouse_unstick_picked` at the start of each run.

Failure model
-------------
Every external IO call (Atoka, Postgres, Redis queue) raises;
`process_tenant_daily_send` catches and logs but never re-raises, so
one tenant's failure doesn't stall the rest of the platform. The
admin alert path (Task 37) consumes the same log events.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client
from .warehouse_alerts_service import (
    emit_atoka_failure_alert,
    emit_warehouse_state_alerts,
)
from .warehouse_policy import WarehousePolicy, policy_for

log = get_logger(__name__)


# ----------------------------------------------------------------------
# Top-level entry — called by the cron registration in workers.cron
# ----------------------------------------------------------------------


async def run_daily_orchestrator() -> dict[str, Any]:
    """Iterate every active tenant and run their daily pipeline.

    Returns a roll-up suitable for logging / weekly digest:
        {"tenants_processed": 12, "tenants_failed": 0, "details": [...]}
    """
    sb = get_service_client()

    # First, recover any stale picks platform-wide. Cheap (one
    # bounded UPDATE) so we always run it before today's pick.
    try:
        recovered = sb.rpc("warehouse_unstick_picked", {"p_max_age_hours": 6}).execute()
        recovered_count = (
            recovered.data if isinstance(recovered.data, int) else 0
        )
        if recovered_count:
            log.warning("warehouse_unstuck_picks", count=recovered_count)
    except Exception as exc:  # noqa: BLE001
        log.warning("warehouse_unstick_failed", err=str(exc))

    tenants = (
        sb.table("tenants")
        .select(
            "id, status, daily_target_send_cap, daily_send_cap_min, "
            "daily_send_cap_max, warehouse_buffer_days, lead_expiration_days, "
            "atoka_survival_target"
        )
        .eq("status", "active")
        .execute()
    )

    details: list[dict[str, Any]] = []
    failed = 0

    for row in tenants.data or []:
        try:
            outcome = await process_tenant_daily_send(row)
            details.append(outcome)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.error(
                "daily_orchestrator_tenant_failed",
                tenant_id=row.get("id"),
                err=str(exc),
            )
            details.append({"tenant_id": row.get("id"), "error": str(exc)})

    summary = {
        "tenants_processed": len(details) - failed,
        "tenants_failed": failed,
        "details": details,
    }
    log.info("daily_orchestrator_complete", **{
        k: v for k, v in summary.items() if k != "details"
    })
    return summary


# ----------------------------------------------------------------------
# Per-tenant
# ----------------------------------------------------------------------


async def process_tenant_daily_send(tenant: dict[str, Any]) -> dict[str, Any]:
    """Run the daily pipeline for one tenant. Never raises."""
    tenant_id = str(tenant["id"])
    policy = policy_for(tenant)
    sb = get_service_client()

    # Read warehouse depth via the dashboard-facing view. Cheaper than
    # COUNT(*) every time and uses the same source-of-truth as the UI,
    # so an admin watching the widget sees what the orchestrator saw.
    health = (
        sb.table("warehouse_health")
        .select(
            "ready_to_send_count, expiring_within_3d, runway_days, needs_refill"
        )
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    h = (health.data or [{}])[0]
    ready_count = int(h.get("ready_to_send_count") or 0)
    expiring_3d = int(h.get("expiring_within_3d") or 0)
    needs_refill = bool(h.get("needs_refill") or policy.needs_refill(ready_count))

    # Tenant-facing alerts (in-app bell). Dedup handled by the alerts
    # service so this is safe to call every tick.
    await emit_warehouse_state_alerts(
        tenant_id=tenant_id,
        ready_count=ready_count,
        min_size=policy.warehouse_min_size,
        expiring_within_3d=expiring_3d,
    )

    refill_outcome: dict[str, Any] | None = None
    if needs_refill:
        try:
            refill_outcome = await _refill_warehouse(tenant_id, policy)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "warehouse_refill_failed",
                tenant_id=tenant_id,
                err=str(exc),
            )
            await emit_atoka_failure_alert(tenant_id=tenant_id, err=str(exc))
            refill_outcome = {"status": "failed", "error": str(exc)}

    # Pick up to daily_send_cap leads (atomic FIFO).
    picked_ids = pick_from_warehouse(tenant_id=tenant_id, n=policy.daily_send_cap)

    # Enqueue per-lead creative + outreach. The pick has already moved
    # the leads to `picked`; on enqueue success they'll progress to
    # `rendering` → `rendered` → `sent` via the existing CreativeAgent
    # / OutreachAgent state machine (Task 34 wires the rendering hook).
    for lid in picked_ids:
        await enqueue(
            "creative_task",
            {"tenant_id": tenant_id, "lead_id": lid, "trigger": "warehouse_pick"},
        )

    return {
        "tenant_id": tenant_id,
        "ready_before": ready_count,
        "needed_refill": needs_refill,
        "refill": refill_outcome,
        "picked": len(picked_ids),
        "cap": policy.daily_send_cap,
    }


# ----------------------------------------------------------------------
# Pick helper — thin wrapper around the warehouse_pick RPC
# ----------------------------------------------------------------------


def pick_from_warehouse(*, tenant_id: str, n: int) -> list[str]:
    """Dequeue up to N leads from the warehouse, returning their ids.

    The transition `ready_to_send → picked` happens atomically inside
    Postgres via the RPC defined in migration 0072. We don't try to be
    clever here — the heavy lifting is in the SQL.
    """
    if n <= 0:
        return []
    sb = get_service_client()
    res = sb.rpc(
        "warehouse_pick",
        {"p_tenant_id": tenant_id, "p_count": n},
    ).execute()
    rows = res.data or []
    out: list[str] = []
    for r in rows:
        rid = r.get("lead_id") if isinstance(r, dict) else None
        if rid:
            out.append(str(rid))
    return out


# ----------------------------------------------------------------------
# Refill
# ----------------------------------------------------------------------


async def _refill_warehouse(
    tenant_id: str,
    policy: WarehousePolicy,
) -> dict[str, Any]:
    """Trigger a discovery cycle to bring the warehouse back to target.

    Routing logic:
      * v3 path (preferred): if the tenant has at least one row in
        ``tenant_target_areas``, enqueue ``hunter_funnel_v3_task`` —
        the geocentric no-Atoka funnel from PRD_FLUSSO_DEFINITIVO.
      * v2 fallback: if the tenant has no zones yet but has territories
        (legacy rows), enqueue ``hunter_task`` per territory.
      * Skip if neither.

    This lets v2 and v3 tenants coexist during the migration without
    duplicate scan spend.
    """
    sb = get_service_client()

    # ----- v3 path: tenant has OSM zones -----
    zones_res = (
        sb.table("tenant_target_areas")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    has_zones = (zones_res.count or 0) > 0
    if has_zones:
        deficit = max(
            policy.warehouse_min_size,
            policy.daily_send_cap * (policy.warehouse_buffer_days + 2),
        )
        await enqueue(
            "hunter_funnel_v3_task",
            {
                "tenant_id": tenant_id,
                "max_l1_candidates": min(2000, max(200, deficit * 5)),
                "trigger": "warehouse_refill",
            },
            job_id=f"funnel_v3_refill:{tenant_id}",
        )
        log.info(
            "warehouse_refill_v3_enqueued",
            tenant_id=tenant_id,
            target_intake=deficit,
        )
        return {
            "status": "enqueued",
            "path": "v3_geocentric",
            "target_intake": deficit,
        }

    # ----- v2 fallback: legacy territories -----
    territories = (
        sb.table("territories")
        .select("id, type, code, metadata")
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .execute()
    )
    rows = territories.data or []
    if not rows:
        return {
            "status": "skipped",
            "reason": "no_zones_no_territories",
        }

    deficit = max(
        policy.warehouse_min_size,
        policy.daily_send_cap * (policy.warehouse_buffer_days + 2),
    )
    per_territory = max(50, deficit // len(rows))

    enqueued = 0
    for t in rows:
        await enqueue(
            "hunter_task",
            {
                "tenant_id": tenant_id,
                "territory_id": t["id"],
                "target_intake": per_territory,
                "trigger": "warehouse_refill",
                "warehouse_expiration_days": policy.lead_expiration_days,
            },
        )
        enqueued += 1

    log.info(
        "warehouse_refill_v2_enqueued",
        tenant_id=tenant_id,
        territories=enqueued,
        per_territory=per_territory,
    )
    return {
        "status": "enqueued",
        "path": "v2_atoka",
        "territories": enqueued,
        "per_territory": per_territory,
    }


# ----------------------------------------------------------------------
# Helpers exported for tests
# ----------------------------------------------------------------------


def compute_expires_at(
    *,
    enqueued_at: datetime,
    lead_expiration_days: int,
) -> datetime:
    """Pure helper: when does a lead enqueued now expire from the warehouse?"""
    return enqueued_at + timedelta(days=lead_expiration_days)


__all__ = [
    "run_daily_orchestrator",
    "process_tenant_daily_send",
    "pick_from_warehouse",
    "compute_expires_at",
]
