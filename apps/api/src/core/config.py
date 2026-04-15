"""Application settings via pydantic-settings.

Single source of truth for all environment variables.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
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

    # ---- Geo / Roof ----
    google_solar_api_key: str = ""
    mapbox_access_token: str = ""

    # ---- Italian business data ----
    visura_api_key: str = ""
    atoka_api_key: str = ""
    hunter_api_key: str = ""
    neverbounce_api_key: str = ""

    # ---- Email ----
    resend_api_key: str = ""
    resend_webhook_secret: str = ""

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance (one per process)."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
