"""Self-healing render recovery.

A creative render can fail for a TRANSIENT reason (an expired Google Solar
key, a flaky AI-paint call, a Remotion hiccup). Until now the lead just sat
in the warehouse with ``rendering_image_url IS NULL`` forever — the only retry
was the manual "Rigenera" button. ``render_retry_cron`` (every 10 min) now
re-enqueues such renders automatically, with exponential backoff, so the
moment the underlying issue clears (e.g. a new Solar key is set) the stuck
leads re-render on their own.

PERMANENT failures (no coords, roof confidence too low, Solar has no building
at all) are NOT retried — a retry can't fix them.

Bookkeeping lives in ``leads.render_retry_count`` + ``render_retry_at``
(migration 0159), kept separate from the manual ``rendering_regen_count``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client

log = get_logger(__name__)

# creative_skipped_reason prefixes worth retrying (transient / config-fixable).
RETRYABLE_REASONS = frozenset(
    {
        "solar_render_error",
        "solar_api_key_not_configured",
        "replicate_token_not_configured",
        "ai_paint_error",
        "render_unexpected",
        "remotion_error",
    }
)
# Prefixes a retry can never fix — left alone (operator must act).
PERMANENT_REASONS = frozenset({"missing_coords", "roof_confidence_too_low", "solar_no_building"})

# Leads in these states don't need a render anymore.
_TERMINAL = ["blacklisted", "closed_won", "closed_lost", "expired"]

MAX_RENDER_RETRIES = 24  # ~5 days of backoff before giving up
PER_RUN_CAP = 20  # bound Solar spend per 10-min tick
_BASE_BACKOFF_S = 900  # 15 min
_MAX_BACKOFF_S = 6 * 3600  # 6 h ceiling
_FETCH_LIMIT = 200


def reason_prefix(reason: str | None) -> str:
    """`solar_render_error: 403 ...` → `solar_render_error`."""
    return (reason or "").split(":", 1)[0].strip()


def is_retryable(reason: str | None) -> bool:
    return reason_prefix(reason) in RETRYABLE_REASONS


def backoff_seconds(retry_count: int) -> int:
    """Exponential backoff capped at 6h: 15m, 30m, 1h, 2h, 4h, 6h, 6h…"""
    n = max(0, int(retry_count))
    return min(_MAX_BACKOFF_S, _BASE_BACKOFF_S * (2**n))


def _parse_ts(ts: Any) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_eligible(lead: dict[str, Any], now: datetime) -> bool:
    """Retryable reason + backoff window elapsed (never-tried → eligible now)."""
    if not is_retryable(lead.get("creative_skipped_reason")):
        return False
    count = int(lead.get("render_retry_count") or 0)
    if count >= MAX_RENDER_RETRIES:
        return False
    last = _parse_ts(lead.get("render_retry_at"))
    if last is None:
        return True
    return (now - last).total_seconds() >= backoff_seconds(count)


async def run_render_retry() -> dict[str, int]:
    """Re-enqueue renders for leads stuck on a transient failure."""
    sb = get_service_client()
    now = datetime.now(UTC)

    res = (
        sb.table("leads")
        .select(
            "id, tenant_id, pipeline_status, creative_skipped_reason, "
            "render_retry_count, render_retry_at"
        )
        .is_("rendering_image_url", "null")
        .not_.is_("creative_skipped_reason", "null")
        .not_.in_("pipeline_status", _TERMINAL)
        .lt("render_retry_count", MAX_RENDER_RETRIES)
        .order("render_retry_at", desc=False)
        .limit(_FETCH_LIMIT)
        .execute()
    )
    rows = res.data or []

    # NULL render_retry_at (never tried) first, then oldest retried.
    rows.sort(key=lambda r: (r.get("render_retry_at") is not None, r.get("render_retry_at") or ""))

    eligible = [r for r in rows if is_eligible(r, now)][:PER_RUN_CAP]

    requeued = 0
    for lead in eligible:
        lead_id = str(lead["id"])
        tenant_id = str(lead["tenant_id"])
        job_ms = int(now.timestamp() * 1000)
        try:
            await enqueue(
                "creative_task",
                {"tenant_id": tenant_id, "lead_id": lead_id, "force": True},
                job_id=f"creative:{tenant_id}:{lead_id}:{job_ms}",
            )
        except Exception:  # noqa: BLE001 — don't bump the counter if enqueue failed
            log.exception("render_retry.enqueue_failed", lead_id=lead_id)
            continue
        sb.table("leads").update(
            {
                "render_retry_count": int(lead.get("render_retry_count") or 0) + 1,
                "render_retry_at": now.isoformat(),
            }
        ).eq("id", lead_id).execute()
        requeued += 1

    log.info(
        "cron.render_retry.done",
        candidates=len(rows),
        eligible=len(eligible),
        requeued=requeued,
    )
    return {"candidates": len(rows), "eligible": len(eligible), "requeued": requeued}
