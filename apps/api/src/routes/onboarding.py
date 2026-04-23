"""Onboarding endpoints — the wizard's server-side hand-offs.

Today this module owns a single endpoint:

    POST /v1/onboarding/territory-confirm
        Called by the last step of the onboarding wizard after the
        installer ticks "Confermo e blocco la mia zona di esclusiva".
        Sets `tenants.territory_locked_at = now()`. Idempotent: a
        second call is a no-op returning the existing timestamp.

Endpoint is tenant-scoped via `require_tenant(ctx)`; any authenticated
user tied to a tenant can confirm (a brand-new tenant has only its
owner as a member, so in practice it's always the installer). If we
later add multi-user tenants we'll re-evaluate whether this should be
restricted to the owner role.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..services.territory_lock_service import lock

router = APIRouter()
log = get_logger(__name__)


@router.post("/territory-confirm")
async def confirm_territory(ctx: CurrentUser) -> dict[str, object]:
    """Lock the tenant's territorial exclusivity.

    Returns the tenant row with `territory_locked_at` set. If already
    locked, the existing timestamp is preserved (no re-lock).
    """
    tenant_id = require_tenant(ctx)
    row = lock(tenant_id, user_id=getattr(ctx, "user_id", None))
    log.info(
        "onboarding.territory_confirmed",
        tenant_id=tenant_id,
        locked_at=row.get("territory_locked_at"),
    )
    return {
        "tenant_id": tenant_id,
        "territory_locked_at": row.get("territory_locked_at"),
        "territory_locked_by": row.get("territory_locked_by"),
    }
