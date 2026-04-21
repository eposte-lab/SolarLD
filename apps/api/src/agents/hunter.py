"""Hunter Agent — v2 dispatch only.

After the April 2026 cleanup the hunter has exactly two pipelines, both
driven by the `sorgente` module of `tenant_modules`:

  ┌──────────────────┬──────────────────────────────────────────────────┐
  │ scan_mode        │ Strategy                                         │
  ├──────────────────┼──────────────────────────────────────────────────┤
  │ b2b_funnel_v2    │ Atoka discovery → Places enrichment → Claude     │
  │                  │ Haiku proxy score → Solar gate on the top 20%.   │
  │                  │ Thin orchestrator over src/agents/hunter_funnel/.│
  ├──────────────────┼──────────────────────────────────────────────────┤
  │ b2c_residential  │ ISTAT income CAP filter → b2c_audiences rows.    │
  │                  │ No Atoka, Places, or Solar at scan time — Solar  │
  │                  │ runs post-engagement via b2c_qualify_service.    │
  └──────────────────┴──────────────────────────────────────────────────┘

The legacy Places-first (`b2b_precision`), transitional alias
(`b2b_ateco_precision`), grid sampling (`opportunistic`, `volume`) and
the fallback classifier / Mapbox Vision branches have all been removed.
See the git history for how the v1 pipeline used to look; migration 0035
drops the `tenant_configs` table those modes relied on.

Cost-tracking and idempotency rules:
  - L4 is the only rung that spends on Solar; `(tenant_id, geohash)`
    UPSERT makes re-runs safe.
  - L1/L2/L3 costs (Atoka, Places, Claude) land on the `ScanCostAccumulator`
    which flushes per-level to `/v1/scans/{id}/costs`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..services.google_solar_service import SolarApiError
from ..services.tenant_config_service import TenantConfig, get_for_tenant
from .base import AgentBase

log = get_logger(__name__)


class HunterInput(BaseModel):
    tenant_id: str
    territory_id: str
    max_roofs: int = Field(default=1000, ge=1, le=10000)
    start_index: int = Field(default=0, ge=0)
    step_meters: float = Field(default=50.0, gt=0.0, le=500.0)
    # Optional override for tests and admin triggers. When absent we read
    # the mode from the tenant's `sorgente` module.
    scan_mode_override: str | None = Field(default=None)
    # Radius hint reserved for future use; v2 funnel sizes its own cells.
    places_radius_m: float = Field(default=5000.0, gt=0.0, le=50000.0)


class HunterOutput(BaseModel):
    # One of: b2b_funnel_v2 | b2c_residential
    scan_mode: str = "b2b_funnel_v2"
    roofs_discovered: int = 0
    roofs_filtered_out: int = 0
    roofs_already_known: int = 0
    api_calls: int = 0
    api_cost_cents: int = 0
    used_fallback_count: int = 0
    next_pagination_token: int | None = None
    territory_exhausted: bool = False
    # Funnel v2 counters — `places_found` is reused as L1 / audience count
    places_found: int = 0
    places_deduped: int = 0
    places_details_fetched: int = 0
    subjects_created: int = 0


class HunterAgent(AgentBase[HunterInput, HunterOutput]):
    name = "agent.hunter"

    async def execute(self, payload: HunterInput) -> HunterOutput:
        """Dispatch to one of the two v2 pipelines based on tenant config."""
        sb = get_service_client()

        # 1) Load tenant config (projected from tenant_modules)
        config = await get_for_tenant(payload.tenant_id)
        mode = payload.scan_mode_override or config.scan_mode

        # 2) Load territory + bbox
        tres = (
            sb.table("territories")
            .select("id, tenant_id, bbox, name, type, code")
            .eq("id", payload.territory_id)
            .eq("tenant_id", payload.tenant_id)
            .single()
            .execute()
        )
        territory = tres.data
        if not territory:
            raise SolarApiError(f"territory {payload.territory_id} not found")
        bbox = territory.get("bbox")
        if not bbox and mode == "b2b_funnel_v2":
            # B2C only needs the territory row (for CAP/province filters),
            # not the polygon. B2B funnel still needs the bbox for geocode
            # proximity hints.
            raise SolarApiError(f"territory {payload.territory_id} has no bbox set")

        await self._emit_event(
            event_type="hunter.scan_started",
            payload={
                "territory_id": payload.territory_id,
                "scan_mode": mode,
                "max_roofs": payload.max_roofs,
                "start_index": payload.start_index,
            },
            tenant_id=payload.tenant_id,
        )

        # 3) Run the mode-specific pipeline
        if mode == "b2b_funnel_v2":
            out = await self._run_b2b_funnel_v2(
                bbox=bbox, payload=payload, config=config, territory=territory
            )
        elif mode == "b2c_residential":
            out = await self._run_b2c_residential(
                payload=payload, config=config, territory=territory
            )
        else:
            raise SolarApiError(
                f"Unsupported scan_mode={mode!r}. v2 only supports "
                "'b2b_funnel_v2' and 'b2c_residential'."
            )

        out.scan_mode = mode

        # 4) Log usage for billing
        try:
            sb.table("api_usage_log").insert(
                {
                    "tenant_id": payload.tenant_id,
                    "provider": "google_solar",
                    "endpoint": "buildingInsights:findClosest",
                    "request_count": out.api_calls,
                    "cost_cents": out.api_cost_cents,
                    "status": "success" if out.api_calls > 0 else "noop",
                    "metadata": {
                        "territory_id": payload.territory_id,
                        "scan_mode": mode,
                        "discovered": out.roofs_discovered,
                        "filtered_out": out.roofs_filtered_out,
                        "fallback": out.used_fallback_count,
                        "places_found": out.places_found,
                    },
                }
            ).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("api_usage_log_write_failed", error=str(exc))

        await self._emit_event(
            event_type="hunter.scan_completed",
            payload=out.model_dump(),
            tenant_id=payload.tenant_id,
        )
        return out

    # ------------------------------------------------------------------
    # MODE: b2b_funnel_v2 — 4-level funnel (Atoka → Enrich → Score → Solar)
    # ------------------------------------------------------------------

    async def _run_b2b_funnel_v2(
        self,
        *,
        bbox: dict[str, Any],
        payload: HunterInput,
        config: TenantConfig,
        territory: dict[str, Any],
    ) -> HunterOutput:
        """Delegate to the funnel orchestrator (imported lazily to avoid
        a circular reference — the funnel modules import HunterOutput /
        HunterInput from this file)."""
        from .hunter_funnel.orchestrator import run_funnel

        return await run_funnel(
            agent=self,
            payload=payload,
            config=config,
            territory=territory,
        )

    # ------------------------------------------------------------------
    # MODE: b2c_residential — ISTAT income CAP → audiences
    # ------------------------------------------------------------------

    async def _run_b2c_residential(
        self,
        *,
        payload: HunterInput,
        config: TenantConfig,
        territory: dict[str, Any],
    ) -> HunterOutput:
        """Residential pipeline: no Atoka, no Places. Pulls high-income CAPs
        from `geo_income_stats` and materialises `b2c_audiences` rows.
        Roof qualification happens post-engagement (see
        `b2c_post_engagement_qualify` task).
        """
        from .hunter_b2c import run_b2c_residential

        return await run_b2c_residential(
            agent=self,
            payload=payload,
            config=config,
            territory=territory,
        )
