"""Tenant operational config — DAO + wizard helpers (Sprint 9).

This module is the single entry point for reading/writing
`tenant_configs` rows. Every caller goes through the typed
`TenantConfig` dataclass — no raw dicts leaking into agent code.

Two primary audiences:

1. **HunterAgent dispatcher** — calls `get_for_tenant(tenant_id)` to
   decide which `_run_*` branch to execute. Never blocks on missing
   config: `DEFAULT_CONFIG` is returned if the row is missing (should
   only happen in unit tests; real tenants always have one after the
   backfill in migration 0013).

2. **Onboarding wizard** (FastAPI route `POST /v1/tenant-config`) —
   calls `upsert_from_wizard()` which validates the payload, expands
   the ATECO whitelist into Google Places types via the mapping
   table, and stamps `wizard_completed_at`.

Budget counters live on `api_usage_log` (per-request, already there
from Sprint 1). Enforcement happens inside the Hunter loop by
comparing the monthly sum against `monthly_scan_budget_eur`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)

ScanMode = Literal["b2b_precision", "opportunistic", "volume"]
Segment = Literal["b2b", "b2c"]


# ---------------------------------------------------------------------------
# Typed config
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class TechnicalFilters:
    """Per-segment thresholds applied after a Google Solar scan."""

    min_area_sqm: float
    min_kwp: float
    max_shading: float
    min_exposure_score: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TechnicalFilters":
        return cls(
            min_area_sqm=float(d.get("min_area_sqm", 0)),
            min_kwp=float(d.get("min_kwp", 0)),
            max_shading=float(d.get("max_shading", 1.0)),
            min_exposure_score=float(d.get("min_exposure_score", 0)),
        )


@dataclass(slots=True, frozen=True)
class TenantConfig:
    """Immutable view of a tenant_configs row.

    All fields required by HunterAgent / ScoringAgent are first-class
    attributes; rarer knobs stay in the raw JSON fields.
    """

    tenant_id: UUID
    scan_mode: ScanMode
    target_segments: tuple[Segment, ...]

    # Google Places discovery (scan_mode='b2b_precision')
    place_type_whitelist: tuple[str, ...]
    place_type_priority: dict[str, int]

    # ATECO (Tier 2 enrichment metadata)
    ateco_whitelist: tuple[str, ...]
    ateco_blacklist: tuple[str, ...]
    ateco_priority: dict[str, int]

    # Size filters (meaningful only post-Atoka)
    min_employees: int | None
    max_employees: int | None
    min_revenue_eur: int | None
    max_revenue_eur: int | None

    # Technical filters per segment
    technical_b2b: TechnicalFilters
    technical_b2c: TechnicalFilters

    # Scoring
    scoring_threshold: int
    scoring_weights: dict[str, dict[str, int]]

    # Budgets
    monthly_scan_budget_eur: float
    monthly_outreach_budget_eur: float

    # Scan strategy
    scan_priority_zones: tuple[str, ...]
    scan_grid_density_m: int

    # Enrichment Tier 2
    atoka_enabled: bool
    atoka_monthly_cap_eur: float

    # Wizard
    wizard_completed_at: datetime | None

    # ------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------

    def filters_for(self, segment: Segment) -> TechnicalFilters:
        return self.technical_b2b if segment == "b2b" else self.technical_b2c

    def targets(self, segment: Segment) -> bool:
        return segment in self.target_segments

    @property
    def wizard_pending(self) -> bool:
        return self.wizard_completed_at is None


# ---------------------------------------------------------------------------
# Defaults — used as a fallback when the tenant has no row yet
# ---------------------------------------------------------------------------


_DEFAULT_TECHNICAL_B2B = TechnicalFilters(500, 50, 0.4, 0.7)
_DEFAULT_TECHNICAL_B2C = TechnicalFilters(60, 3, 0.5, 0.6)


def _default_for(tenant_id: UUID) -> TenantConfig:
    """Safe in-memory default. Never persisted."""
    return TenantConfig(
        tenant_id=tenant_id,
        scan_mode="opportunistic",
        target_segments=("b2b", "b2c"),
        place_type_whitelist=("establishment",),
        place_type_priority={},
        ateco_whitelist=(),
        ateco_blacklist=(),
        ateco_priority={},
        min_employees=None,
        max_employees=None,
        min_revenue_eur=None,
        max_revenue_eur=None,
        technical_b2b=_DEFAULT_TECHNICAL_B2B,
        technical_b2c=_DEFAULT_TECHNICAL_B2C,
        scoring_threshold=60,
        scoring_weights={
            "b2b": {"kwp": 25, "consumption": 25, "solvency": 20, "incentives": 15, "distance": 15},
            "b2c": {"kwp": 20, "consumption": 25, "solvency": 15, "incentives": 20, "distance": 20},
        },
        monthly_scan_budget_eur=1500.0,
        monthly_outreach_budget_eur=2000.0,
        scan_priority_zones=("capoluoghi",),
        scan_grid_density_m=30,
        atoka_enabled=False,
        atoka_monthly_cap_eur=0.0,
        wizard_completed_at=None,
    )


# ---------------------------------------------------------------------------
# Parsing — Supabase row → TenantConfig
# ---------------------------------------------------------------------------


def _parse_row(row: dict[str, Any]) -> TenantConfig:
    tf = row.get("technical_filters") or {}
    wca = row.get("wizard_completed_at")
    wizard_dt: datetime | None
    if wca:
        # Supabase returns ISO-8601 with trailing 'Z' sometimes
        wizard_dt = datetime.fromisoformat(wca.replace("Z", "+00:00"))
    else:
        wizard_dt = None

    return TenantConfig(
        tenant_id=UUID(row["tenant_id"]),
        scan_mode=row["scan_mode"],
        target_segments=tuple(row.get("target_segments") or ("b2b",)),
        place_type_whitelist=tuple(row.get("place_type_whitelist") or ("establishment",)),
        place_type_priority=dict(row.get("place_type_priority") or {}),
        ateco_whitelist=tuple(row.get("ateco_whitelist") or ()),
        ateco_blacklist=tuple(row.get("ateco_blacklist") or ()),
        ateco_priority=dict(row.get("ateco_priority") or {}),
        min_employees=row.get("min_employees"),
        max_employees=row.get("max_employees"),
        min_revenue_eur=row.get("min_revenue_eur"),
        max_revenue_eur=row.get("max_revenue_eur"),
        technical_b2b=TechnicalFilters.from_dict(tf.get("b2b") or {}),
        technical_b2c=TechnicalFilters.from_dict(tf.get("b2c") or {}),
        scoring_threshold=int(row.get("scoring_threshold") or 60),
        scoring_weights=dict(row.get("scoring_weights") or {}),
        monthly_scan_budget_eur=float(row.get("monthly_scan_budget_eur") or 0),
        monthly_outreach_budget_eur=float(row.get("monthly_outreach_budget_eur") or 0),
        scan_priority_zones=tuple(row.get("scan_priority_zones") or ()),
        scan_grid_density_m=int(row.get("scan_grid_density_m") or 30),
        atoka_enabled=bool(row.get("atoka_enabled") or False),
        atoka_monthly_cap_eur=float(row.get("atoka_monthly_cap_eur") or 0),
        wizard_completed_at=wizard_dt,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_for_tenant(tenant_id: UUID | str) -> TenantConfig:
    """Fetch the tenant's operational config. Returns `DEFAULT_CONFIG`
    (opportunistic, wizard_pending=True) if no row exists yet — used
    during unit tests and edge cases only.
    """
    sb = get_service_client()
    tid = str(tenant_id)
    res = sb.table("tenant_configs").select("*").eq("tenant_id", tid).maybe_single().execute()
    row = getattr(res, "data", None)
    if not row:
        log.warning("tenant_config.missing", extra={"tenant_id": tid})
        return _default_for(UUID(tid))
    return _parse_row(row)


async def list_ateco_options() -> list[dict[str, Any]]:
    """Return the wizard dropdown options grouped by `wizard_group`.

    The frontend renders one accordion per group. We include
    `google_types` so the client can preview which Places types will
    be queried; the server recomputes server-side on submit to avoid
    trust issues.
    """
    sb = get_service_client()
    res = (
        sb.table("ateco_google_types")
        .select("ateco_code, ateco_label, wizard_group, google_types, priority_hint, target_segment")
        .order("wizard_group")
        .order("priority_hint", desc=True)
        .execute()
    )
    return list(res.data or [])


@dataclass(slots=True)
class WizardPayload:
    """Shape the wizard endpoint accepts. Server validates before upsert."""

    scan_mode: ScanMode
    target_segments: list[Segment]
    ateco_codes: list[str]  # selected rows from ateco_google_types
    # Step 3 — technical
    min_kwp_b2b: float | None = None
    min_kwp_b2c: float | None = None
    max_shading: float = 0.5
    min_exposure_score: float = 0.6
    # Step 4 — territory & budget
    scan_priority_zones: list[str] = field(default_factory=lambda: ["capoluoghi"])
    monthly_scan_budget_eur: float = 1500.0
    monthly_outreach_budget_eur: float = 2000.0
    # Step 5 — scoring threshold bucket
    scoring_threshold: int = 60


async def upsert_from_wizard(tenant_id: UUID | str, payload: WizardPayload) -> TenantConfig:
    """Apply a validated wizard submission to the tenant's config.

    Steps:
      1. Resolve `payload.ateco_codes` → Google Places types via the
         mapping table (server-side — never trust the client's
         `place_type_whitelist`).
      2. Build the update dict with all wizard-owned fields.
      3. Upsert on `tenant_id`; stamp `wizard_completed_at = now()`.

    The returned `TenantConfig` reflects the freshly persisted state.
    """
    sb = get_service_client()
    tid = str(tenant_id)

    # Step 1 — expand ATECO → Google types
    place_types: set[str] = set()
    place_priority: dict[str, int] = {}
    ateco_priority: dict[str, int] = {}

    if payload.ateco_codes:
        res = (
            sb.table("ateco_google_types")
            .select("ateco_code, google_types, priority_hint")
            .in_("ateco_code", payload.ateco_codes)
            .execute()
        )
        for row in res.data or []:
            for gt in row["google_types"]:
                place_types.add(gt)
                # Highest priority_hint across ateco codes mapped to this type wins.
                place_priority[gt] = max(place_priority.get(gt, 0), row["priority_hint"])
            ateco_priority[row["ateco_code"]] = row["priority_hint"]

    # Fallback so we don't persist an empty whitelist
    if not place_types:
        place_types = {"establishment"}

    # Step 2 — build technical_filters override (only the fields the
    # wizard edits; others keep schema defaults).
    tech: dict[str, dict[str, float]] = {"b2b": {}, "b2c": {}}
    if payload.min_kwp_b2b is not None:
        tech["b2b"]["min_kwp"] = payload.min_kwp_b2b
    if payload.min_kwp_b2c is not None:
        tech["b2c"]["min_kwp"] = payload.min_kwp_b2c
    for seg in ("b2b", "b2c"):
        tech[seg]["max_shading"] = payload.max_shading
        tech[seg]["min_exposure_score"] = payload.min_exposure_score

    # Step 3 — upsert. We merge technical_filters rather than replacing
    # wholesale, to keep server-side defaults (min_area_sqm etc.).
    existing = sb.table("tenant_configs").select("technical_filters").eq("tenant_id", tid).maybe_single().execute()
    base_tech = (getattr(existing, "data", None) or {}).get("technical_filters") or {}
    merged_tech = {
        "b2b": {**(base_tech.get("b2b") or {}), **tech["b2b"]},
        "b2c": {**(base_tech.get("b2c") or {}), **tech["b2c"]},
    }

    update = {
        "tenant_id": tid,
        "scan_mode": payload.scan_mode,
        "target_segments": list(payload.target_segments),
        "place_type_whitelist": sorted(place_types),
        "place_type_priority": place_priority,
        "ateco_whitelist": list(payload.ateco_codes),
        "ateco_priority": ateco_priority,
        "technical_filters": merged_tech,
        "scoring_threshold": int(payload.scoring_threshold),
        "scan_priority_zones": list(payload.scan_priority_zones),
        "monthly_scan_budget_eur": float(payload.monthly_scan_budget_eur),
        "monthly_outreach_budget_eur": float(payload.monthly_outreach_budget_eur),
        "wizard_completed_at": "now()",
    }

    # Supabase upsert on the UNIQUE(tenant_id) constraint — updates in place.
    sb.table("tenant_configs").upsert(update, on_conflict="tenant_id").execute()

    log.info(
        "tenant_config.wizard_upsert",
        extra={
            "tenant_id": tid,
            "scan_mode": payload.scan_mode,
            "ateco_count": len(payload.ateco_codes),
            "places_count": len(place_types),
        },
    )
    return await get_for_tenant(tid)
