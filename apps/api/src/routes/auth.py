"""Authentication endpoints.

Login/refresh are actually handled by Supabase Auth directly from the
frontend clients. This router exposes `/me` so the frontend can
resolve the current user + tenant context in one call.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..core.security import CurrentUser

router = APIRouter()


@router.get("/me")
async def me(ctx: CurrentUser) -> dict[str, object]:
    """Return current user + tenant binding."""
    return {
        "user_id": ctx.user_id,
        "email": ctx.email,
        "tenant_id": ctx.tenant_id,
        "role": ctx.role,
    }
