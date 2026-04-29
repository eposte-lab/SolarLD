"""Warehouse-state alerts (Sprint 11, Task 37).

Operational signals that need an admin's attention. The dashboard's
own widget already shows these to the tenant — this service additionally
posts an in-app notification (and, for super-admins, a structured log
that Sentry/Slack pipes into the ops channel) so issues don't sit
unread at the bottom of the daily digest.

Triggered from the daily pipeline orchestrator and the cleanup cron.
Each alert is *bounded* — we don't re-emit the same state every tick;
a Redis-backed dedup key with a 12h TTL keeps the noise floor low.

Alert codes
-----------
warehouse_empty            ready_to_send_count == 0
warehouse_low              ready_to_send_count < min_size
atoka_failed               last refill cycle raised an exception
low_survival_rate          last refill survival < atoka_survival_target
territory_consumption_high >85 % of the territory's eligible companies
                           have already been processed (no more headroom)
"""

from __future__ import annotations

from typing import Any, Literal

from ..core.logging import get_logger
from ..core.redis import get_redis
from .notifications_service import notify

log = get_logger(__name__)


AlertCode = Literal[
    "warehouse_empty",
    "warehouse_low",
    "atoka_failed",
    "low_survival_rate",
    "territory_consumption_high",
]

# Per-code dedup TTL — long enough to survive a single day's tick cadence
# (one warehouse_low per tenant per day is plenty), short enough that an
# unresolved problem re-pings tomorrow.
_DEDUP_TTL_S = 12 * 3600

# Severities the dashboard renders with distinct colours.
_SEVERITY: dict[AlertCode, str] = {
    "warehouse_empty": "error",
    "warehouse_low": "warning",
    "atoka_failed": "error",
    "low_survival_rate": "warning",
    "territory_consumption_high": "info",
}

# Italian messages — these are tenant-facing.
_TITLE: dict[AlertCode, str] = {
    "warehouse_empty": "Magazzino lead vuoto",
    "warehouse_low": "Magazzino lead in esaurimento",
    "atoka_failed": "Ciclo di scoperta fallito",
    "low_survival_rate": "Tasso di sopravvivenza basso",
    "territory_consumption_high": "Territorio quasi esaurito",
}


async def maybe_alert(
    *,
    tenant_id: str,
    code: AlertCode,
    body: str,
    metadata: dict[str, Any] | None = None,
    href: str = "/settings/warehouse",
) -> bool:
    """Emit an alert unless we already emitted the same code recently.

    Returns True if a new notification was inserted, False if the
    dedup window suppressed it. Failures (Redis down, notifications
    insert error) are swallowed — alerts must never block a worker.
    """
    if not await _should_emit(tenant_id, code):
        return False

    severity = _SEVERITY.get(code, "warning")
    title = _TITLE.get(code, code.replace("_", " ").title())

    try:
        await notify(
            tenant_id=tenant_id,
            title=title,
            body=body,
            severity=severity,
            href=href,
            metadata={"code": code, **(metadata or {})},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "warehouse_alert_notify_failed",
            tenant_id=tenant_id,
            code=code,
            err=str(exc),
        )

    # Always log a structured event regardless of notification success
    # — the ops Slack ingest tails on this log channel.
    log.warning(
        "warehouse_alert",
        tenant_id=tenant_id,
        code=code,
        severity=severity,
        body=body,
        metadata=metadata or {},
    )
    return True


# ----------------------------------------------------------------------
# Dedup
# ----------------------------------------------------------------------


def _dedup_key(tenant_id: str, code: AlertCode) -> str:
    return f"warehouse_alert:{tenant_id}:{code}"


async def _should_emit(tenant_id: str, code: AlertCode) -> bool:
    """Return True if this (tenant, code) pair hasn't fired in the dedup window.

    Implementation: SETNX with TTL. If the SET succeeds (key didn't
    exist), we own the right to emit. If it fails, an alert is already
    in flight and we skip. Redis-down → fail-open (emit) so we don't
    silently swallow a real outage.
    """
    key = _dedup_key(tenant_id, code)
    try:
        r = get_redis()
        # SET NX EX — atomic "create if absent + expire".
        ok = await r.set(key, "1", ex=_DEDUP_TTL_S, nx=True)
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "warehouse_alert_dedup_redis_error",
            tenant_id=tenant_id,
            code=code,
            err=str(exc),
        )
        return True


# ----------------------------------------------------------------------
# High-level helpers used by the orchestrator + cleanup cron
# ----------------------------------------------------------------------


async def emit_warehouse_state_alerts(
    *,
    tenant_id: str,
    ready_count: int,
    min_size: int,
    expiring_within_3d: int,
) -> None:
    """Inspect a warehouse snapshot and emit any alerts that apply."""
    if ready_count == 0:
        await maybe_alert(
            tenant_id=tenant_id,
            code="warehouse_empty",
            body=(
                "Il magazzino non contiene lead pronti per il prossimo invio. "
                "Verrà avviato un ciclo di scoperta straordinario."
            ),
            metadata={"ready_count": ready_count},
        )
    elif ready_count < min_size:
        await maybe_alert(
            tenant_id=tenant_id,
            code="warehouse_low",
            body=(
                f"Solo {ready_count} lead in magazzino — la soglia di refill "
                f"è {min_size}. Avvio del ciclo di scoperta in coda."
            ),
            metadata={"ready_count": ready_count, "min_size": min_size},
        )

    if expiring_within_3d > 0 and expiring_within_3d >= max(10, min_size // 5):
        # Only alert if the expiring batch is *meaningful* — a couple
        # of leads expiring is normal traffic.
        await maybe_alert(
            tenant_id=tenant_id,
            code="warehouse_low",
            body=(
                f"{expiring_within_3d} lead scadranno entro 3 giorni. "
                "Considera di alzare il cap giornaliero o avviare un "
                "ciclo di scoperta."
            ),
            metadata={"expiring_within_3d": expiring_within_3d},
        )


async def emit_atoka_failure_alert(
    *,
    tenant_id: str,
    err: str,
    territory_id: str | None = None,
) -> None:
    await maybe_alert(
        tenant_id=tenant_id,
        code="atoka_failed",
        body=(
            "Il ciclo di scoperta aziende non si è completato: "
            f"{err}. Controlla la configurazione del territorio."
        ),
        metadata={"err": err, "territory_id": territory_id},
        href="/territories",
    )


async def emit_low_survival_alert(
    *,
    tenant_id: str,
    survival_rate: float,
    target: float,
) -> None:
    await maybe_alert(
        tenant_id=tenant_id,
        code="low_survival_rate",
        body=(
            f"Tasso di sopravvivenza dei lead post-filtri: "
            f"{survival_rate:.0%} (obiettivo: {target:.0%}). "
            "Valuta di raffinare i criteri della Sorgente."
        ),
        metadata={"survival_rate": survival_rate, "target": target},
        href="/settings/sources",
    )


async def emit_territory_consumed_alert(
    *,
    tenant_id: str,
    territory_id: str,
    consumed_pct: float,
) -> None:
    await maybe_alert(
        tenant_id=tenant_id,
        code="territory_consumption_high",
        body=(
            f"Hai contattato circa il {consumed_pct:.0%} delle aziende "
            "idonee in questo territorio. Pianifica un'estensione "
            "geografica o un nuovo segmento ICP."
        ),
        metadata={"territory_id": territory_id, "consumed_pct": consumed_pct},
        href="/territories",
    )


__all__ = [
    "maybe_alert",
    "emit_warehouse_state_alerts",
    "emit_atoka_failure_alert",
    "emit_low_survival_alert",
    "emit_territory_consumed_alert",
]
