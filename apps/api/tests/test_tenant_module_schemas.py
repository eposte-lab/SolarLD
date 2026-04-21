"""Pure schema tests for the modular wizard.

We cover the bits of `tenant_module_service` that don't require a
Supabase client: the Pydantic schemas, their defaults, dedup/strip
behaviour on ATECO codes, bounds checking on numeric fields, and
`extra='forbid'` rejection of unknown keys.

DAO tests (`get_module`, `upsert_module`, `list_modules`) need a
live Supabase fixture and land in the integration suite.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.services.tenant_module_service import (
    MODULE_KEYS,
    CRMConfig,
    EconomicoConfig,
    OutreachConfig,
    SorgenteConfig,
    TecnicoConfig,
    hydrate_config,
    schema_for,
    validate_config,
)


# ---------------------------------------------------------------------------
# Schema instantiation + defaults
# ---------------------------------------------------------------------------


def test_every_module_key_has_a_schema():
    for key in MODULE_KEYS:
        schema = schema_for(key)
        # Must instantiate with zero args (defaults only)
        instance = schema()
        # Must serialise to JSON-safe dict
        dumped = instance.model_dump(mode="json")
        assert isinstance(dumped, dict)


def test_sorgente_defaults_match_plan():
    s = SorgenteConfig()
    assert s.min_employees == 20
    assert s.max_employees == 250
    assert s.min_revenue_eur == 2_000_000
    assert s.max_revenue_eur == 50_000_000
    assert s.reddito_min_eur == 35_000
    assert s.case_unifamiliari_pct_min == 40
    assert s.ateco_codes == []


def test_tecnico_defaults_match_plan():
    t = TecnicoConfig()
    assert t.solar_gate_pct == 0.20
    assert t.solar_gate_min_candidates == 20
    assert t.min_kwp == 50.0
    assert "S" in t.orientamenti_ok


def test_economico_defaults_match_plan():
    e = EconomicoConfig()
    assert e.budget_scan_eur == 50.0
    assert e.ticket_medio_eur == 25_000


def test_outreach_defaults_email_only():
    o = OutreachConfig()
    assert o.channels.email is True
    assert o.channels.postal is False
    assert o.channels.meta_ads is False


def test_crm_defaults_have_pipeline_labels():
    c = CRMConfig()
    assert "nuovo" in c.pipeline_labels
    assert "chiuso" in c.pipeline_labels
    assert c.webhook_url is None


# ---------------------------------------------------------------------------
# Validation behaviour
# ---------------------------------------------------------------------------


def test_sorgente_dedupes_and_strips_ateco():
    s = SorgenteConfig(ateco_codes=[" 10.51 ", "10.51", "20.11", " "])
    assert s.ateco_codes == ["10.51", "20.11"]


def test_tecnico_solar_gate_bounds():
    with pytest.raises(ValidationError):
        TecnicoConfig(solar_gate_pct=1.5)
    with pytest.raises(ValidationError):
        TecnicoConfig(solar_gate_pct=0.0)


def test_tecnico_shading_range():
    with pytest.raises(ValidationError):
        TecnicoConfig(max_shading=1.1)


def test_extra_fields_forbidden_on_every_module():
    # Each schema explicitly forbids unknown keys so a buggy frontend
    # can't silently persist garbage.
    for key in MODULE_KEYS:
        with pytest.raises(ValidationError):
            schema_for(key)(**{"totally_not_a_field": 1})


def test_validate_config_applies_defaults():
    out = validate_config("tecnico", {})
    assert out["solar_gate_pct"] == 0.20
    assert out["min_kwp"] == 50.0


def test_validate_config_rejects_unknown_module_key():
    with pytest.raises(KeyError):
        validate_config("sorgentezzz", {})  # type: ignore[arg-type]


def test_outreach_cta_length_cap():
    with pytest.raises(ValidationError):
        OutreachConfig(cta_primary="x" * 200)


def test_crm_sla_bounds():
    with pytest.raises(ValidationError):
        CRMConfig(sla_hours_first_touch=-1)
    with pytest.raises(ValidationError):
        CRMConfig(sla_hours_first_touch=10_000)


def test_orientamenti_ok_rejects_unknown_cardinal():
    with pytest.raises(ValidationError):
        TecnicoConfig(orientamenti_ok=["X"])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# hydrate_config — read-path invariant enforcer (see migration 0036)
# ---------------------------------------------------------------------------
# These exist because the frontend previously crashed on `undefined.join()`
# when a DB row held `config = {}` (fresh tenant post-0032) and missing
# array fields leaked straight through. `hydrate_config` closes that gap
# by re-running Pydantic defaults over whatever JSONB is on disk.


def test_hydrate_empty_config_returns_full_shape_per_module():
    for key in MODULE_KEYS:
        out = hydrate_config(key, {})
        # Every field in the schema must be present after hydration —
        # this is the invariant the frontend relies on.
        for field in schema_for(key).model_fields:
            assert field in out, f"{key}: field '{field}' missing after hydration"


def test_hydrate_none_config_returns_full_shape():
    # Supabase returns `config: None` when a column is SQL NULL; the
    # JSONB DEFAULT on tenant_modules is '{}' so this shouldn't happen
    # in practice, but we tolerate it rather than 500.
    out = hydrate_config("sorgente", None)
    assert out["ateco_codes"] == []
    assert out["min_employees"] == 20


def test_hydrate_preserves_user_provided_values():
    out = hydrate_config("tecnico", {"min_kwp": 75.0, "solar_gate_pct": 0.3})
    assert out["min_kwp"] == 75.0
    assert out["solar_gate_pct"] == 0.3
    # Untouched fields fall back to defaults.
    assert out["min_area_sqm"] == 500.0


def test_hydrate_drops_unknown_legacy_keys_silently():
    # A field removed from the schema in a later release might linger
    # in old rows. Reads tolerate this (unlike writes, which reject
    # with 422 via `extra='forbid'`).
    out = hydrate_config(
        "tecnico",
        {"min_kwp": 60.0, "deprecated_field": "should_not_raise"},
    )
    assert out["min_kwp"] == 60.0
    assert "deprecated_field" not in out


def test_hydrate_fills_nested_outreach_channels():
    # The crash class that started this refactor: `value.channels.email`
    # when channels is missing. After hydration channels must be a dict
    # with all four boolean toggles present.
    out = hydrate_config("outreach", {})
    assert isinstance(out["channels"], dict)
    for flag in ("email", "postal", "whatsapp", "meta_ads"):
        assert flag in out["channels"]


def test_hydrate_arrays_never_return_undefined_equivalent():
    # Guarantee that every list-typed field comes back as a list (not
    # None, not missing) — this is the invariant that lets the frontend
    # drop its `Array.isArray(value) ? value : []` defensive guards.
    out_s = hydrate_config("sorgente", {})
    assert out_s["ateco_codes"] == []
    assert out_s["province"] == []
    out_t = hydrate_config("tecnico", {})
    assert isinstance(out_t["orientamenti_ok"], list)
    out_c = hydrate_config("crm", {})
    assert isinstance(out_c["pipeline_labels"], list)
