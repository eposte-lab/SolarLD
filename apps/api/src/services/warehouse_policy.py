"""Warehouse policy reader (Sprint 11).

Pure helpers that translate a `tenants` row into the operational
parameters that the daily orchestrator + cleanup worker + dashboard
all need to agree on. Everything is read-only and side-effect free
so the same code path is safe in tests, workers, and request handlers.

The DB CHECK constraints in migration 0072 guarantee the values are
internally consistent (min ≤ target ≤ max, buffer ≤ expiration, …),
so we don't re-validate them here — we just provide named accessors
with defensive defaults for tenants that predate the migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Defaults mirror the column defaults in migration 0072. Kept here as
# a safety net for unit tests or stale rows; if you change a default,
# change it in both places.
_DEFAULT_DAILY_CAP = 250
_DEFAULT_CAP_MIN = 50
_DEFAULT_CAP_MAX = 250
_DEFAULT_BUFFER_DAYS = 7
_DEFAULT_EXPIRATION_DAYS = 21
_DEFAULT_SURVIVAL_TARGET = 0.80


@dataclass(frozen=True, slots=True)
class WarehousePolicy:
    """Operational parameters for one tenant's warehouse pipeline."""

    daily_send_cap: int            # effective send cap today
    daily_send_cap_min: int        # admin-slider lower bound
    daily_send_cap_max: int        # admin-slider upper bound
    warehouse_buffer_days: int     # refill trigger threshold
    lead_expiration_days: int      # auto-expire after N days in ready_to_send
    atoka_survival_target: float   # below → admin alert

    # ----- derived -----------------------------------------------------

    @property
    def warehouse_min_size(self) -> int:
        """Minimum warehouse depth that avoids triggering a refill."""
        return self.daily_send_cap * self.warehouse_buffer_days

    def runway_days(self, ready_count: int) -> float | None:
        """How many days of sending the warehouse covers."""
        if self.daily_send_cap <= 0:
            return None
        return round(ready_count / self.daily_send_cap, 1)

    def needs_refill(self, ready_count: int) -> bool:
        return ready_count < self.warehouse_min_size


def policy_for(tenant_row: dict[str, Any]) -> WarehousePolicy:
    """Build a WarehousePolicy from a `tenants` row.

    Tolerates missing keys (returns defaults) so the function is safe
    on rows fetched with a partial select.
    """
    return WarehousePolicy(
        daily_send_cap=_int(tenant_row.get("daily_target_send_cap"), _DEFAULT_DAILY_CAP),
        daily_send_cap_min=_int(
            tenant_row.get("daily_send_cap_min"), _DEFAULT_CAP_MIN
        ),
        daily_send_cap_max=_int(
            tenant_row.get("daily_send_cap_max"), _DEFAULT_CAP_MAX
        ),
        warehouse_buffer_days=_int(
            tenant_row.get("warehouse_buffer_days"), _DEFAULT_BUFFER_DAYS
        ),
        lead_expiration_days=_int(
            tenant_row.get("lead_expiration_days"), _DEFAULT_EXPIRATION_DAYS
        ),
        atoka_survival_target=_float(
            tenant_row.get("atoka_survival_target"), _DEFAULT_SURVIVAL_TARGET
        ),
    )


def _int(v: Any, fallback: int) -> int:
    if isinstance(v, bool):
        return fallback  # avoid bools coerced to int
    if isinstance(v, (int, float)):
        n = int(v)
        return n if n > 0 else fallback
    return fallback


def _float(v: Any, fallback: float) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = float(v)
        return f if f > 0 else fallback
    return fallback


__all__ = ["WarehousePolicy", "policy_for"]
