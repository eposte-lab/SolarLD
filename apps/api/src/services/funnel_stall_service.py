"""Funnel-stall recovery — the consumption-liveness signal worker_watchdog is blind to.

``worker_watchdog`` only fires on a GIL/sync event-loop wedge. A coroutine parked on
an unbounded ``await`` (the 2026-06-26 L4 vision hang) keeps the loop beating, so the
watchdog never trips while candidate CONSUMPTION is dead for hours. This asserts the
missing signal at the DB level: a tenant that has an ACTIVE scan job AND un-processed
candidates (work IS available) but has consumed NOTHING in ``funnel_stall_seconds`` is
stalled → emit an event and RE-ENQUEUE a fresh funnel run.

ALERT + RE-ENQUEUE ONLY — never a process bail — so it cannot crash-loop the container
(the 2026-06-18 incident). The ``job_try>1`` fast-skip stays intact, and Wave-1's
``funnel_run_timeout_seconds`` caps the hung run so its worker slot frees itself.

Also ``run_orphan_candidate_cleanup``: stale un-processed scan_candidates (dead old
scans — e.g. the 189 stuck since 06-17) pin the backlog above
``_SKIP_DISCOVERY_BACKLOG=200`` and silently suppress L1 discovery. Mark them processed
so the backlog stays honest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..core.config import settings
from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client

log = get_logger(__name__)

# Mirrors the dispatcher's notion of "a job that should be producing".
_ACTIVE_SCAN_STATES = ("pending", "in_progress", "paused_daily_cap")

ORPHAN_MAX_AGE_DAYS = 5
ORPHAN_CLEANUP_LIMIT = 1000  # bounded per tick — next daily run mops up the rest


def _emit(sb: Any, tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
    try:
        sb.table("events").insert(
            {
                "tenant_id": tenant_id,
                "event_type": event_type,
                "event_source": "funnel_stall",
                "occurred_at": datetime.now(UTC).isoformat(),
                "payload": payload,
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.debug("funnel_stall.event_failed", err=str(exc)[:120])


async def run_funnel_stall_recovery() -> dict[str, int]:
    """Re-enqueue the funnel for any tenant whose consumption has stalled while work
    is available. Returns per-outcome counts. Safe + idempotent."""
    sb = get_service_client()
    now = datetime.now(UTC)
    stall_cutoff = (now - timedelta(seconds=settings.funnel_stall_seconds)).isoformat()

    jobs = (
        sb.table("scan_jobs")
        .select("id, tenant_id, status, priority, daily_validated_cap, province_codes")
        .in_("status", list(_ACTIVE_SCAN_STATES))
        .execute()
    ).data or []

    # Top-priority active job per tenant (lower priority number wins, like the dispatcher).
    top: dict[str, dict[str, Any]] = {}
    for j in jobs:
        t = j["tenant_id"]
        if t not in top or (j.get("priority") or 0) < (top[t].get("priority") or 0):
            top[t] = j

    checked = stalled = recovered = 0
    for tid, job in top.items():
        prov = list(job.get("province_codes") or [])

        # CONSUMABLE work available? Mirror load_backlog's predicate
        # (level1_places.py): the funnel only ever consumes candidates with a
        # google_place_id (and, when the scan job is province-scoped, in those
        # provinces). Counting un-consumable rows (NULL place_id / out-of-scope
        # province) would mark a tenant "stalled" forever and re-enqueue every tick.
        unproc_q = (
            sb.table("scan_candidates")
            .select("id", count="exact")
            .eq("tenant_id", tid)
            .is_("processed_at", "null")
            .not_.is_("google_place_id", "null")
        )
        if prov:
            unproc_q = unproc_q.in_("province_code", prov)
        unproc = unproc_q.limit(1).execute()
        if (unproc.count or 0) == 0:
            continue  # nothing the funnel can consume → not a stall
        checked += 1

        # Consumed any such candidate recently? (.gt excludes NULL processed_at)
        recent_q = (
            sb.table("scan_candidates")
            .select("id", count="exact")
            .eq("tenant_id", tid)
            .gt("processed_at", stall_cutoff)
            .not_.is_("google_place_id", "null")
        )
        if prov:
            recent_q = recent_q.in_("province_code", prov)
        recent = recent_q.limit(1).execute()
        if (recent.count or 0) > 0:
            continue  # consumption is fresh → healthy

        # STALL: work available + nothing consumed for > funnel_stall_seconds.
        stalled += 1
        _emit(
            sb,
            tid,
            "funnel.stall_recovered",
            {"scan_job_id": job["id"], "backlog": unproc.count},
        )
        log.warning(
            "funnel_stall.detected",
            tenant_id=tid,
            scan_job_id=job["id"],
            backlog=unproc.count,
        )
        try:
            await enqueue(
                "hunter_funnel_v3_task",
                {
                    "tenant_id": tid,
                    "scan_job_id": job["id"],
                    "max_l1_candidates": int(job.get("daily_validated_cap") or 200) * 5,
                },
                # Unique job_id → never deduped, runs as a fresh job_try=1 (so the
                # job_try>1 fast-skip can't abandon it).
                job_id=f"funnel_v3_stall_recovery:{tid}:{int(now.timestamp())}",
            )
            recovered += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("funnel_stall.reenqueue_failed", tenant_id=tid, err=str(exc)[:160])

    result = {"checked": checked, "stalled": stalled, "recovered": recovered}
    if checked:
        log.info("funnel_stall_recovery.done", **result)
    return result


async def run_orphan_candidate_cleanup(
    *, max_age_days: int = ORPHAN_MAX_AGE_DAYS, limit: int = ORPHAN_CLEANUP_LIMIT
) -> dict[str, int]:
    """Mark long-stale un-processed scan_candidates as processed.

    Dead old-scan rows that never drain pin the unprocessed backlog above
    ``_SKIP_DISCOVERY_BACKLOG`` and silently suppress L1 discovery. Gated on
    ``created_at`` (v3 never writes ``updated_at``, so it stays frozen at insert);
    protection of an in-flight candidate relies solely on ``processed_at`` being
    stamped at end-of-run, not on age. Bounded per tick via select-ids → update-IN
    (like ``retention_cron``) so it never holds a wide lock on the hot
    scan_candidates table; the next daily run mops up any remainder. Idempotent.
    """
    sb = get_service_client()
    now = datetime.now(UTC)
    cutoff = (now - timedelta(days=max_age_days)).isoformat()
    try:
        ids_res = (
            sb.table("scan_candidates")
            .select("id")
            .is_("processed_at", "null")
            .lt("created_at", cutoff)
            .limit(limit)
            .execute()
        )
        ids = [r["id"] for r in (ids_res.data or [])]
        if not ids:
            return {"cleared": 0, "errored": 0}
        sb.table("scan_candidates").update({"processed_at": now.isoformat()}).in_(
            "id", ids
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("orphan_candidate_cleanup.failed", err=str(exc)[:160])
        return {"cleared": 0, "errored": 1}
    log.info("orphan_candidate_cleanup.done", cleared=len(ids), max_age_days=max_age_days)
    return {"cleared": len(ids), "errored": 0}
