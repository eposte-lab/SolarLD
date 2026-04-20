"""Audit log service — append-only trail of operator mutations.

Part B.11. Every meaningful state-change performed by an authenticated
operator is recorded here so the tenant can:
  1. Demonstrate GDPR compliance (who deleted what, when).
  2. Investigate anomalies ("who sent that rogue follow-up?").
  3. Satisfy a data subject access request (full history by target_id).

Design rules:
  - **Best-effort**: ``log_action`` never raises. A write failure is
    logged as a warning but never rolls back the primary mutation.
  - **Service-role writes**: RLS has no INSERT policy; the table is
    written exclusively via the service client.
  - **Actor resolution**: call sites pass ``actor_user_id`` from the
    JWT sub claim (``ctx.sub`` in FastAPI handlers). Cron/system
    actions leave it None.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)


async def log_action(
    tenant_id: str,
    action: str,
    *,
    actor_user_id: str | None = None,
    target_table: str | None = None,
    target_id: str | None = None,
    diff: dict[str, Any] | None = None,
) -> None:
    """Append one row to ``audit_log``.

    Parameters
    ----------
    tenant_id:
        Owning tenant — required for RLS reads.
    action:
        Dot-namespaced verb, e.g. ``"lead.deleted"``,
        ``"lead.feedback_updated"``, ``"config.updated"``.
    actor_user_id:
        Supabase auth UID of the operator who triggered the action.
        Pass ``ctx.sub`` from FastAPI's ``CurrentUser`` context.
    target_table:
        Postgres table name of the mutated row (``"leads"``, etc.).
    target_id:
        String-cast PK of the mutated row (UUID or numeric id as str).
    diff:
        JSON-serialisable dict describing what changed.  Keep it small
        — include only the fields that are meaningful for audit (not
        the full row). Never include PII beyond what's already indexed.
    """
    try:
        sb = get_service_client()
        sb.table("audit_log").insert(
            {
                "tenant_id": tenant_id,
                "actor_user_id": actor_user_id,
                "action": action,
                "target_table": target_table,
                "target_id": str(target_id) if target_id is not None else None,
                "diff": diff,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        # Never propagate — audit failure must not break the primary flow.
        log.warning(
            "audit_log.write_failed",
            action=action,
            target_table=target_table,
            target_id=str(target_id) if target_id is not None else None,
            err=str(exc),
        )


async def get_audit_log(
    tenant_id: str,
    *,
    limit: int = 100,
    target_table: str | None = None,
    target_id: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent audit rows (for the settings/privacy viewer).

    This hits the service client directly — it's used only from
    server-side API handlers. The dashboard reads via Supabase RLS
    from ``lib/data/audit.ts`` without going through FastAPI.
    """
    sb = get_service_client()
    q = (
        sb.table("audit_log")
        .select("id, action, target_table, target_id, actor_user_id, diff, at")
        .eq("tenant_id", tenant_id)
    )
    if target_table:
        q = q.eq("target_table", target_table)
    if target_id:
        q = q.eq("target_id", str(target_id))

    res = q.order("at", desc=True).limit(limit).execute()
    return res.data or []
