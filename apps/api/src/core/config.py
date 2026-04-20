"""Application settings via pydantic-settings.

Single source of truth for all environment variables.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All environment-backed configuration."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Runtime ----
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = True
    log_level: str = "INFO"

    # ---- API ----
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    # ---- Supabase ----
    next_public_supabase_url: str = Field(default="", alias="NEXT_PUBLIC_SUPABASE_URL")
    next_public_supabase_anon_key: str = Field(default="", alias="NEXT_PUBLIC_SUPABASE_ANON_KEY")
    supabase_service_role_key: str = ""
    supabase_db_url: str = "postgresql://postgres:postgres@localhost:54322/postgres"
    supabase_jwt_secret: str = ""

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379"
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""

    # ---- AI ----
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    replicate_api_token: str = ""

    # ---- Remotion sidecar (apps/video-renderer) ----
    video_renderer_url: str = "http://localhost:4000"

    # ---- Public-facing frontends (used inside email templates) ----
    next_public_lead_portal_url: str = Field(
        default="http://localhost:3001", alias="NEXT_PUBLIC_LEAD_PORTAL_URL"
    )
    next_public_dashboard_url: str = Field(
        default="http://localhost:3000", alias="NEXT_PUBLIC_DASHBOARD_URL"
    )

    # ---- Geo / Roof ----
    google_solar_api_key: str = ""
    google_places_api_key: str = ""
    mapbox_access_token: str = ""

    # ---- Italian business data ----
    visura_api_key: str = ""
    atoka_api_key: str = ""
    hunter_api_key: str = ""
    neverbounce_api_key: str = ""

    # ---- Email ----
    resend_api_key: str = ""
    resend_webhook_secret: str = ""
    resend_inbound_secret: str = ""  # shared secret appended as ?secret= on inbound webhook URL

    # ---- Postal ----
    pixart_api_key: str = ""
    pixart_webhook_secret: str = ""

    # ---- WhatsApp ----
    dialog360_api_key: str = ""
    dialog360_webhook_secret: str = ""

    # ---- Payments ----
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""

    # ---- Monitoring ----
    sentry_dsn: str = ""
    posthog_key: str = ""
    posthog_host: str = "https://eu.posthog.com"

    # ---- Security ----
    jwt_secret: str = "development-secret-change-me-min-32-chars"
    encryption_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        """Return parsed CORS origins as a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    # ------------------------------------------------------------------
    # Safety: forbid staging/production startup with dev defaults.
    # ------------------------------------------------------------------
    # A dangerously permissive dev secret in staging makes JWTs forgeable.
    # Localhost Redis in staging would silently send all jobs into the
    # developer's dev queue. Every missing external credential would
    # silently turn an inbound webhook into a no-op. Listing them all
    # here and raising early keeps a staging/production deploy from
    # starting in a broken state.

    _DEV_JWT_DEFAULT = "development-secret-change-me-min-32-chars"

    @model_validator(mode="after")
    def _validate_non_dev_env_secrets(self) -> "Settings":
        if self.app_env not in {"staging", "production"}:
            return self

        errors: list[str] = []
        if self.jwt_secret == self._DEV_JWT_DEFAULT or len(self.jwt_secret) < 32:
            errors.append(
                "JWT_SECRET must be set to a strong random value "
                "(≥32 chars, not the dev default)."
            )
        if not self.supabase_service_role_key:
            errors.append("SUPABASE_SERVICE_ROLE_KEY must be set.")
        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY must be set.")
        if not self.resend_api_key:
            errors.append("RESEND_API_KEY must be set.")
        if not self.resend_webhook_secret:
            errors.append("RESEND_WEBHOOK_SECRET must be set.")
        # Inbound reply handler uses a URL shared-secret; if you haven't
        # configured it the webhook silently ignores replies.
        if not self.resend_inbound_secret:
            errors.append("RESEND_INBOUND_SECRET must be set.")
        # Postal + WhatsApp webhooks both require HMAC secrets to not
        # return 401 in production traffic.
        if not self.pixart_webhook_secret:
            errors.append("PIXART_WEBHOOK_SECRET must be set.")
        if not self.dialog360_webhook_secret:
            errors.append("DIALOG360_WEBHOOK_SECRET must be set.")
        if self.redis_url.startswith("redis://localhost") or self.redis_url.startswith(
            "redis://127.0.0.1"
        ):
            errors.append(
                "REDIS_URL must point at the managed Redis instance, "
                "not localhost."
            )

        if errors:
            bullets = "\n  - ".join(errors)
            raise ValueError(
                f"Cannot start in app_env={self.app_env!r} with dev defaults:\n"
                f"  - {bullets}\n"
                "Set the missing env vars or downgrade APP_ENV=development."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance (one per process)."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
