"""Tenant operational config — projected from `tenant_modules`.

After the April 2026 v2 rollover there is no longer a `tenant_configs`
table: migration 0035 drops it. The five wizard modules (sorgente,
tecnico, economico, outreach, crm) are the single source of truth for
how a scan runs.

This module keeps the `TenantConfig` + `TechnicalFilters` value objects
that the hunter funnel and scoring agent already consume, but now
`get_for_tenant` builds them by reading the modules and projecting the
relevant fields. Code that used to live in `tenant_module_service` on
one side and `tenant_config_service` on the other is now a one-way
projection: modules in, TenantConfig out.

Two pipelines survive in v2:

  * `b2b_funnel_v2`    Atoka → Enrich → Claude score → Solar gate
  * `b2c_residential`  ISTAT income CAP → audience materialisation

The legacy `b2b_precision`, `b2b_ateco_precision`, `opportunistic`, and
`volume` modes have been deleted — the hunter dispatch no longer
recognises them (see `hunter.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from ..core.logging import get_logger

log = get_logger(__name__)

# Single source of truth for the two pipelines. Kept as a Literal so the
# hunter dispatch can exhaustively match against it.
ScanMode = Literal["b2b_funnel_v2", "b2c_residential"]
Segment = Literal["b2b", "b2c"]

# Per-tenant scoring threshold used to belong on `tenant_configs.scoring_threshold`;
# with the wizard simplified to five modules the threshold is no longer a
# tenant-facing knob. We keep a single platform-wide default that agents
# consume via `TenantConfig.scoring_threshold` so call sites don't have to
# branch on "wizard done yet". A future iteration may surface it again on
# the `tecnico` module.
_DEFAULT_SCORING_THRESHOLD = 60


# ---------------------------------------------------------------------------
# Value objects consumed by HunterFunnel + Scoring
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class TechnicalFilters:
    """Per-segment thresholds applied after a Google Solar scan.

    Retained from the v1 shape so `_apply_filters` in `hunter_funnel/level4`
    and the scoring tier logic can keep their existing call sites. The v2
    source for these numbers is the `tecnico` module.
    """

    min_area_sqm: float
    min_kwp: float
    max_shading: float
    min_exposure_score: float

    @classmethod
    def from_tecnico(cls, tecnico_cfg: dict[str, Any]) -> "TechnicalFilters":
        """Project a `tecnico` module's config dict onto TechnicalFilters."""
        return cls(
            min_area_sqm=float(tecnico_cfg.get("min_area_sqm") or 0.0),
            min_kwp=float(tecnico_cfg.get("min_kwp") or 0.0),
            max_shading=float(tecnico_cfg.get("max_shading") or 1.0),
            min_exposure_score=float(tecnico_cfg.get("min_exposure_score") or 0.0),
        )


@dataclass(slots=True, frozen=True)
class TenantConfig:
    """Immutable projection of the five tenant modules onto the shape
    HunterAgent + ScoringAgent expect.

    Only fields still consumed by v2 code are present. The v1 surface
    (place_type_whitelist, scoring_weights, scan_grid_density_m, …) is
    gone along with the pipelines that used it.
    """

    tenant_id: UUID
    scan_mode: ScanMode

    # B2B Funnel v2 — L1 Atoka inputs
    ateco_whitelist: tuple[str, ...]
    min_employees: int | None
    max_employees: int | None
    min_revenue_eur: int | None
    max_revenue_eur: int | None

    # L4 Solar-gate filters (also read as the scoring tier floor input)
    technical_b2b: TechnicalFilters

    # ScoringAgent — collapses leads below this tier to REJECTED
    scoring_threshold: int

    # Geographic preference from sorgente.regioni (e.g. ["Campania"]).
    # Used as fallback geo filter when the territory type can't yield a
    # province code (e.g. CAP without parent-province metadata).
    geo_regioni: tuple[str, ...] = ()

    # ---- Economico module ------------------------------------------------
    # Per-scan spend ceiling (€). Funnel aborts between levels once the
    # accumulated API cost crosses this threshold.
    budget_scan_eur: float = 50.0

    # Monthly outreach spend ceiling (€). OutreachAgent skips sends once
    # the sum of campaigns.cost_cents for the current calendar month
    # exceeds this.
    budget_outreach_eur_month: float = 2_000.0

    # Average contract value (€). Used by the ROI calculator to compare
    # against the lead's net-capex estimate — surfaces on the lead portal
    # as "questo impianto è nel tuo range commerciale".
    ticket_medio_eur: int = 25_000

    # Payback target (years). RoiEstimate.meets_roi_target is True when
    # payback_years ≤ this value.
    roi_target_years: int = 6

    # ---- Sector-aware multi-target (Sprint A) ---------------------------
    # Wizard group palettes the tenant has opted into (see
    # `ateco_google_types.wizard_group`). Empty tuple → legacy ATECO-only
    # mode (backward-compat for tenants configured before sector-aware
    # rollout). The hunter funnel uses these in:
    #   * L1: derive_ateco_whitelist union when ateco_codes is also empty
    #   * L1: stamp scan_candidates.predicted_sector
    #   * L2: pick site_signal_keywords union
    #   * L3: render the "Target sector" prompt section
    target_wizard_groups: tuple[str, ...] = ()
    sector_priority: dict[str, int] | None = None


# ---------------------------------------------------------------------------
# Projection: tenant_modules → TenantConfig
# ---------------------------------------------------------------------------


def _project_from_modules(
    tenant_id: UUID, modules_by_key: dict[str, dict[str, Any]]
) -> TenantConfig:
    """Build a TenantConfig from the raw `config` dicts of each module.

    Missing modules fall back to the Pydantic schema defaults so the
    projection never raises on a half-configured tenant — the hunter
    dispatch then has a consistent shape to reason about.
    """
    # Local import to avoid circularity at module-load time.
    from .tenant_module_service import schema_for

    sorgente = modules_by_key.get("sorgente") or schema_for("sorgente")().model_dump(
        mode="json"
    )
    tecnico = modules_by_key.get("tecnico") or schema_for("tecnico")().model_dump(
        mode="json"
    )
    economico = modules_by_key.get("economico") or schema_for("economico")().model_dump(
        mode="json"
    )

    mode: ScanMode = sorgente.get("mode") or "b2b_funnel_v2"

    return TenantConfig(
        tenant_id=tenant_id,
        scan_mode=mode,
        ateco_whitelist=tuple(sorgente.get("ateco_codes") or ()),
        min_employees=sorgente.get("min_employees"),
        max_employees=sorgente.get("max_employees"),
        min_revenue_eur=sorgente.get("min_revenue_eur"),
        max_revenue_eur=sorgente.get("max_revenue_eur"),
        geo_regioni=tuple(sorgente.get("regioni") or ()),
        technical_b2b=TechnicalFilters.from_tecnico(tecnico),
        scoring_threshold=_DEFAULT_SCORING_THRESHOLD,
        budget_scan_eur=float(economico.get("budget_scan_eur") or 50.0),
        budget_outreach_eur_month=float(economico.get("budget_outreach_eur_month") or 2_000.0),
        ticket_medio_eur=int(economico.get("ticket_medio_eur") or 25_000),
        roi_target_years=int(economico.get("roi_target_years") or 6),
        target_wizard_groups=tuple(sorgente.get("target_wizard_groups") or ()),
        sector_priority=dict(sorgente.get("sector_priority") or {}) or None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_for_tenant(tenant_id: UUID | str) -> TenantConfig:
    """Load a tenant's five modules and project them into `TenantConfig`.

    If the tenant has no module rows yet (brand-new signup before the
    modular wizard completes) we still return a fully-formed config
    backed by schema defaults — the downstream code (hunter L1, scoring
    tier logic) can run safely on that shape.
    """
    from .tenant_module_service import list_modules

    tid = UUID(str(tenant_id))
    modules = await list_modules(tid)
    by_key = {m.module_key: (m.config or {}) for m in modules}
    return _project_from_modules(tid, by_key)
