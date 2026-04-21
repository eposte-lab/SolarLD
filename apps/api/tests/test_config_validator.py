"""Tests for the ``Settings`` model-validator that blocks staging /
production startup when dev defaults are still in place.

The validator is critical to staging safety — if it silently passes we
risk deploying with forgeable JWTs or missing webhook signing secrets.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_valid_kwargs(app_env: str) -> dict[str, str]:
    """A kwargs set that satisfies every check — baseline for mutation tests."""
    return {
        "app_env": app_env,
        "jwt_secret": "x" * 48,
        "supabase_service_role_key": "srk",
        # The public Supabase URL + anon key are read by the dashboard
        # and the portal; without them the frontends don't boot, so
        # the validator blocks staging too.
        "next_public_supabase_url": "https://example.supabase.co",
        "next_public_supabase_anon_key": "anon_key_placeholder",
        "anthropic_api_key": "sk-a",
        "resend_api_key": "re_k",
        "resend_webhook_secret": "rw_s",
        "resend_inbound_secret": "ri_s",
        "pixart_webhook_secret": "px_s",
        "dialog360_webhook_secret": "dw_s",
        "redis_url": "redis://red.example.com:6379",
    }


# ---------------------------------------------------------------------------
# Passes in development even with all defaults
# ---------------------------------------------------------------------------


def test_development_env_accepts_dev_defaults() -> None:
    # This is essentially the out-of-the-box developer case.
    s = Settings(app_env="development")  # type: ignore[call-arg]
    assert s.app_env == "development"


# ---------------------------------------------------------------------------
# Staging / production: rejects dev defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["staging", "production"])
def test_rejects_dev_jwt_default(env: str) -> None:
    kw = _full_valid_kwargs(env)
    kw["jwt_secret"] = "development-secret-change-me-min-32-chars"
    with pytest.raises(ValidationError) as exc:
        Settings(**kw)  # type: ignore[arg-type]
    assert "JWT_SECRET" in str(exc.value)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_rejects_short_jwt(env: str) -> None:
    kw = _full_valid_kwargs(env)
    kw["jwt_secret"] = "short"
    with pytest.raises(ValidationError):
        Settings(**kw)  # type: ignore[arg-type]


@pytest.mark.parametrize("env", ["staging", "production"])
def test_rejects_localhost_redis(env: str) -> None:
    kw = _full_valid_kwargs(env)
    kw["redis_url"] = "redis://localhost:6379"
    with pytest.raises(ValidationError) as exc:
        Settings(**kw)  # type: ignore[arg-type]
    assert "REDIS_URL" in str(exc.value)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_rejects_127_redis(env: str) -> None:
    kw = _full_valid_kwargs(env)
    kw["redis_url"] = "redis://127.0.0.1:6379"
    with pytest.raises(ValidationError):
        Settings(**kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "missing_field",
    [
        "supabase_service_role_key",
        "anthropic_api_key",
        "resend_api_key",
        "resend_webhook_secret",
        "resend_inbound_secret",
        "pixart_webhook_secret",
        "dialog360_webhook_secret",
    ],
)
def test_rejects_missing_required_secret_in_staging(missing_field: str) -> None:
    kw = _full_valid_kwargs("staging")
    kw[missing_field] = ""
    with pytest.raises(ValidationError):
        Settings(**kw)  # type: ignore[arg-type]


def test_reports_all_errors_in_one_raise() -> None:
    """If more than one secret is wrong we surface them all — the dev
    fixes the deploy in one pass rather than whack-a-mole-ing one at a time."""
    kw = _full_valid_kwargs("staging")
    kw["anthropic_api_key"] = ""
    kw["resend_api_key"] = ""
    kw["jwt_secret"] = "development-secret-change-me-min-32-chars"

    with pytest.raises(ValidationError) as exc:
        Settings(**kw)  # type: ignore[arg-type]
    msg = str(exc.value)
    assert "JWT_SECRET" in msg
    assert "ANTHROPIC_API_KEY" in msg
    assert "RESEND_API_KEY" in msg


def test_meta_app_id_without_secret_rejected() -> None:
    """Setting META_APP_ID opts into the Meta Lead Ads integration;
    without the matching secret + verify token the webhook handler
    would reject every inbound POST in staging. Block startup."""
    kw = _full_valid_kwargs("staging")
    kw["meta_app_id"] = "1234567890"
    # meta_app_secret + meta_app_verify_token intentionally unset
    with pytest.raises(ValidationError) as exc:
        Settings(**kw)  # type: ignore[arg-type]
    msg = str(exc.value)
    assert "META_APP_SECRET" in msg
    assert "META_APP_VERIFY_TOKEN" in msg


def test_meta_app_id_unset_no_meta_checks() -> None:
    """Tenants that haven't enabled the Meta channel should NOT trip
    the Meta validators. Leaving all three Meta fields blank is the
    valid "Meta off" posture."""
    kw = _full_valid_kwargs("staging")
    # No meta_* keys — baseline passes
    s = Settings(**kw)  # type: ignore[arg-type]
    assert s.meta_app_id == ""


def test_full_valid_staging_config_passes() -> None:
    s = Settings(**_full_valid_kwargs("staging"))  # type: ignore[arg-type]
    assert s.app_env == "staging"
    assert s.is_production is False


def test_full_valid_production_config_passes() -> None:
    s = Settings(**_full_valid_kwargs("production"))  # type: ignore[arg-type]
    assert s.app_env == "production"
    assert s.is_production is True
