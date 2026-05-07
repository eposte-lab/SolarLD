"""Unit tests for services.lead_quality_validator.

Covers:
  - Disposable email → hard_reject
  - Free email provider → soft -20 penalty
  - Role account exact-match → soft -10 penalty
  - Combo free + role → -30 total
  - Valid Italian VAT → no flag
  - Invalid VAT checksum (11 digits but wrong) → hard_reject
  - Non-Italian phone prefix → -5 penalty
  - Clean lead → zero delta, no flags
  - Short-circuit on disposable (no further checks)
"""

import pytest

from src.services.lead_quality_validator import (
    _is_valid_italian_vat,
    validate,
)

# ---------------------------------------------------------------------------
# _is_valid_italian_vat
# ---------------------------------------------------------------------------


class TestItalianVat:
    # Valid Italian VATs — checksum manually verified.
    # 01234567897: computed from 0123456789 + check digit 7 (sum=50, 50%10=0)
    # 98765432103: computed from 9876543210 + check digit 3 (sum=50, 50%10=0)
    @pytest.mark.parametrize(
        "vat",
        [
            "00743110157",  # Pirelli — widely cited
            "00514490010",  # Fiat — widely cited
            "02113530345",  # Barilla — widely cited
            "01234567897",  # computed-valid synthetic
            "98765432103",  # computed-valid synthetic
        ],
    )
    def test_valid_vats(self, vat: str) -> None:
        assert _is_valid_italian_vat(vat) is True

    # 5 known-invalid VATs (bad checksum digit).
    # Note: 00000000000 actually PASSES Luhn-IT (all-zeros sum=0, mod10=0)
    # so it is intentionally NOT in this list.
    @pytest.mark.parametrize(
        "vat",
        [
            "11111111111",  # sum=16, 16%10=6 → invalid
            "12345678901",
            "99999999999",
            "00743110150",  # Pirelli with last digit changed 7→0
            "01234567890",  # synthetic with last digit 0 instead of 7
        ],
    )
    def test_invalid_vats(self, vat: str) -> None:
        assert _is_valid_italian_vat(vat) is False

    def test_wrong_length(self) -> None:
        assert _is_valid_italian_vat("1234567") is False

    def test_non_digit(self) -> None:
        assert _is_valid_italian_vat("0074311015X") is False


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


class TestValidate:
    def test_clean_lead_no_flags(self) -> None:
        verdict = validate(
            email="mario.rossi@azienda.it",
            vat_number="00743110157",
            phone="+393331234567",
        )
        assert verdict.score_delta == 0
        assert verdict.flags == []
        assert verdict.hard_reject is False

    # ── Disposable email ────────────────────────────────────────────────

    def test_disposable_email_hard_reject(self) -> None:
        verdict = validate(
            email="test@mailinator.com",
            vat_number=None,
        )
        assert verdict.hard_reject is True
        assert "disposable_email" in verdict.flags
        assert verdict.score_delta == -100

    def test_disposable_short_circuits(self) -> None:
        """When disposable email fires, bad VAT should NOT add a second flag."""
        verdict = validate(
            email="foo@yopmail.com",
            vat_number="12345678901",  # also invalid checksum
        )
        assert verdict.hard_reject is True
        assert verdict.flags == ["disposable_email"]  # only one flag

    # ── Free email provider ─────────────────────────────────────────────

    def test_free_email_soft_penalty(self) -> None:
        verdict = validate(email="mario@gmail.com", vat_number=None)
        assert verdict.hard_reject is False
        assert "free_email_provider_b2b" in verdict.flags
        assert verdict.score_delta == -20

    def test_italian_free_email(self) -> None:
        verdict = validate(email="mario@libero.it", vat_number=None)
        assert "free_email_provider_b2b" in verdict.flags
        assert verdict.score_delta == -20

    # ── Role account ────────────────────────────────────────────────────

    def test_role_account_exact_match(self) -> None:
        verdict = validate(email="info@azienda.it", vat_number=None)
        assert "role_account_email" in verdict.flags
        assert verdict.score_delta == -10

    def test_role_account_not_substring(self) -> None:
        """'mario.informatico@…' should NOT fire role_account — not an exact match."""
        verdict = validate(email="mario.informatico@azienda.it", vat_number=None)
        assert "role_account_email" not in verdict.flags

    def test_role_account_admin(self) -> None:
        verdict = validate(email="admin@company.it", vat_number=None)
        assert "role_account_email" in verdict.flags

    # ── Combo ───────────────────────────────────────────────────────────

    def test_free_plus_role_cumulative_penalty(self) -> None:
        verdict = validate(email="info@gmail.com", vat_number=None)
        assert "free_email_provider_b2b" in verdict.flags
        assert "role_account_email" in verdict.flags
        assert verdict.score_delta == -30
        assert verdict.hard_reject is False

    # ── VAT checksum ────────────────────────────────────────────────────

    def test_valid_vat_no_flag(self) -> None:
        verdict = validate(email=None, vat_number="IT00743110157")
        assert "invalid_vat_checksum" not in verdict.flags

    def test_invalid_vat_hard_reject(self) -> None:
        verdict = validate(email=None, vat_number="12345678901")
        assert verdict.hard_reject is True
        assert "invalid_vat_checksum" in verdict.flags

    def test_vat_with_it_prefix_cleaned(self) -> None:
        """VAT passed as 'IT + digits' must still validate correctly."""
        verdict = validate(email=None, vat_number="IT00743110157")
        assert "invalid_vat_checksum" not in verdict.flags

    def test_non_11_digit_vat_ignored(self) -> None:
        """A VAT that is < 11 digits after cleaning is not flagged (ambiguous data)."""
        verdict = validate(email=None, vat_number="12345")
        assert "invalid_vat_checksum" not in verdict.flags

    # ── Phone mismatch ──────────────────────────────────────────────────

    def test_non_italian_phone_penalty(self) -> None:
        verdict = validate(email=None, vat_number=None, phone="+441234567890")
        assert "phone_country_mismatch" in verdict.flags
        assert verdict.score_delta == -5
        assert verdict.hard_reject is False

    def test_italian_phone_no_flag(self) -> None:
        verdict = validate(email=None, vat_number=None, phone="+393331234567")
        assert "phone_country_mismatch" not in verdict.flags

    def test_local_phone_no_flag(self) -> None:
        """Domestic numbers without + prefix are not flagged."""
        verdict = validate(email=None, vat_number=None, phone="0331234567")
        assert "phone_country_mismatch" not in verdict.flags

    # ── None inputs ─────────────────────────────────────────────────────

    def test_all_none_returns_clean(self) -> None:
        verdict = validate(email=None, vat_number=None)
        assert verdict.score_delta == 0
        assert verdict.flags == []
        assert verdict.hard_reject is False
