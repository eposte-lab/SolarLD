"""Unit tests for ``practice_data_mapper.py``.

Strategy: mock ``get_service_client`` so the mapper instantiates without
hitting Postgres, then exercise (1) the static norme block, (2) the
template-requirement validation, and (3) the dotted-path resolver
helper. Full integration with real PostgREST is covered by the manual
test plan (M5/M6) in the sprint plan.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Static module-level constants — exercised without instantiating the class.
# ---------------------------------------------------------------------------


def test_norme_reference_block_includes_required_normatives() -> None:
    """The DM 37/08 template lists these by key; if any are renamed
    the rendered "Norme" section silently goes missing. Pin the keys."""
    from src.services.practice_data_mapper import _NORME_REFERENCE

    expected_keys = {
        "dm_37_08",
        "cei_0_21",
        "cei_0_16",
        "cei_82_25",
        "uni_8290",
        "dlgs_28_2011",
    }
    assert expected_keys.issubset(_NORME_REFERENCE.keys())
    # Each entry should be a non-trivial citation, not a key alias.
    for k in expected_keys:
        assert len(_NORME_REFERENCE[k]) > 20


def test_template_requirements_cover_dm_37_08_and_comunicazione() -> None:
    """If we ever ship a template without listed requirements,
    `validate_for_template` returns [] and the renderer eats the
    error silently. Pin the two Sprint 1 templates."""
    from src.services.practice_data_mapper import _TEMPLATE_REQUIREMENTS

    assert "dm_37_08" in _TEMPLATE_REQUIREMENTS
    assert "comunicazione_comune" in _TEMPLATE_REQUIREMENTS

    dm_paths = [path for path, _ in _TEMPLATE_REQUIREMENTS["dm_37_08"]]
    # The dichiarazione cannot be signed without these — see migration 0082.
    assert "tenant.codice_fiscale" in dm_paths
    assert "tenant.numero_cciaa" in dm_paths
    assert "tenant.responsabile_tecnico_nome" in dm_paths
    assert "tenant.responsabile_tecnico_iscrizione_albo" in dm_paths

    cc_paths = [
        path for path, _ in _TEMPLATE_REQUIREMENTS["comunicazione_comune"]
    ]
    # The communication is addressed "Al Sig. Sindaco del Comune di {comune}".
    assert "ubicazione.comune" in cc_paths


# ---------------------------------------------------------------------------
# _resolve_path — dotted-path walker used by validate_for_template
# ---------------------------------------------------------------------------


def test_resolve_path_returns_value_when_present() -> None:
    from src.services.practice_data_mapper import _resolve_path

    ctx = {"tenant": {"codice_fiscale": "RSSMRA80A01H501Z"}}
    assert _resolve_path(ctx, "tenant.codice_fiscale") == "RSSMRA80A01H501Z"


def test_resolve_path_returns_none_when_missing_or_empty() -> None:
    from src.services.practice_data_mapper import _resolve_path

    ctx: dict[str, Any] = {
        "tenant": {"codice_fiscale": "", "responsabile_tecnico_nome": None},
        "impianto": {"potenza_kw": 0},
    }
    # Empty string treated as missing — required-field semantics.
    assert _resolve_path(ctx, "tenant.codice_fiscale") is None
    # None too.
    assert _resolve_path(ctx, "tenant.responsabile_tecnico_nome") is None
    # Zero kW also treated as missing — an obviously broken impianto.
    assert _resolve_path(ctx, "impianto.potenza_kw") is None
    # Path that doesn't exist at all.
    assert _resolve_path(ctx, "ubicazione.comune") is None


def test_resolve_path_walks_nested_dicts() -> None:
    from src.services.practice_data_mapper import _resolve_path

    ctx = {
        "installatore": {
            "responsabile_tecnico": {"nome_completo": "Mario Rossi"}
        }
    }
    assert (
        _resolve_path(
            ctx, "installatore.responsabile_tecnico.nome_completo"
        )
        == "Mario Rossi"
    )


# ---------------------------------------------------------------------------
# validate_for_template — exercises the mapper end-to-end with a stubbed
# Supabase client. We only care about the validation surface; the field
# expansion is tested implicitly by the renderer smoke test.
# ---------------------------------------------------------------------------


def _stub_chain(data: list[dict[str, Any]] | None = None) -> MagicMock:
    """Mirror the supabase-py builder pattern used in the mapper:
    .table(..).select(..).eq(..).eq(..).limit(..).execute() → Result(data)."""
    chain = MagicMock()
    chain.execute.return_value = MagicMock(data=data or [])
    for m in ("select", "insert", "update", "eq", "limit", "order", "in_"):
        getattr(chain, m).return_value = chain
    return chain


def _build_sb(
    *,
    practice_row: dict[str, Any],
    tenant_row: dict[str, Any],
) -> MagicMock:
    sb = MagicMock()

    def table(name: str) -> MagicMock:
        if name == "practices":
            return _stub_chain(data=[practice_row])
        if name == "tenants":
            return _stub_chain(data=[tenant_row])
        return _stub_chain()

    sb.table.side_effect = table
    return sb


@patch("src.services.practice_data_mapper.get_service_client")
def test_validate_dm_37_08_flags_missing_responsabile_tecnico(
    mock_client: MagicMock,
) -> None:
    """A tenant without the responsabile_tecnico_* columns set should
    block DM 37/08 — the dichiarazione is unsignable. The list comes
    back with friendly Italian labels (not column names)."""
    from src.services.practice_data_mapper import PracticeDataMapper

    practice_row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "tenant_id": "00000000-0000-0000-0000-0000000000aa",
        "lead_id": "00000000-0000-0000-0000-0000000000bb",
        "quote_id": None,
        "practice_number": "ACME/2026/0001",
        "practice_seq": 1,
        "status": "in_preparation",
        "impianto_potenza_kw": 50.0,
        "impianto_pannelli_count": 86,
        "impianto_pod": None,
        "impianto_distributore": "e_distribuzione",
        "impianto_data_inizio_lavori": "2026-04-01",
        "impianto_data_fine_lavori": "2026-04-30",
        "catastale_foglio": "123",
        "catastale_particella": "45",
        "catastale_subalterno": None,
        "componenti_data": {},
        "data_snapshot": {},
        "created_at": "2026-04-30T12:00:00Z",
        "updated_at": "2026-04-30T12:00:00Z",
        # Embedded join → roof carries the address fields.
        "leads": {
            "id": "00000000-0000-0000-0000-0000000000bb",
            "subjects": {"business_name": "Cliente SRL"},
            "roofs": {
                "address": "Via Roma 1",
                "comune": "Genova",
                "provincia": "GE",
                "cap": "16100",
            },
        },
        "lead_quotes": None,
    }
    tenant_row = {
        "id": "00000000-0000-0000-0000-0000000000aa",
        "business_name": "Acme Energy SRL",
        "legal_name": "Acme Energy S.r.l.",
        "vat_number": "01234567890",
        # Missing: codice_fiscale, numero_cciaa, responsabile_tecnico_*
        "codice_fiscale": None,
        "numero_cciaa": None,
        "responsabile_tecnico_nome": None,
        "responsabile_tecnico_cognome": None,
        "responsabile_tecnico_codice_fiscale": None,
        "responsabile_tecnico_qualifica": None,
        "responsabile_tecnico_iscrizione_albo": None,
        "contact_email": "info@acme.it",
        "contact_phone": None,
        "brand_logo_url": None,
        "brand_primary_color": None,
        "settings": {},
        "legal_address": "Via Milano 5",
    }

    mock_client.return_value = _build_sb(
        practice_row=practice_row, tenant_row=tenant_row
    )
    mapper = PracticeDataMapper(
        practice_id=practice_row["id"], tenant_id=tenant_row["id"]
    )
    missing = mapper.validate_for_template("dm_37_08")

    # Should flag at least the 4 required tenant-level fields.
    joined = " | ".join(missing)
    assert "Codice fiscale" in joined
    assert "CCIAA" in joined
    assert "responsabile tecnico" in joined.lower()


@patch("src.services.practice_data_mapper.get_service_client")
def test_validate_comunicazione_comune_passes_with_minimal_data(
    mock_client: MagicMock,
) -> None:
    """The communication only needs the impianto address + dates +
    potenza. No tenant legal fields required — it's a notification,
    not a sworn declaration."""
    from src.services.practice_data_mapper import PracticeDataMapper

    practice_row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "tenant_id": "00000000-0000-0000-0000-0000000000aa",
        "lead_id": "00000000-0000-0000-0000-0000000000bb",
        "quote_id": None,
        "practice_number": "ACME/2026/0001",
        "practice_seq": 1,
        "status": "in_preparation",
        "impianto_potenza_kw": 50.0,
        "impianto_pannelli_count": 86,
        "impianto_pod": None,
        "impianto_distributore": "e_distribuzione",
        "impianto_data_inizio_lavori": "2026-04-01",
        "impianto_data_fine_lavori": "2026-04-30",
        "catastale_foglio": None,
        "catastale_particella": None,
        "catastale_subalterno": None,
        "componenti_data": {},
        "data_snapshot": {},
        "created_at": "2026-04-30T12:00:00Z",
        "updated_at": "2026-04-30T12:00:00Z",
        "leads": {
            "id": "00000000-0000-0000-0000-0000000000bb",
            "subjects": {"business_name": "Cliente SRL"},
            "roofs": {
                "address": "Via Roma 1",
                "comune": "Genova",
                "provincia": "GE",
                "cap": "16100",
            },
        },
        "lead_quotes": None,
    }
    tenant_row = {
        "id": "00000000-0000-0000-0000-0000000000aa",
        "business_name": "Acme Energy SRL",
        "legal_name": "Acme",
        "vat_number": "01234567890",
        "codice_fiscale": None,  # Not required for comunicazione_comune.
        "numero_cciaa": None,
        "responsabile_tecnico_nome": None,
        "responsabile_tecnico_cognome": None,
        "responsabile_tecnico_codice_fiscale": None,
        "responsabile_tecnico_qualifica": None,
        "responsabile_tecnico_iscrizione_albo": None,
        "contact_email": "info@acme.it",
        "contact_phone": None,
        "brand_logo_url": None,
        "brand_primary_color": None,
        "settings": {},
        "legal_address": "Via Milano 5",
    }

    mock_client.return_value = _build_sb(
        practice_row=practice_row, tenant_row=tenant_row
    )
    mapper = PracticeDataMapper(
        practice_id=practice_row["id"], tenant_id=tenant_row["id"]
    )
    missing = mapper.validate_for_template("comunicazione_comune")
    assert missing == []


@patch("src.services.practice_data_mapper.get_service_client")
def test_validate_unknown_template_does_not_crash(
    mock_client: MagicMock,
) -> None:
    """An unknown template_code (e.g. typo, or Sprint 2 in-flight) returns
    [] — the renderer raises its own error downstream, which the route
    surfaces as a clean 400. No silent template_code-typo trap."""
    from src.services.practice_data_mapper import PracticeDataMapper

    practice_row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "tenant_id": "00000000-0000-0000-0000-0000000000aa",
        "lead_id": "00000000-0000-0000-0000-0000000000bb",
        "quote_id": None,
        "practice_number": "ACME/2026/0001",
        "practice_seq": 1,
        "status": "in_preparation",
        "impianto_potenza_kw": 1.0,
        "impianto_pannelli_count": None,
        "impianto_pod": None,
        "impianto_distributore": "e_distribuzione",
        "impianto_data_inizio_lavori": None,
        "impianto_data_fine_lavori": None,
        "catastale_foglio": None,
        "catastale_particella": None,
        "catastale_subalterno": None,
        "componenti_data": {},
        "data_snapshot": {},
        "created_at": "2026-04-30T12:00:00Z",
        "updated_at": "2026-04-30T12:00:00Z",
        "leads": {"subjects": {}, "roofs": {}},
        "lead_quotes": None,
    }
    tenant_row = {
        "id": "00000000-0000-0000-0000-0000000000aa",
        "business_name": "Test",
        "legal_name": None,
        "vat_number": None,
        "codice_fiscale": None,
        "numero_cciaa": None,
        "responsabile_tecnico_nome": None,
        "responsabile_tecnico_cognome": None,
        "responsabile_tecnico_codice_fiscale": None,
        "responsabile_tecnico_qualifica": None,
        "responsabile_tecnico_iscrizione_albo": None,
        "contact_email": None,
        "contact_phone": None,
        "brand_logo_url": None,
        "brand_primary_color": None,
        "settings": {},
        "legal_address": None,
    }

    mock_client.return_value = _build_sb(
        practice_row=practice_row, tenant_row=tenant_row
    )
    mapper = PracticeDataMapper(
        practice_id=practice_row["id"], tenant_id=tenant_row["id"]
    )
    assert mapper.validate_for_template("nonexistent_doc") == []
