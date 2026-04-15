"""Admin-only endpoints (super-admin).

Role enforcement: CurrentUser.role must be 'super_admin'.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ..core.security import CurrentUser
from ..core.supabase_client import get_service_client

router = APIRouter()


def _require_super_admin(ctx: CurrentUser) -> None:
    if ctx.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires super_admin role",
        )


@router.get("/system/health")
async def system_health(ctx: CurrentUser) -> dict[str, object]:
    _require_super_admin(ctx)
    return {"status": "ok", "services": ["db", "redis", "claude", "replicate"]}


@router.get("/blacklist")
async def list_blacklist(ctx: CurrentUser) -> list[dict[str, object]]:
    _require_super_admin(ctx)
    sb = get_service_client()
    res = (
        sb.table("global_blacklist")
        .select("*")
        .order("created_at", desc=True)
        .limit(500)
        .execute()
    )
    return res.data or []
