"""Inbox selector — multi-inbox round-robin for outreach sends.

Picks the best available sending inbox for a tenant and atomically
claims one send slot from its daily cap. Falls back gracefully to None
when no inboxes are configured (caller uses the legacy single-inbox
path) or when all inboxes are at cap / paused.

Design
------
* **Round-robin** by ``last_sent_at ASC NULLS FIRST`` — the inbox that
  has been idle the longest goes next. Distributes load evenly without
  a separate queue.

* **Python-side capacity filtering**: we fetch all active inboxes for the
  tenant (typically 2–10 rows) and filter paused/capped inboxes in
  Python. Avoids PostgREST column-comparison limitations.

* **Atomic claim** via UPDATE with an optimistic-lock guard on
  ``total_sent_today``. If a concurrent worker incremented the counter
  between our SELECT and this UPDATE, we get 0 rows and move to the
  next candidate. No lost increments, no over-cap sends.

* **Lazy daily reset**: if ``sent_date < today``, the counter is stale.
  The UPDATE resets it to 1 (first send of the day) only if ``sent_date``
  is still pre-today (guard against concurrent same-day race). No cron
  required.

* **Auto-pause** after provider errors. ``pause_inbox()`` sets
  ``paused_until = now() + N hours`` so the inbox is excluded from
  selection for that window. The other inboxes carry the load.
  Resend 429 → 2 h pause; Resend 5xx → 4 h pause.

Usage
-----
::

    inbox = await pick_and_claim(sb, tenant_id)
    if inbox is None:
        # Either no inboxes configured (fall back to legacy) or all capped.
        ...
    else:
        from_address = build_from_address(inbox)
        # On ResendError with 5xx / 429:
        await pause_inbox(sb, inbox['id'], hours=4, reason="resend_5xx")
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.logging import get_logger
from .rate_limit_service import inbox_effective_daily_cap
from .send_window_service import is_inbox_human_delay_ok

log = get_logger(__name__)

# Pause durations by error type.
PAUSE_HOURS_5XX = 4
PAUSE_HOURS_429 = 2

_SELECT_FIELDS = (
    "id, tenant_id, email, display_name, reply_to_email, "
    "signature_html, daily_cap, paused_until, pause_reason, "
    "sent_date, total_sent_today, last_sent_at, active, "
    "provider, domain_id, warmup_started_at"
)

# Joined domain fields we embed on each inbox row for the OutreachAgent.
# We join tenant_email_domains so the agent can pick up tracking_host and
# domain-level pause state without an extra round-trip.
_DOMAIN_JOIN_FIELDS = (
    "domain_id, "
    "tenant_email_domains!tenant_inboxes_domain_id_fkey"
    "(id, domain, purpose, tracking_host, paused_until, pause_reason, default_provider)"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def pick_and_claim(
    sb: Any,
    tenant_id: str,
    *,
    campaign_inbox_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """Select and atomically claim one send slot from an available inbox.

    Returns the inbox row (dict) on success, or ``None`` if:
      - No inboxes are configured for the tenant (caller falls back to
        the single-inbox legacy path).
      - All configured inboxes have hit their ``daily_cap`` for today.
      - All configured inboxes are currently paused.
      - ``campaign_inbox_ids`` is non-empty and none of the specified
        inboxes are available.

    Concurrent workers are safe: the UPDATE is atomic + optimistic lock.
    If two workers try to claim the last slot simultaneously, exactly one
    succeeds and the other moves to the next candidate inbox.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── 1. Fetch all active inboxes + their domain row ────────────────────
    # We try the JOIN-select first (migration 0050 adds domain_id).
    # If the JOIN fails (e.g. migration not yet applied in dev), fall back
    # to the plain select so we never break an already-running send.
    #
    # NOTE: postgrest-py requires the chain order
    #   .table() → .select() → .eq()/.order()/.in_()
    # calling .eq() directly on the SyncRequestBuilder returned by .table()
    # raises AttributeError.  We therefore rebuild the filter chain after
    # each .select().
    select_with_domain = f"{_SELECT_FIELDS}, {_DOMAIN_JOIN_FIELDS}"

    def _build(select_cols: str):  # type: ignore[no-untyped-def]
        q = (
            sb.table("tenant_inboxes")
            .select(select_cols)
            .eq("tenant_id", tenant_id)
            .eq("active", True)
            .order("last_sent_at", desc=False, nullsfirst=True)
        )
        if campaign_inbox_ids:
            q = q.in_("id", campaign_inbox_ids)
        return q

    try:
        result = _build(select_with_domain).execute()
    except Exception:  # noqa: BLE001 — JOIN may fail pre-migration
        try:
            result = _build(_SELECT_FIELDS).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("inbox_selector.fetch_failed", tenant_id=tenant_id, err=str(exc))
            return None

    all_inboxes: list[dict[str, Any]] = result.data or []

    if not all_inboxes:
        # No active inboxes → caller uses legacy path.
        return None

    # ── 2. Python-side filtering: skip paused, at-cap, and too-recent inboxes
    # Also skip inboxes whose parent domain is paused.
    now_dt = datetime.now(timezone.utc)  # used by human-delay check below
    available: list[dict[str, Any]] = []
    for inbox in all_inboxes:
        # Inbox-level pause.
        paused_until = inbox.get("paused_until")
        if paused_until and paused_until > now_iso:
            continue

        # Domain-level pause (if domain row was joined).
        domain_row = inbox.get("tenant_email_domains") or {}
        if isinstance(domain_row, dict):
            domain_paused = domain_row.get("paused_until")
            if domain_paused and domain_paused > now_iso:
                continue

        # Human-delay guard (Task 19): skip inboxes that sent too recently.
        # MIN_INTER_SEND_SECONDS (180 s) must have elapsed since last_sent_at.
        # This prevents back-to-back sends from the same inbox that look
        # robotic to Gmail's spam classifier. Combined with LRU rotation,
        # the 12-inbox fleet naturally spaces sends 3-5 min apart.
        if not is_inbox_human_delay_ok(inbox, now=now_dt):
            log.debug(
                "inbox_selector.human_delay_skip",
                inbox_id=inbox.get("id"),
                last_sent_at=inbox.get("last_sent_at"),
            )
            continue

        sent_date = inbox.get("sent_date") or ""
        total_sent = int(inbox.get("total_sent_today") or 0)
        # Apply the 21-day per-inbox warm-up curve for outreach inboxes;
        # brand inboxes return their configured daily_cap unchanged.
        cap = inbox_effective_daily_cap(inbox)

        if sent_date == today and total_sent >= cap:
            continue  # daily cap exhausted for today (warm-up or steady-state)

        # Counter is stale (yesterday or older) → treat as 0 used.
        available.append(inbox)

    if not available:
        log.info(
            "inbox_selector.all_inboxes_blocked",
            tenant_id=tenant_id,
            total=len(all_inboxes),
        )
        return None

    # ── 3. Attempt atomic claim on each candidate (round-robin order) ─────
    for candidate in available:
        inbox_id: str = candidate["id"]
        # Use the warm-up-aware effective cap (Sprint 6.3).
        cap = inbox_effective_daily_cap(candidate)
        sent_date = candidate.get("sent_date") or ""
        total_sent = int(candidate.get("total_sent_today") or 0)
        is_new_day = sent_date != today

        # Warm-up start: if this inbox has never sent before, mark now
        # as warmup_started_at so rate_limit_service can compute the
        # warm-up phase (day 1-21) for all subsequent sends.
        needs_warmup_start = (
            candidate.get("warmup_started_at") is None
            and candidate.get("email_style") != "visual_preventivo"
        )

        if is_new_day:
            # Lazy daily reset: set counter to 1 (first send today).
            # Guard: only apply if another worker hasn't already reset it
            # to today (concurrent first-send race).
            #
            # NOTE: We can't use plain `.neq("sent_date", today)` because in
            # PostgREST `col != value` doesn't match rows where `col IS NULL`
            # (SQL: NULL != anything is UNKNOWN, not TRUE). A brand-new inbox
            # has sent_date = NULL, so the neq guard would silently match 0
            # rows and we'd report "all inboxes blocked" on first-ever send.
            # Use `.or_("sent_date.is.null,sent_date.neq.today")` so NULL and
            # stale dates both claim.
            update_payload: dict[str, Any] = {
                "sent_date": today,
                "total_sent_today": 1,
                "last_sent_at": now_iso,
                "updated_at": now_iso,
            }
            if needs_warmup_start:
                update_payload["warmup_started_at"] = now_iso
            q = (
                sb.table("tenant_inboxes")
                .update(update_payload)
                .eq("id", inbox_id)
                .eq("tenant_id", tenant_id)
                # Guard covers both "never sent before" (NULL) and "last sent
                # was a prior day" — both are legitimate first-send-today.
                .or_(f"sent_date.is.null,sent_date.neq.{today}")
            )
        else:
            # Same day: increment under cap.
            # The `.eq("total_sent_today", total_sent)` is the optimistic lock.
            update_payload = {
                "total_sent_today": total_sent + 1,
                "last_sent_at": now_iso,
                "updated_at": now_iso,
            }
            if needs_warmup_start:
                update_payload["warmup_started_at"] = now_iso
            q = (
                sb.table("tenant_inboxes")
                .update(update_payload)
                .eq("id", inbox_id)
                .eq("tenant_id", tenant_id)
                # Optimistic lock: only apply if nobody else incremented.
                .eq("total_sent_today", total_sent)
                # Cap guard: total_sent is what we read, ensure < cap.
                # (redundant with Python filter above, but safe belt-and-braces)
                .lt("total_sent_today", cap)
            )

        try:
            update_res = q.execute()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "inbox_selector.claim_error",
                inbox_id=inbox_id,
                err=str(exc),
            )
            continue

        if update_res.data:
            # Claimed successfully.
            claimed = update_res.data[0]
            log.info(
                "inbox_selector.claimed",
                tenant_id=tenant_id,
                inbox_id=inbox_id,
                email=candidate["email"],
                sent_today=claimed.get("total_sent_today"),
                cap=cap,
                new_day=is_new_day,
            )
            # Return merged row (static fields from SELECT + updated fields).
            return {**candidate, **claimed}

        # UPDATE matched 0 rows → concurrent worker beat us. Try next.
        log.debug(
            "inbox_selector.claim_race_lost",
            inbox_id=inbox_id,
        )

    # All candidates exhausted after racing.
    log.info(
        "inbox_selector.all_candidates_exhausted",
        tenant_id=tenant_id,
        tried=len(available),
    )
    return None


async def pause_inbox(
    sb: Any,
    inbox_id: str,
    *,
    hours: int,
    reason: str = "",
    tenant_id: str | None = None,
) -> None:
    """Put an inbox in pause mode for ``hours`` hours.

    Called by OutreachAgent when Resend returns 429 (hours=2) or 5xx
    (hours=4). The next pick_and_claim() will skip this inbox until
    ``paused_until`` has elapsed.

    Safe to call even if the inbox doesn't exist — the UPDATE is a no-op.
    """
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        q = (
            sb.table("tenant_inboxes")
            .update(
                {
                    "paused_until": until,
                    "pause_reason": reason or f"auto_pause_{hours}h",
                    "updated_at": now_iso,
                }
            )
            .eq("id", inbox_id)
        )
        if tenant_id:
            q = q.eq("tenant_id", tenant_id)
        q.execute()
        log.info(
            "inbox_selector.paused",
            inbox_id=inbox_id,
            hours=hours,
            reason=reason,
            until=until,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "inbox_selector.pause_failed",
            inbox_id=inbox_id,
            err=str(exc),
        )


async def unpause_inbox(sb: Any, inbox_id: str, *, tenant_id: str) -> bool:
    """Manually unpause an inbox from the dashboard.

    Returns True if the row was updated.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            sb.table("tenant_inboxes")
            .update(
                {
                    "paused_until": None,
                    "pause_reason": None,
                    "updated_at": now_iso,
                }
            )
            .eq("id", inbox_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
        return bool(res.data)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "inbox_selector.unpause_failed",
            inbox_id=inbox_id,
            err=str(exc),
        )
        return False


def build_from_address(inbox: dict[str, Any]) -> str:
    """Build RFC-5322 From header from an inbox row.

    ``"Display Name <user@domain.it>"``
    Fallback (no display_name): ``"user@domain.it"``
    """
    name = (inbox.get("display_name") or "").strip()
    email = (inbox.get("email") or "").strip()
    return f"{name} <{email}>" if name else email


def get_tracking_host(inbox: dict[str, Any]) -> str | None:
    """Return the custom tracking host for this inbox, or None.

    Reads from the joined ``tenant_email_domains`` row (Sprint 6.2).
    Falls back to None when the domain row is absent (pre-migration or
    legacy single-inbox path) — callers then use the default shared host.

    Example: ``"go.agendasolar.it"``
    """
    domain_row = inbox.get("tenant_email_domains") or {}
    if isinstance(domain_row, dict):
        return (domain_row.get("tracking_host") or "").strip() or None
    return None


def get_domain_purpose(inbox: dict[str, Any]) -> str:
    """Return 'brand' or 'outreach' from the joined domain row.

    Defaults to 'brand' (conservative) when domain info is unavailable
    so that legacy-path inboxes use the visual template family.
    """
    domain_row = inbox.get("tenant_email_domains") or {}
    if isinstance(domain_row, dict):
        return (domain_row.get("purpose") or "brand")
    return "brand"
