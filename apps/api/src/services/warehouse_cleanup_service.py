"""Warehouse cleanup (Sprint 11).

A lead in `ready_to_send` past its `expires_at` is by definition stale —
the underlying business signals (employees, ATECO, contact info) may
have drifted, the territory may have shifted, and the copy assigned via
the cluster A/B engine may have rotated. Rather than send a stale email,
we expire the lead out of the warehouse and drop it on the
`reverification_queue` so an admin (or a future weekly job) can decide
whether to re-pull fresh data and re-promote.

The actual SQL is intentionally simple: a UPDATE … RETURNING that
transitions ready_to_send → expired, plus an INSERT … ON CONFLICT into
the queue. We do this in two statements rather than one CTE so the
queue insert cleanly idempotents on re-runs (UNIQUE (tenant_id, lead_id)).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)


async def expire_stale_warehouse_leads(*, batch_size: int = 1000) -> dict[str, Any]:
    """Find and expire leads past their TTL. Idempotent.

    Returns ``{"expired": N, "queued_for_reverification": M}`` for
    the caller (cron job) to log. Safe to run multiple times — a
    second pass will see no rows in `ready_to_send` past expiry.

    Bounded by `batch_size` to keep one cron tick cheap; if the
    backlog is huge the next tick mops up the rest. With the partial
    index on ``leads(expires_at) WHERE pipeline_status='ready_to_send'``
    the scan is O(stale rows) regardless of warehouse depth.
    """
    sb = get_service_client()
    now = datetime.now(timezone.utc).isoformat()

    # 1) Pick the candidates. We avoid an UPDATE … RETURNING that
    # would scale poorly under contention with the orchestrator's
    # FOR UPDATE pick — instead we select stale rows first, then
    # transition them in a second statement scoped by id list.
    candidates = (
        sb.table("leads")
        .select("id, tenant_id")
        .eq("pipeline_status", "ready_to_send")
        .lte("expires_at", now)
        .limit(batch_size)
        .execute()
    )
    rows = candidates.data or []
    if not rows:
        return {"expired": 0, "queued_for_reverification": 0}

    ids = [r["id"] for r in rows]

    # 2) Transition. Idempotent: filter by status='ready_to_send' so a
    # concurrent orchestrator's pick doesn't get clobbered.
    upd = (
        sb.table("leads")
        .update(
            {
                "pipeline_status": "expired",
                "expired_at": now,
                "last_status_transition_at": now,
            }
        )
        .in_("id", ids)
        .eq("pipeline_status", "ready_to_send")
        .execute()
    )
    expired = upd.data or []
    expired_ids = {r["id"] for r in expired}

    # 3) Reverification queue. UPSERT on (tenant_id, lead_id) so a
    # second cron tick reusing an already-queued lead is a no-op.
    if expired_ids:
        queue_rows = [
            {
                "tenant_id": r["tenant_id"],
                "lead_id": r["id"],
                "reason": "expired_in_warehouse",
            }
            for r in rows
            if r["id"] in expired_ids
        ]
        try:
            sb.table("reverification_queue").upsert(
                queue_rows,
                on_conflict="tenant_id,lead_id",
            ).execute()
        except Exception as exc:  # noqa: BLE001
            # We've already expired the leads — losing the reverification
            # tail is annoying but not a data-integrity issue. Log loudly.
            log.error(
                "warehouse_cleanup_reverify_queue_failed",
                err=str(exc),
                count=len(queue_rows),
            )

    log.info(
        "warehouse_cleanup_expired",
        expired=len(expired_ids),
        queued=len(expired_ids),
    )
    return {
        "expired": len(expired_ids),
        "queued_for_reverification": len(expired_ids),
    }


__all__ = ["expire_stale_warehouse_leads"]
