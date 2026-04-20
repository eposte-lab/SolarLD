"""Admin-only endpoints (super-admin).

All routes in this module require ``ctx.role == 'super_admin'``.
The service role client bypasses RLS so these endpoints can reach
cross-tenant data — the role gate is the only thing keeping them
safe. Do not expose any new admin endpoint without calling
``_require_super_admin(ctx)`` at the top.

Surface area:

    GET    /system/health                    — smoke check
    GET    /system/stats                     — platform KPIs
    GET    /blacklist                        — global email blacklist
    GET    /tenants                          — all tenants + counters
    GET    /tenants/{id}                     — one tenant + usage
    PATCH  /tenants/{id}                     — tier/status/flags updates
    GET    /tenants/{id}/feature-flags       — current flags
    PATCH  /tenants/{id}/feature-flags       — update one flag (partial)
    GET    /cost-report?days=30              — spend rollup platform-wide
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..core.security import CurrentUser
from ..core.supabase_client import get_service_client

router = APIRouter()


# ---------------------------------------------------------------------------
# Role gate
# ---------------------------------------------------------------------------


def _require_super_admin(ctx: CurrentUser) -> None:
    if ctx.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires super_admin role",
        )


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@router.get("/system/health")
async def system_health(ctx: CurrentUser) -> dict[str, object]:
    _require_super_admin(ctx)
    return {"status": "ok", "services": ["db", "redis", "claude", "replicate"]}


@router.get("/system/stats")
async def system_stats(ctx: CurrentUser) -> dict[str, Any]:
    """Platform-wide KPIs. Counts are cheap under RLS-bypassing service role."""
    _require_super_admin(ctx)
    sb = get_service_client()

    tenants = sb.table("tenants").select("id", count="exact", head=True).execute()
    active_tenants = (
        sb.table("tenants")
        .select("id", count="exact", head=True)
        .eq("status", "active")
        .execute()
    )
    leads = sb.table("leads").select("id", count="exact", head=True).execute()
    users = (
        sb.table("tenant_members")
        .select("user_id", count="exact", head=True)
        .execute()
    )
    return {
        "tenants_total": tenants.count or 0,
        "tenants_active": active_tenants.count or 0,
        "users_total": users.count or 0,
        "leads_total": leads.count or 0,
    }


# ---------------------------------------------------------------------------
# Blacklist
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tenant management
# ---------------------------------------------------------------------------


@router.get("/tenants")
async def list_tenants(ctx: CurrentUser) -> list[dict[str, Any]]:
    """All tenants + roll-up counters (leads, cost MTD, members)."""
    _require_super_admin(ctx)
    sb = get_service_client()
    try:
        res = sb.rpc("admin_tenant_overview", {}).execute()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"admin_tenant_overview rpc failed: {exc}"
        ) from exc
    return res.data or []


@router.get("/tenants/{tenant_id}")
async def get_tenant(ctx: CurrentUser, tenant_id: str) -> dict[str, Any]:
    _require_super_admin(ctx)
    sb = get_service_client()
    t_res = (
        sb.table("tenants")
        .select("*")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not t_res.data:
        raise HTTPException(status_code=404, detail="tenant not found")
    usage_res = sb.rpc(
        "analytics_usage_mtd", {"p_tenant_id": tenant_id}
    ).execute()
    members_res = (
        sb.table("tenant_members")
        .select("user_id, role, created_at")
        .eq("tenant_id", tenant_id)
        .execute()
    )
    return {
        "tenant": t_res.data[0],
        "usage_mtd": usage_res.data,
        "members": members_res.data or [],
    }


class TenantAdminUpdate(BaseModel):
    """Super-admin editable tenant fields — distinct from tenant self-serve."""

    tier: Literal["founding", "growth", "enterprise"] | None = None
    status: Literal[
        "onboarding", "active", "paused", "churned", "trial"
    ] | None = None
    monthly_rate_cents: int | None = Field(default=None, ge=0)
    contract_start_date: str | None = None
    contract_end_date: str | None = None
    business_name: str | None = None


@router.patch("/tenants/{tenant_id}")
async def update_tenant(
    ctx: CurrentUser, tenant_id: str, payload: TenantAdminUpdate
) -> dict[str, Any]:
    _require_super_admin(ctx)
    update = payload.model_dump(exclude_none=True)
    if not update:
        raise HTTPException(status_code=400, detail="no updatable fields")
    sb = get_service_client()
    res = (
        sb.table("tenants")
        .update(update)
        .eq("id", tenant_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="tenant not found")
    return res.data[0]


# ---------------------------------------------------------------------------
# Feature flags (stored in tenants.settings.feature_flags JSONB map)
# ---------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/feature-flags")
async def get_feature_flags(ctx: CurrentUser, tenant_id: str) -> dict[str, Any]:
    _require_super_admin(ctx)
    sb = get_service_client()
    res = (
        sb.table("tenants")
        .select("settings")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="tenant not found")
    settings_obj = res.data[0].get("settings") or {}
    return {"feature_flags": settings_obj.get("feature_flags", {})}


class FeatureFlagPatch(BaseModel):
    """Partial update — only the keys provided are changed/added."""

    flags: dict[str, Any] = Field(
        description=(
            "Map of flag name → value. Null value removes the flag. "
            "Example: {\"whatsapp_outreach\": true, \"legacy_scoring\": null}"
        )
    )


@router.patch("/tenants/{tenant_id}/feature-flags")
async def patch_feature_flags(
    ctx: CurrentUser, tenant_id: str, payload: FeatureFlagPatch
) -> dict[str, Any]:
    _require_super_admin(ctx)
    sb = get_service_client()

    res = (
        sb.table("tenants")
        .select("settings")
        .eq("id", tenant_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="tenant not found")

    current = res.data[0].get("settings") or {}
    flags = dict(current.get("feature_flags") or {})
    for key, value in payload.flags.items():
        if value is None:
            flags.pop(key, None)
        else:
            flags[key] = value

    new_settings = dict(current)
    new_settings["feature_flags"] = flags

    upd = (
        sb.table("tenants")
        .update({"settings": new_settings})
        .eq("id", tenant_id)
        .execute()
    )
    return {"tenant_id": tenant_id, "feature_flags": flags, "updated": bool(upd.data)}


# ---------------------------------------------------------------------------
# Cost report — platform-wide spend rollup
# ---------------------------------------------------------------------------


@router.get("/cost-report")
async def cost_report(
    ctx: CurrentUser,
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    _require_super_admin(ctx)
    sb = get_service_client()
    try:
        res = sb.rpc("admin_platform_cost", {"p_days": days}).execute()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"admin_platform_cost rpc failed: {exc}"
        ) from exc
    return res.data or {
        "window_days": days,
        "by_tenant": [],
        "by_provider": [],
        "total_cost_cents": 0,
    }
