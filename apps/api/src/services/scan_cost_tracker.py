"""Scan cost tracker — per-scan API spend telemetry for the funnel v2.

The old modes (b2b_precision, opportunistic, volume) lump all costs into
`api_usage_log` with a single `api_cost_cents` number; that's fine for
monthly billing but useless for understanding *where* a scan spent its
budget. The funnel v2 has four cost centres (Atoka / Places / Claude /
Solar) that behave very differently:

  * Atoka is cheap-but-bulk (€0.01 × 5000 records = €50 on a big L1)
  * Solar is expensive-per-call but volume-gated (€0.03 × 500 records)
  * Claude is tiny (€0.001 × 5000 records)
  * Places is medium but only on L2 survivors

Per-scan breakdown lets the dashboard:
  (a) tell installers *why* a scan cost what it did,
  (b) detect runaway costs early (e.g. L2 blowing past budget cap),
  (c) compare funnel efficiency (cost per qualified lead) across scans.

The tracker is deliberately lightweight: one row per scan in
`scan_cost_log`, incremented via UPSERTs from each funnel level. No
hot-path queries — we only read for the `/v1/scans/{id}/costs` endpoint.

See migration `0031_scan_candidates.sql` for the schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)


@dataclass(slots=True)
class ScanCostAccumulator:
    """In-memory running totals for a single scan — flushed at each
    level boundary so partial progress survives a worker crash.

    Fields mirror the `scan_cost_log` columns 1:1. Integers in cents to
    avoid float drift on API billing reconciliation.
    """

    tenant_id: str
    scan_id: str
    scan_mode: str
    territory_id: str | None = None

    atoka_cost_cents: int = 0
    places_cost_cents: int = 0
    claude_cost_cents: int = 0
    solar_cost_cents: int = 0
    mapbox_cost_cents: int = 0

    candidates_l1: int = 0
    candidates_l2: int = 0
    candidates_l3: int = 0
    candidates_l4: int = 0
    leads_qualified: int = 0

    # Module-local copy of the per-cost-centre increments since last flush
    # so we can track velocity for budget-cap enforcement.
    _dirty: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    # Bump helpers — called from each funnel level
    # ------------------------------------------------------------------

    def add_atoka(self, records: int, cost_cents: int) -> None:
        self.atoka_cost_cents += cost_cents
        self.candidates_l1 += records
        self._dirty = True

    def add_places(self, calls: int, cost_cents: int) -> None:
        self.places_cost_cents += cost_cents
        self.candidates_l2 += calls
        self._dirty = True

    def add_claude(self, scored: int, cost_cents: int) -> None:
        self.claude_cost_cents += cost_cents
        self.candidates_l3 += scored
        self._dirty = True

    def add_solar(self, calls: int, cost_cents: int) -> None:
        self.solar_cost_cents += cost_cents
        self.candidates_l4 += calls
        self._dirty = True

    def add_mapbox(self, cost_cents: int) -> None:
        # Mapbox costs are tiny (<€0.01/geocode) but we track separately
        # because they scale with candidate count, not with lead count.
        self.mapbox_cost_cents += cost_cents
        self._dirty = True

    def mark_lead_qualified(self, count: int = 1) -> None:
        self.leads_qualified += count
        self._dirty = True

    @property
    def total_cost_cents(self) -> int:
        return (
            self.atoka_cost_cents
            + self.places_cost_cents
            + self.claude_cost_cents
            + self.solar_cost_cents
            + self.mapbox_cost_cents
        )

    # ------------------------------------------------------------------
    # Persistence — idempotent via UPSERT on (tenant_id, scan_id)
    # ------------------------------------------------------------------

    async def flush(self, *, completed: bool = False) -> None:
        """Persist the running totals. Safe to call repeatedly.

        We re-write the whole row (not incremental) so a crashed worker
        resuming mid-funnel doesn't double-count: the canonical total is
        always whatever the accumulator currently holds.
        """
        if not self._dirty and not completed:
            return

        sb = get_service_client()
        row: dict[str, object] = {
            "tenant_id": self.tenant_id,
            "scan_id": self.scan_id,
            "territory_id": self.territory_id,
            "scan_mode": self.scan_mode,
            "atoka_cost_cents": self.atoka_cost_cents,
            "places_cost_cents": self.places_cost_cents,
            "claude_cost_cents": self.claude_cost_cents,
            "solar_cost_cents": self.solar_cost_cents,
            "mapbox_cost_cents": self.mapbox_cost_cents,
            "total_cost_cents": self.total_cost_cents,
            "candidates_l1": self.candidates_l1,
            "candidates_l2": self.candidates_l2,
            "candidates_l3": self.candidates_l3,
            "candidates_l4": self.candidates_l4,
            "leads_qualified": self.leads_qualified,
        }
        if completed:
            # Supabase uses `now()` server-side via the default, but we set
            # it explicitly here to satisfy the UPSERT (UPDATE path doesn't
            # re-run column defaults).
            from datetime import datetime, timezone

            row["completed_at"] = datetime.now(timezone.utc).isoformat()

        try:
            sb.table("scan_cost_log").upsert(
                row, on_conflict="tenant_id,scan_id"
            ).execute()
            self._dirty = False
        except Exception as exc:  # noqa: BLE001
            # Telemetry must never take down the scan itself.
            log.warning("scan_cost_flush_failed", err=str(exc), scan_id=self.scan_id)

    def over_budget(self, budget_eur: float | None) -> bool:
        """Return True when the scan has already spent beyond the tenant's
        monthly budget. Callers use this to short-circuit L2/L3/L4 before
        starting another batch.
        """
        if budget_eur is None or budget_eur <= 0:
            return False
        return self.total_cost_cents >= int(budget_eur * 100)
