"""Pure-function tests for the Outreach Agent helpers.

The agent itself exercises Supabase + Resend — out of scope here. These
tests only cover the small pure helpers that drive recipient selection,
greeting composition, template id formation, and URL building.
"""

from __future__ import annotations

from src.agents.outreach import (
    _build_from_address,
    _greeting_for,
    _optout_url,
    _public_lead_url,
    _resolve_recipient,
    _template_id_for,
)


# ---------------------------------------------------------------------------
# _resolve_recipient
# ---------------------------------------------------------------------------


def test_resolve_recipient_b2b_verified() -> None:
    email = _resolve_recipient(
        {
            "type": "b2b",
            "decision_maker_email": "Ceo@Example.com",
            "decision_maker_email_verified": True,
        }
    )
    # Lowercased + trimmed.
    assert email == "ceo@example.com"


def test_resolve_recipient_b2b_unverified_returns_none() -> None:
    email = _resolve_recipient(
        {
            "type": "b2b",
            "decision_maker_email": "ceo@example.com",
            "decision_maker_email_verified": False,
        }
    )
    assert email is None


def test_resolve_recipient_b2b_missing_email() -> None:
    email = _resolve_recipient(
        {
            "type": "b2b",
            "decision_maker_email": None,
            "decision_maker_email_verified": True,
        }
    )
    assert email is None


def test_resolve_recipient_b2c_always_none_for_now() -> None:
    email = _resolve_recipient(
        {
            "type": "b2c",
            "decision_maker_email": "x@y.com",
            "decision_maker_email_verified": True,
        }
    )
    # B2C goes postal — we don't send emails.
    assert email is None


def test_resolve_recipient_unknown_type_none() -> None:
    assert (
        _resolve_recipient({"type": "unknown", "decision_maker_email": "x@y.com"})
        is None
    )


# ---------------------------------------------------------------------------
# _greeting_for
# ---------------------------------------------------------------------------


def test_greeting_b2b_prefers_decision_maker_name() -> None:
    g = _greeting_for(
        {
            "decision_maker_name": "Luca Bianchi",
            "business_name": "Acme SpA",
        },
        "b2b",
    )
    assert g == "Luca Bianchi"


def test_greeting_b2b_falls_back_to_business_name() -> None:
    g = _greeting_for(
        {"decision_maker_name": "", "business_name": "Acme SpA"},
        "b2b",
    )
    assert g == "Acme SpA"


def test_greeting_b2b_final_fallback() -> None:
    g = _greeting_for({}, "b2b")
    assert g == "Gentili responsabili"


def test_greeting_b2c_joins_first_and_last_names() -> None:
    g = _greeting_for(
        {"owner_first_name": "Maria", "owner_last_name": "Rossi"}, "b2c"
    )
    assert g == "Maria Rossi"


def test_greeting_b2c_missing_names_fallback() -> None:
    g = _greeting_for({}, "b2c")
    assert g == "Gentile proprietario"


def test_greeting_unknown_type_fallback() -> None:
    g = _greeting_for({}, "mystery")
    assert g == "Buongiorno"


# ---------------------------------------------------------------------------
# _template_id_for
# ---------------------------------------------------------------------------


def test_template_id_b2b() -> None:
    assert _template_id_for("b2b") == "outreach_b2b_v1"


def test_template_id_b2c() -> None:
    assert _template_id_for("b2c") == "outreach_b2c_v1"


def test_template_id_unknown() -> None:
    assert _template_id_for("mystery") == "outreach_generic_v1"


def test_template_id_case_insensitive() -> None:
    assert _template_id_for("B2B") == "outreach_b2b_v1"


def test_template_id_none() -> None:
    assert _template_id_for("") == "outreach_generic_v1"


# ---------------------------------------------------------------------------
# _build_from_address
# ---------------------------------------------------------------------------


def test_build_from_uses_tenant_domain() -> None:
    addr = _build_from_address(
        {
            "email_from_name": "Solare Rapido",
            "email_from_domain": "solarerapido.it",
            "business_name": "Solare Rapido SRL",
        }
    )
    assert addr == "Solare Rapido <outreach@solarerapido.it>"


def test_build_from_falls_back_when_no_domain() -> None:
    addr = _build_from_address(
        {"email_from_name": "", "email_from_domain": "", "business_name": "Acme"}
    )
    assert addr.endswith("<outreach@solarlead.it>")
    assert "Acme" in addr


def test_build_from_uses_default_display_when_no_names() -> None:
    addr = _build_from_address({})
    assert addr == "SolarLead <outreach@solarlead.it>"


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def test_public_lead_url_contains_slug() -> None:
    url = _public_lead_url("abc123")
    assert url.endswith("/l/abc123")


def test_public_lead_url_missing_slug_returns_base() -> None:
    url = _public_lead_url(None)
    # No trailing /l when slug is missing.
    assert "/l/" not in url


def test_optout_url_contains_slug() -> None:
    url = _optout_url("abc123")
    assert url.endswith("/optout/abc123")


def test_optout_url_missing_slug_returns_base_optout() -> None:
    url = _optout_url("")
    assert url.endswith("/optout")
