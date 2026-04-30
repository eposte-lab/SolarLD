"""Unit tests for ``practice_service.py``.

The service is mostly an orchestrator over Supabase + the practice
mapper + the WeasyPrint renderer. We test only the pure helpers
(``_tenant_abbr``, ``_guess_distributore``) and the
``next_practice_number`` formatting glue here. The async creation
path is exercised end-to-end in ``test_practice_pdf_renderer.py``
(template render) and via the dashboard manual-test plan (M5/M6).

The Supabase-mocked tests for ``create_practice`` are skipped because
the service touches 5 tables in sequence — duplicating that with
MagicMock chains would be more brittle than it's worth. Instead we
rely on the service-level smoke tests run from ``conftest`` integration
fixtures (when SUPABASE_URL is set), and the pure-function tests below.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _tenant_abbr — pure formatting
# ---------------------------------------------------------------------------


def test_tenant_abbr_takes_first_4_alphanumeric_uppercase() -> None:
    from src.services.practice_service import _tenant_abbr

    assert _tenant_abbr("Sole Energy SRL") == "SOLE"
    assert _tenant_abbr("Solar-LED Italia") == "SOLA"
    # Trailing spaces, dashes, apostrophes are all stripped.
    assert _tenant_abbr(" SunEnergy 2.0 ") == "SUNE"


def test_tenant_abbr_pads_short_names_to_4_chars() -> None:
    from src.services.practice_service import _tenant_abbr

    # "AB" → "ABPR" (padded with the PRA suffix to keep the format
    # deterministic — UNIQUE(tenant_id, practice_number) needs a stable
    # leading abbr per tenant).
    assert _tenant_abbr("AB") == "ABPR"
    assert _tenant_abbr("X") == "XPRA"


def test_tenant_abbr_falls_back_to_pra_for_empty_or_garbage() -> None:
    from src.services.practice_service import _tenant_abbr

    assert _tenant_abbr("") == "PRA"
    assert _tenant_abbr("   ") == "PRA"
    assert _tenant_abbr("---") == "PRA"
    assert _tenant_abbr("@@@") == "PRA"


# ---------------------------------------------------------------------------
# _guess_distributore — Sprint 1 CAP heuristic
# ---------------------------------------------------------------------------


def test_guess_distributore_routes_roma_to_areti() -> None:
    from src.services.practice_service import _guess_distributore

    assert _guess_distributore("00184") == "areti"  # central Rome
    assert _guess_distributore("00100") == "areti"
    # Same prefix but 4-digit CAPs would never appear (Italian CAPs
    # are 5 chars) — the function gates on len == 5.
    assert _guess_distributore("0019") == "e_distribuzione"


def test_guess_distributore_routes_milano_to_unareti() -> None:
    from src.services.practice_service import _guess_distributore

    assert _guess_distributore("20121") == "unareti"
    assert _guess_distributore("20162") == "unareti"


def test_guess_distributore_falls_back_to_e_distribuzione() -> None:
    from src.services.practice_service import _guess_distributore

    # National incumbent ~85% of LV POD — anything outside the two
    # exceptions defaults to e-distribuzione.
    assert _guess_distributore("16100") == "e_distribuzione"  # Genova
    assert _guess_distributore("80100") == "e_distribuzione"  # Napoli
    assert _guess_distributore("") == "e_distribuzione"
    assert _guess_distributore("garbage") == "e_distribuzione"


# ---------------------------------------------------------------------------
# next_practice_number — assembly
# ---------------------------------------------------------------------------


@patch("src.services.practice_service.get_service_client")
def test_next_practice_number_formats_correctly(mock_client: MagicMock) -> None:
    """Hits the tenants table once + the RPC once and assembles the
    {ABBR}/{YEAR}/{NNNN} string. We don't care which year — pin the
    assertion to the year segment by component."""
    from src.services.practice_service import next_practice_number

    sb = MagicMock()
    # tenants.select.eq.limit.execute → business_name
    tenants_chain = MagicMock()
    tenants_chain.select.return_value = tenants_chain
    tenants_chain.eq.return_value = tenants_chain
    tenants_chain.limit.return_value = tenants_chain
    tenants_chain.execute.return_value = MagicMock(
        data=[{"business_name": "Sole Energy SRL"}]
    )
    sb.table.return_value = tenants_chain

    # next_practice_seq RPC returns 42
    rpc_chain = MagicMock()
    rpc_chain.execute.return_value = MagicMock(data=42)
    sb.rpc.return_value = rpc_chain

    mock_client.return_value = sb

    number, seq = next_practice_number("00000000-0000-0000-0000-000000000001")

    assert seq == 42
    # Format: {ABBR}/{YEAR}/{NNNN} — pad to 4 digits.
    parts = number.split("/")
    assert len(parts) == 3
    assert parts[0] == "SOLE"
    assert parts[1].isdigit() and len(parts[1]) == 4  # year
    assert parts[2] == "0042"


@patch("src.services.practice_service.get_service_client")
def test_next_practice_number_raises_on_zero_seq(mock_client: MagicMock) -> None:
    """A 0/None RPC return means the counter table is broken — refuse
    to mint a number rather than risk a duplicate."""
    import pytest

    from src.services.practice_service import next_practice_number

    sb = MagicMock()
    tenants_chain = MagicMock()
    tenants_chain.select.return_value = tenants_chain
    tenants_chain.eq.return_value = tenants_chain
    tenants_chain.limit.return_value = tenants_chain
    tenants_chain.execute.return_value = MagicMock(
        data=[{"business_name": "Acme"}]
    )
    sb.table.return_value = tenants_chain

    rpc_chain = MagicMock()
    rpc_chain.execute.return_value = MagicMock(data=0)
    sb.rpc.return_value = rpc_chain

    mock_client.return_value = sb

    with pytest.raises(RuntimeError, match="non-positive"):
        next_practice_number("00000000-0000-0000-0000-000000000001")
