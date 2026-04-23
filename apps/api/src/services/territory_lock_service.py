"""Territory lock — contractual territorial exclusivity.

Once the installer confirms their zone at the end of onboarding, the
tenant's `territory_locked_at` column is set. From that point on:

  - POST/DELETE on /v1/territories/* returns 423 for the tenant.
  - PUT /v1/modules/sorgente returns 423 if it would change the
    geographic fields (`regioni`, `province`, `cap`); non-geo fields
    (ATECO, employees, revenue, B2C income) remain editable.
  - The dashboard hides the add/delete buttons and shows a banner.
  - Only ops can reverse via POST /v1/admin/tenants/:id/territory-unlock.

The actual gating happens here so routes/admin/onboarding agree on
semantics and error shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)


# Columns inside sorgente.config that become frozen once the tenant's
# territory is locked. Changing any of these keys while locked is a 423.
SORGENTE_LOCKED_FIELDS: tuple[str, ...] = ("regioni", "province", "cap")


def is_locked(tenant_id: UUID | str) -> bool:
    """Is this tenant's territory currently frozen?

    Returns False if the tenant row cannot be read — fail-open on reads
    (the caller typically checks this before a write and will still be
    gated by RLS / downstream validation).
    """
    sb = get_service_client()
    res = (
        sb.table("tenants")
        .select("territory_locked_at")
        .eq("id", str(tenant_id))
        .limit(1)
        .execute()
    )
    if not res.data:
        return False
    return bool(res.data[0].get("territory_locked_at"))


def require_unlocked(tenant_id: UUID | str, *, what: str = "territory") -> None:
    """Raise 423 Locked if the tenant's territory is frozen.

    `what` is woven into the error message so the dashboard can show a
    helpful hint ("territory", "sorgente geo fields", etc.).
    """
    if is_locked(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                f"Territory locked — cannot modify {what}. "
                "Contact support to unlock (contratto di esclusiva)."
            ),
        )


def lock(tenant_id: UUID | str, *, user_id: UUID | str | None) -> dict[str, Any]:
    """Set territory_locked_at = now() for the tenant. Idempotent.

    If already locked, the existing lock timestamp is preserved; we
    never overwrite (that would mask the real audit trail).
    """
    sb = get_service_client()
    tid = str(tenant_id)

    existing = (
        sb.table("tenants")
        .select("id, territory_locked_at, territory_locked_by")
        .eq("id", tid)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(status_code=404, detail="tenant not found")
    row = existing.data[0]

    if row.get("territory_locked_at"):
        log.info("territory_lock.noop", tenant_id=tid, reason="already_locked")
        return row

    now_iso = datetime.now(timezone.utc).isoformat()
    upd = (
        sb.table("tenants")
        .update(
            {
                "territory_locked_at": now_iso,
                "territory_locked_by": str(user_id) if user_id else None,
            }
        )
        .eq("id", tid)
        .execute()
    )
    log.info("territory_lock.set", tenant_id=tid, locked_by=str(user_id) if user_id else None)
    return (upd.data or [row])[0]


def unlock(tenant_id: UUID | str) -> dict[str, Any]:
    """Clear the lock. Service-role / ops only — no user-facing endpoint."""
    sb = get_service_client()
    tid = str(tenant_id)
    upd = (
        sb.table("tenants")
        .update({"territory_locked_at": None, "territory_locked_by": None})
        .eq("id", tid)
        .execute()
    )
    if not upd.data:
        raise HTTPException(status_code=404, detail="tenant not found")
    log.warning("territory_lock.unset", tenant_id=tid)
    return upd.data[0]


def reject_geo_change(
    tenant_id: UUID | str,
    *,
    current: dict[str, Any] | None,
    proposed: dict[str, Any],
) -> None:
    """423 if any of the frozen geo fields differs between current and proposed.

    Called from PUT /v1/modules/sorgente. We compare per-field rather
    than diff-whole-config so changes to ATECO codes / employees /
    income bands remain allowed post-lock. `current` can be None (no
    row yet — not possible post-onboarding but defended).
    """
    if not is_locked(tenant_id):
        return

    cur = current or {}
    changed = [
        f
        for f in SORGENTE_LOCKED_FIELDS
        if _normalised(cur.get(f)) != _normalised(proposed.get(f))
    ]
    if changed:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                f"Territory locked — cannot change sorgente geo fields: {changed}. "
                "Contact support to unlock (contratto di esclusiva)."
            ),
        )


def _normalised(v: Any) -> list[str]:
    """Coerce a list-or-None JSONB value to a sorted list for comparison."""
    if v is None:
        return []
    if isinstance(v, list):
        return sorted(str(x) for x in v)
    return [str(v)]
