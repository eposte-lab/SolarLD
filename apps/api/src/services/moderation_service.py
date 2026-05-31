"""Tenant-facing lead-visibility gate for moderated trial tenants.

The dashboard's RLS read path (``lib/data/leads.ts``) is gated by the
``leads_select`` policy from migration 0145 — a moderated tenant simply
cannot ``SELECT`` an un-released lead. But the API endpoints in
``routes/leads.py`` read through the **service role**, which *bypasses
RLS*, so that gate does not apply there. These helpers re-impose the
exact same gate at the API layer:

  * a hidden (un-released) lead must NOT appear in lists / exports;
  * a hidden lead must ``404`` on direct fetch / single-lead actions —
    indistinguishable from "does not exist", so the moderation layer
    stays invisible to the tenant.

A lead is *visible* to a moderated tenant when
``operator_released_at IS NOT NULL`` (mirrors the RLS predicate).

**Fail-open**, like ``appointment_service``: any error treats the
tenant as NON-moderated (everything visible), so a transient config
hiccup never silently hides a normal tenant's pipeline.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from ..core.logging import get_logger
from .appointment_service import get_moderation_config

log = get_logger(__name__)


def is_moderated(sb: Any, tenant_id: str) -> bool:
    """True when the tenant is under trial moderation (fail-open False)."""
    moderated, _ = get_moderation_config(sb, tenant_id)
    return moderated


def apply_released_filter(query: Any, sb: Any, tenant_id: str) -> Any:
    """Restrict a ``leads`` query to operator-released rows when moderated.

    No-op for non-moderated tenants, so the query is byte-for-byte the
    old behavior. For a moderated tenant it appends the same predicate
    the RLS ``leads_select`` policy enforces (``operator_released_at IS
    NOT NULL``), hiding pending/held leads from lists and exports.
    """
    if is_moderated(sb, tenant_id):
        return query.not_.is_("operator_released_at", "null")
    return query


def assert_lead_visible(sb: Any, tenant_id: str, lead_id: str) -> None:
    """Raise 404 if ``lead_id`` is hidden from a moderated tenant.

    Use on single-lead endpoints that mutate without a prior ownership
    SELECT (e.g. PATCH feedback) or that read a different table keyed by
    ``lead_id`` (e.g. the timeline ``events`` query). Endpoints that
    already run a ``.eq("id").eq("tenant_id")`` ownership check should
    instead fold ``apply_released_filter`` into that query so the
    existing 404 path also covers visibility (one fewer round-trip).
    """
    if not is_moderated(sb, tenant_id):
        return
    res = (
        sb.table("leads")
        .select("id")
        .eq("id", lead_id)
        .eq("tenant_id", tenant_id)
        .not_.is_("operator_released_at", "null")
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Lead not found")
