"""Contatti — scan_candidates list endpoint.

``scan_candidates`` is the working set of every company discovered by the
B2B funnel (L1-L4). Rows here are *not* yet in the sales pipeline — they
are raw B2B contacts sourced from Atoka, enriched, scored by Haiku, and
Solar-gated. Only those that survive L4 become ``leads``.

This endpoint exposes the table for the dashboard "/contatti" view so
operators can see the full top-of-funnel picture and understand where
candidates dropped out.

Terminology (to be consistent with the dashboard copy):
  stage 1 = discovered by Atoka (L1)
  stage 2 = enriched via Places (L2)
  stage 3 = scored by Haiku (L3)
  stage 4 = Solar-qualified (L4) — ``solar_verdict`` discriminates further
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client

router = APIRouter()
log = get_logger(__name__)

# Columns returned for the list view — tight select to keep the payload lean.
_LIST_SELECT = (
    "id, scan_id, territory_id, vat_number, business_name, "
    "ateco_code, employees, revenue_eur, hq_city, hq_province, "
    "score, stage, solar_verdict, created_at, "
    "territories:territory_id(name, type, code)"
)


@router.get("")
async def list_contatti(
    ctx: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    stage: int | None = Query(
        default=None, ge=1, le=4,
        description="Filter by funnel stage (1=Atoka, 2=Enriched, 3=Scored, 4=Solar-qualified)",
    ),
    territory_id: str | None = Query(
        default=None,
        description="UUID of the territory to filter by",
    ),
    solar_verdict: str | None = Query(
        default=None,
        description="Filter by solar_verdict (accepted, rejected_tech, no_building, api_error, skipped_below_gate)",
    ),
) -> dict[str, Any]:
    """Paginated list of scan_candidates for the current tenant.

    RLS on ``scan_candidates`` (policy ``sc_tenant_iso``, migration 0041)
    restricts every row to ``tenant_id = auth_tenant_id()``.  We use the
    service client (bypasses RLS) but filter explicitly by tenant_id so
    the endpoint is always tenant-scoped regardless of policy state.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    from_idx = (page - 1) * page_size
    to_idx = from_idx + page_size - 1

    q = (
        sb.table("scan_candidates")
        .select(_LIST_SELECT, count="exact")
        .eq("tenant_id", tenant_id)
    )

    if stage is not None:
        q = q.eq("stage", stage)
    if territory_id:
        q = q.eq("territory_id", territory_id)
    if solar_verdict:
        q = q.eq("solar_verdict", solar_verdict)

    try:
        result = (
            q.order("created_at", desc=True)
            .range(from_idx, to_idx)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("contatti.list_failed", tenant_id=tenant_id, err=str(exc))
        return {"rows": [], "total": 0, "page": page, "page_size": page_size}

    return {
        "rows": result.data or [],
        "total": result.count or 0,
        "page": page,
        "page_size": page_size,
    }


@router.get("/summary")
async def contatti_summary(ctx: CurrentUser) -> dict[str, Any]:
    """Aggregate counts per funnel stage + solar_verdict breakdown.

    Used by the /contatti page header and the /funnel waterfall.
    One query: GROUP BY stage + GROUP BY solar_verdict on stage=4.
    We do two tiny queries rather than a complex GROUP BY so PostgREST
    can inline the count without a custom RPC.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    try:
        # Stage counts — we count per stage using .eq().count("exact").
        # Four parallel lightweight head-only queries.
        import asyncio  # noqa: PLC0415 (local import for clarity)

        def _count(stage_val: int) -> int:
            r = (
                sb.table("scan_candidates")
                .select("id", count="exact")
                .eq("tenant_id", tenant_id)
                .gte("stage", stage_val)
                .execute()
            )
            return r.count or 0

        def _count_solar(verdict: str) -> int:
            r = (
                sb.table("scan_candidates")
                .select("id", count="exact")
                .eq("tenant_id", tenant_id)
                .eq("stage", 4)
                .eq("solar_verdict", verdict)
                .execute()
            )
            return r.count or 0

        l1 = _count(1)
        l2 = _count(2)
        l3 = _count(3)
        l4_accepted = _count_solar("accepted")
        l4_rejected_tech = _count_solar("rejected_tech")
        l4_no_building = _count_solar("no_building")
        l4_skipped = _count_solar("skipped_below_gate")

    except Exception as exc:  # noqa: BLE001
        log.warning("contatti.summary_failed", tenant_id=tenant_id, err=str(exc))
        return {
            "l1": 0, "l2": 0, "l3": 0,
            "l4_accepted": 0, "l4_rejected_tech": 0,
            "l4_no_building": 0, "l4_skipped": 0,
        }

    return {
        "l1": l1,
        "l2": l2,
        "l3": l3,
        "l4_accepted": l4_accepted,
        "l4_rejected_tech": l4_rejected_tech,
        "l4_no_building": l4_no_building,
        "l4_skipped": l4_skipped,
    }
