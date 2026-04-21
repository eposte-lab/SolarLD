"""Residential (B2C) discovery pipeline — `scan_mode='b2c_residential'`.

Unlike the B2B funnel, the B2C path does NOT produce individual leads
at scan time. Instead it materialises *audience segments*: groups of
CAPs (postcodes) that match the tenant's income + household-type
profile. Outreach (letter, Meta ads, door-to-door export) then runs
against a whole segment rather than individual houses. Solar
qualification is deferred to post-engagement — we only scan the roof
of people who raise their hand.

Flow:
    1. Load the tenant's `sorgente` module (B2C fields) and `outreach`
       module (which channels are on).
    2. Filter `geo_income_stats` by territory + ICP (income + household
       mix) → up to 500 CAPs.
    3. UPSERT one `b2c_audiences` row per CAP with reddito_bucket +
       stima_contatti + canali_attivi snapshot.
    4. Emit `scan.b2c_audiences_ready` with counts → dashboard lights up
       the Audiences tile; the installer then picks a channel to trigger
       (Pixart letter / Meta campaign / PDF export).
    5. Return HunterOutput with `roofs_discovered=0` (correct — roof
       qualification is deferred until post-engagement).

Cost: ~€0 at scan time. No Atoka, no Places, no Solar. The ISTAT table
is a one-off load (`scripts/load_istat_income.py`), not per-tenant.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..core.logging import get_logger
from ..services.b2c_audience_service import (
    AudienceFilters,
    materialise_audiences,
)
from ..services.tenant_config_service import TenantConfig
from ..services.tenant_module_service import get_module
from .hunter import HunterAgent, HunterInput, HunterOutput

log = get_logger(__name__)


async def run_b2c_residential(
    *,
    agent: HunterAgent,
    payload: HunterInput,
    config: TenantConfig,
    territory: dict[str, Any],
) -> HunterOutput:
    """Materialise B2C audiences for the given territory + tenant ICP.

    Does not call Solar, Atoka, or Places — only reads
    `geo_income_stats` and writes `b2c_audiences`. Safe to call on a
    brand-new tenant (yields an empty audience list with a telemetry
    warning — orchestrator doesn't crash).
    """
    scan_id = uuid4()

    # Read the tenant's Sorgente + Outreach modules. These hold the
    # B2C filters (reddito_min, case_unifamiliari) and the channel
    # toggles the audience snapshots on creation.
    sorgente = await get_module(payload.tenant_id, "sorgente")
    outreach = await get_module(payload.tenant_id, "outreach")

    filters = AudienceFilters.from_config(sorgente.config)

    channels_cfg = (outreach.config or {}).get("channels") or {}
    channels_active = [k for k, v in channels_cfg.items() if v]

    audiences = await materialise_audiences(
        tenant_id=payload.tenant_id,
        scan_id=scan_id,
        territory_id=payload.territory_id,
        territory=territory,
        filters=filters,
        channels_active=channels_active,
    )

    if not audiences:
        log.warning(
            "b2c_residential_empty",
            extra={
                "tenant_id": payload.tenant_id,
                "territory_id": payload.territory_id,
                "reddito_min": filters.reddito_min_eur,
                "unifamiliari_pct_min": filters.case_unifamiliari_pct_min,
            },
        )

    await agent._emit_event(
        event_type="scan.b2c_audiences_ready",
        payload={
            "scan_id": str(scan_id),
            "territory_id": payload.territory_id,
            "audiences": len(audiences),
            "channels_active": channels_active,
            # Rough reachable-household total across all CAPs — useful
            # for the dashboard tile headline.
            "reachable_contacts": sum(
                a.get("stima_contatti", 0) for a in audiences
            ),
        },
        tenant_id=payload.tenant_id,
    )

    out = HunterOutput()
    out.scan_mode = "b2c_residential"
    # `places_found` is reused as "audiences created" for the waterfall
    # visualisation — the existing HunterOutput schema has no B2C-
    # specific field and bumping the contract would ripple everywhere.
    out.places_found = len(audiences)
    return out
