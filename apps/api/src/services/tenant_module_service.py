"""Tenant modules — DAO + Pydantic validation for the modular wizard.

Five independent configurable modules replace the monolithic wizard:

  sorgente  — *where* we look for prospects (ATECO/size/geo for B2B,
              income bands for B2C)
  tecnico   — *what* qualifies as a viable roof (kW, area, exposure,
              Solar-gate fraction)
  economico — pricing + budget caps (ticket medio, ROI target,
              per-scan spend cap)
  outreach  — active channels (email / postal / WhatsApp / Meta Ads)
              + tone + CTA
  crm       — downstream pipeline (webhook + HMAC + labels + SLA)

Each module's config lives in one `tenant_modules` row keyed by
(tenant_id, module_key). The JSONB body is validated on write by the
Pydantic models below — readers get either a typed dataclass-ish
object or a sensible default if the row is missing.

Why Pydantic here (vs dataclasses used in `tenant_config_service`):
the wizard needs both write validation (reject bad submits early) and
automatic OpenAPI schema generation for the `/v1/modules/*` routes.
Pydantic gives both for free; dataclasses would need a separate
validation layer.

Relationship with `tenant_config_service`:
  `tenant_config_service.get_for_tenant` consumes the five module
  configs and projects them into a `TenantConfig` value object the
  hunter funnel + scoring agent read. The `tenant_modules` rows are the
  sole source of truth — there is no longer a `tenant_configs` table.

The module schemas are the public contract between backend and
frontend — editing them is a coordinated change with the TS types in
`apps/dashboard/src/types/modules.ts` (generated later).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)


ModuleKey = Literal["sorgente", "tecnico", "economico", "outreach", "crm"]
MODULE_KEYS: tuple[ModuleKey, ...] = (
    "sorgente",
    "tecnico",
    "economico",
    "outreach",
    "crm",
)


# ---------------------------------------------------------------------------
# Per-module Pydantic schemas
# ---------------------------------------------------------------------------
# Each schema represents the `config` JSONB body for one module.
# Fields have sensible defaults — the wizard frontend shows these as
# pre-filled form values and the installer tweaks only what matters.


class SorgenteConfig(BaseModel):
    """Module `sorgente` — discovery source.

    Feeds L1 of the B2B funnel (Atoka search criteria) and — for
    `b2c_residential` — the CAP income filter. The B2B and B2C fields
    live in the same schema rather than two sub-objects because most
    tenants run a single scan_mode; keeping one flat shape simplifies
    the frontend. B2C-only fields are ignored by B2B flows and vice
    versa.

    The `mode` field is the single switch between the two v2 pipelines:
    `b2b_funnel_v2` runs Atoka → Enrich → Score → Solar gate; `b2c_residential`
    runs ISTAT income CAP → audience materialisation. It replaces the old
    `tenant_configs.scan_mode` column (removed in migration 0035) — there
    is no more back-compat to the deprecated Places-first modes.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Mode selector (only two v2 pipelines) ---
    mode: Literal["b2b_funnel_v2", "b2c_residential"] = Field(
        default="b2b_funnel_v2",
        description="Which v2 scan pipeline this tenant runs.",
    )

    # --- B2B fields ---
    ateco_codes: list[str] = Field(
        default_factory=list,
        description="ATECO 2007 codes (e.g. '10.51') to include in Atoka search.",
    )
    min_employees: int | None = Field(default=20, ge=0, le=100_000)
    max_employees: int | None = Field(default=250, ge=0, le=100_000)
    min_revenue_eur: int | None = Field(default=2_000_000, ge=0)
    max_revenue_eur: int | None = Field(default=50_000_000, ge=0)
    # Geography — one of these three must be non-empty for the scan
    # to target a specific area. Validation is deferred to scan time
    # since a wizard draft may have all three empty.
    province: list[str] = Field(default_factory=list)
    regioni: list[str] = Field(default_factory=list)
    cap: list[str] = Field(default_factory=list)

    # --- B2C fields (residential) ---
    reddito_min_eur: int = Field(
        default=35_000,
        ge=0,
        description="Minimum average declared income per CAP (ISTAT).",
    )
    case_unifamiliari_pct_min: int = Field(
        default=40,
        ge=0,
        le=100,
        description="Minimum % of single-family houses per CAP.",
    )

    @field_validator("ateco_codes")
    @classmethod
    def _dedupe_ateco(cls, v: list[str]) -> list[str]:
        """Preserve order, drop duplicates — installers sometimes
        paste the same code twice from two different tables."""
        seen: set[str] = set()
        out: list[str] = []
        for code in v:
            c = code.strip()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out


class TecnicoConfig(BaseModel):
    """Module `tecnico` — roof qualification thresholds + Solar gate.

    The `solar_gate_pct` knob is the single biggest cost dial in the
    funnel: raising it sends more candidates to Google Solar (~€0.03
    each) at the cost of a bigger API bill. Default 20% is conservative
    and has survived internal tuning on the v1 preview dataset.
    """

    model_config = ConfigDict(extra="forbid")

    min_kwp: float = Field(default=50.0, ge=0, le=10_000)
    min_area_sqm: float = Field(default=500.0, ge=0, le=1_000_000)
    max_shading: float = Field(default=0.4, ge=0, le=1.0)
    min_exposure_score: float = Field(default=0.7, ge=0, le=1.0)
    orientamenti_ok: list[Literal["N", "NE", "E", "SE", "S", "SO", "O", "NO"]] = Field(
        default_factory=lambda: ["S", "SE", "SO", "E", "O"],
        description="Accepted roof orientations (Italian cardinals).",
    )
    solar_gate_pct: float = Field(
        default=0.20,
        ge=0.01,
        le=1.0,
        description=(
            "Fraction of L3-scored candidates that enter Solar L4. "
            "0.20 = top 20%. Controls API spend directly."
        ),
    )
    solar_gate_min_candidates: int = Field(
        default=20,
        ge=1,
        le=10_000,
        description=(
            "Floor so tiny scans (e.g. 10 candidates * 20% = 2) still "
            "yield a reasonable sample through Solar."
        ),
    )


class EconomicoConfig(BaseModel):
    """Module `economico` — pricing + budget caps.

    `budget_scan_eur` is the *per-scan* soft cap. Once exceeded the
    orchestrator short-circuits remaining levels — the partial result
    is still persisted so the installer gets a look at what ran. Hard
    monthly caps live on `budget_outreach_eur_month` and are enforced
    by the outreach workers.
    """

    model_config = ConfigDict(extra="forbid")

    ticket_medio_eur: int = Field(default=25_000, ge=0, le=1_000_000)
    roi_target_years: int = Field(default=6, ge=1, le=30)
    budget_scan_eur: float = Field(
        default=50.0,
        ge=0,
        le=10_000,
        description=(
            "Per-scan spend ceiling (€). Funnel aborts levels once "
            "accumulated cost crosses this. 50 = ~€50/run typical."
        ),
    )
    budget_outreach_eur_month: float = Field(
        default=2_000.0,
        ge=0,
        le=1_000_000,
    )


class OutreachChannels(BaseModel):
    """Toggles for outbound channels. Each must be independently
    enableable — a tenant running only postal letters (B2C door-less
    campaigns) shouldn't need to set up email providers."""

    model_config = ConfigDict(extra="forbid")

    email: bool = True
    postal: bool = False
    whatsapp: bool = False
    meta_ads: bool = False


class OutreachConfig(BaseModel):
    """Module `outreach` — channels + voice.

    `cta_primary` is user-visible copy; the creative agent weaves it
    into generated emails/letters. Kept short (max 80 chars) so it
    fits email subject lines too.
    """

    model_config = ConfigDict(extra="forbid")

    channels: OutreachChannels = Field(default_factory=OutreachChannels)
    tone_of_voice: str = Field(
        default="professionale-diretto",
        max_length=60,
    )
    cta_primary: str = Field(
        default="Prenota un sopralluogo gratuito",
        max_length=80,
    )


class CRMConfig(BaseModel):
    """Module `crm` — outbound webhooks + pipeline vocabulary.

    `webhook_secret` is the HMAC-SHA256 shared secret. The `/v1/modules/crm`
    endpoint auto-generates one on first save if the installer leaves it
    blank; we don't return it after save unless the installer requests a
    reveal (same TOTP pattern as GitHub tokens).
    """

    model_config = ConfigDict(extra="forbid")

    webhook_url: str | None = Field(default=None, max_length=2048)
    webhook_secret: str | None = Field(default=None, max_length=128)
    pipeline_labels: list[str] = Field(
        default_factory=lambda: [
            "nuovo",
            "contattato",
            "in-valutazione",
            "preventivo",
            "chiuso",
        ],
        max_length=20,
    )
    sla_hours_first_touch: int = Field(default=24, ge=0, le=720)


# Union discriminator — used when we need to handle an unknown-key
# config generically (e.g. the GET /v1/modules list endpoint).
_SCHEMA_BY_KEY: dict[ModuleKey, type[BaseModel]] = {
    "sorgente": SorgenteConfig,
    "tecnico": TecnicoConfig,
    "economico": EconomicoConfig,
    "outreach": OutreachConfig,
    "crm": CRMConfig,
}


def schema_for(key: ModuleKey) -> type[BaseModel]:
    """Return the Pydantic class validating a module's config JSON."""
    return _SCHEMA_BY_KEY[key]


def validate_config(key: ModuleKey, raw: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalise a raw config dict against the module schema.

    Returns the `.model_dump()` — which may differ from the input
    (defaults applied, keys dropped by `extra='forbid'`, dedup'd ATECO
    codes). Raises `ValidationError` on bad shape — the route handler
    converts that to a 422.
    """
    return schema_for(key)(**raw).model_dump(mode="json")


def hydrate_config(key: ModuleKey, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Fill missing fields in a persisted config with their schema defaults.

    Migration 0036 inserts `config = '{}'` for every new tenant, and a
    schema addition to one of the module models would leave old rows
    without the new field. Calling Pydantic in non-strict mode with
    whatever JSONB is on disk yields a fully-populated dict that the
    wire format can expose without the frontend having to hydrate
    defaults itself.

    Unlike `validate_config`, this tolerates extra keys silently (a
    deprecated field hanging around on an old row shouldn't 500 the
    read path). Writes still go through `validate_config` which uses
    `extra='forbid'`.
    """
    model = schema_for(key)
    # Use the "lax" variant by constructing from a shallow-copied dict
    # and ignoring extras. `extra='forbid'` on the model class would
    # reject legacy keys — we dump and re-construct to sidestep that on
    # reads only.
    data = dict(raw or {})
    # Build an instance by dropping unknown keys so `extra='forbid'`
    # doesn't raise. Pydantic fills missing fields from defaults.
    allowed = set(model.model_fields.keys())
    filtered = {k: v for k, v in data.items() if k in allowed}
    return model(**filtered).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Module row — the composite view returned to the API
# ---------------------------------------------------------------------------


class TenantModule(BaseModel):
    """One `tenant_modules` row projected to the HTTP/domain layer."""

    model_config = ConfigDict(extra="ignore")

    tenant_id: UUID
    module_key: ModuleKey
    config: dict[str, Any]
    active: bool
    version: int
    updated_at: str | None = None


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------


def _row_to_module(row: dict[str, Any]) -> TenantModule:
    """Project a raw `tenant_modules` row to the domain/HTTP object,
    hydrating `config` through the Pydantic schema so callers never
    see a missing field. See `hydrate_config` for why."""
    key: ModuleKey = row["module_key"]
    return TenantModule(
        tenant_id=UUID(row["tenant_id"]),
        module_key=key,
        config=hydrate_config(key, row.get("config")),
        active=bool(row.get("active", True)),
        version=int(row.get("version") or 0),
        updated_at=row.get("updated_at"),
    )


def _synth_module(tenant_id: str, key: ModuleKey) -> TenantModule:
    """Build a fresh defaulted TenantModule for a tenant that has no row yet.

    After migration 0036 installs a trigger creating the five rows on
    tenant INSERT, this path is only reachable if something bypassed the
    trigger (manual SQL test fixtures, a legacy tenant we somehow
    missed). Keeping the synth here means the API still renders 5 tiles
    uniformly instead of surfacing a hole to the UI.
    """
    return TenantModule(
        tenant_id=UUID(tenant_id),
        module_key=key,
        config=schema_for(key)().model_dump(mode="json"),
        active=True,
        version=0,
    )


async def get_module(
    tenant_id: UUID | str, key: ModuleKey
) -> TenantModule:
    """Fetch one module row, returning a defaulted instance if missing.

    Migration 0036 makes the "row missing" case unreachable for new
    tenants, but we still defend — a deleted row or an unreplicated
    read replica should not 500 the form.
    """
    sb = get_service_client()
    tid = str(tenant_id)
    res = (
        sb.table("tenant_modules")
        .select("tenant_id, module_key, config, active, version, updated_at")
        .eq("tenant_id", tid)
        .eq("module_key", key)
        .maybe_single()
        .execute()
    )
    row = getattr(res, "data", None)
    if not row:
        return _synth_module(tid, key)
    return _row_to_module(row)


async def list_modules(tenant_id: UUID | str) -> list[TenantModule]:
    """Return all 5 modules for a tenant, hydrated through Pydantic.

    Post-0036 every tenant has the five rows from insertion, but we
    still iterate `MODULE_KEYS` and synth any missing one — defence in
    depth against the trigger being dropped or a partial delete.
    """
    sb = get_service_client()
    tid = str(tenant_id)
    res = (
        sb.table("tenant_modules")
        .select("tenant_id, module_key, config, active, version, updated_at")
        .eq("tenant_id", tid)
        .execute()
    )
    rows = list(getattr(res, "data", None) or [])
    by_key = {r["module_key"]: r for r in rows}

    out: list[TenantModule] = []
    for key in MODULE_KEYS:
        r = by_key.get(key)
        out.append(_row_to_module(r) if r else _synth_module(tid, key))
    return out


async def upsert_module(
    tenant_id: UUID | str,
    key: ModuleKey,
    *,
    config: dict[str, Any] | None = None,
    active: bool | None = None,
) -> TenantModule:
    """Create or update one module row.

    Args:
        tenant_id: tenant UUID.
        key: module key (one of MODULE_KEYS).
        config: raw config dict — validated before write. If None, only
            `active` is toggled.
        active: if set, overrides the active flag. If both `config` and
            `active` are None this is a no-op (returns current state).

    Returns the freshly persisted row. `version` is bumped server-side
    by the trigger whenever `config` changes.
    """
    if config is None and active is None:
        return await get_module(tenant_id, key)

    sb = get_service_client()
    tid = str(tenant_id)

    payload: dict[str, Any] = {"tenant_id": tid, "module_key": key}
    if config is not None:
        payload["config"] = validate_config(key, config)
    if active is not None:
        payload["active"] = bool(active)

    # Upsert by (tenant_id, module_key) — the unique constraint on the
    # table. Postgres handles insert-or-update atomically.
    sb.table("tenant_modules").upsert(
        payload, on_conflict="tenant_id,module_key"
    ).execute()

    log.info(
        "tenant_module.upsert",
        tenant_id=tid,
        module_key=key,
        config_changed=config is not None,
        active_changed=active is not None,
    )
    return await get_module(tid, key)


async def all_completed(tenant_id: UUID | str) -> bool:
    """Has the installer saved every module at least once?

    Since migration 0036 the five rows exist from tenant creation with
    `version = 0`. The `tenant_modules_touch` trigger bumps `version`
    only when `config` actually changes, so `version >= 1` on all five
    keys is a clean marker for "installer walked through the wizard".

    Merely toggling `active` doesn't count — that's a settings tweak,
    not a wizard step.
    """
    sb = get_service_client()
    tid = str(tenant_id)
    res = (
        sb.table("tenant_modules")
        .select("module_key, version")
        .eq("tenant_id", tid)
        .execute()
    )
    rows = list(getattr(res, "data", None) or [])
    touched = {r["module_key"] for r in rows if int(r.get("version") or 0) >= 1}
    return set(MODULE_KEYS).issubset(touched)
