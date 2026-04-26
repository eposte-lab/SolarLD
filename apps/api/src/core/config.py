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
    # Regex for dynamic frontend origins (e.g. Vercel preview URLs whose
    # subdomain changes per-deploy). Anything matching this regex is
    # accepted in addition to `cors_origins`. Kept env-driven so prod can
    # tighten it (e.g. only this team's projects) without a code change.
    # Default matches:
    #   - any *.vercel.app           (Vercel preview + production deployments)
    #   - any *.up.railway.app       (Railway preview deployments)
    #   - any *.solarld.app          (custom production domain)
    #   - localhost / 127.0.0.1 with any port
    cors_origin_regex: str = (
        r"^https://([a-z0-9-]+\.)*vercel\.app$"
        r"|^https://([a-z0-9-]+\.)*up\.railway\.app$"
        r"|^https://([a-z0-9-]+\.)*solarld\.app$"
        r"|^http://localhost(:\d+)?$"
        r"|^http://127\.0\.0\.1(:\d+)?$"
    )

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
    # Cheap ranker for funnel L3 proxy scoring. Kept as a separate setting
    # so the default Sonnet can be swapped for newer models independently.
    anthropic_haiku_model: str = "claude-haiku-4-5"
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

    # Set GOOGLE_SOLAR_MOCK_MODE=true to bypass the real Solar API and
    # generate plausible synthetic roof data.  Only active when
    # google_solar_api_key is not configured; real key always wins.
    google_solar_mock_mode: bool = False

    # ---- Creative rendering engine ----
    # "google_solar" (default, only active path): fetch RGB aerial from
    #   Google Solar dataLayers + draw panel geometry deterministically
    #   with PIL — no AI, no Replicate.
    # "replicate": reserved for future re-activation of the legacy
    #   Stable Diffusion inpainting path.  Not currently wired in
    #   creative.py — setting this has no effect.
    creative_rendering_engine: Literal["google_solar", "replicate"] = "google_solar"

    # ---- Italian business data ----
    visura_api_key: str = ""
    atoka_api_key: str = ""
    hunter_api_key: str = ""
    neverbounce_api_key: str = ""

    # ---- Atoka mock mode (dev / integration testing without a real key) ----
    # Set ATOKA_MOCK_MODE=true to bypass the real Atoka API and generate
    # deterministic synthetic Italian businesses instead.  Mock VATs start
    # with IT9999 so they never collide with real records.
    # Safe to leave false in staging/production — has no effect when
    # atoka_api_key is set (real key always takes priority over mock).
    atoka_mock_mode: bool = False
    # How many synthetic companies to generate per L1 discovery run.
    # Keep ≤ 50 in dev to stay fast; increase for load testing.
    atoka_mock_count: int = 20

    # ---- Email ----
    resend_api_key: str = ""
    resend_webhook_secret: str = ""
    resend_inbound_secret: str = ""  # shared secret appended as ?secret= on inbound webhook URL

    # ---- Smartlead.ai (Task 14 — warm-up management) ----
    # Obtain from https://app.smartlead.ai → Settings → API.
    # Required for: inbox warm-up enrollment, daily sync cron,
    # and CLI `python -m src.services.smartlead_service enroll-all`.
    smartlead_api_key: str = ""

    # ---- Postal ----
    pixart_api_key: str = ""
    pixart_webhook_secret: str = ""

    # ---- WhatsApp ----
    dialog360_api_key: str = ""
    dialog360_webhook_secret: str = ""

    # ---- Meta Marketing / Lead Ads ----
    # `meta_app_verify_token`: long random string we give Meta when we
    # register the webhook subscription. Meta echoes it back on the
    # one-time GET challenge (`hub.verify_token`). Per-tenant HMAC
    # signing secrets live on `meta_connections.webhook_secret`.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_app_verify_token: str = ""

    # ---- Payments ----
    # Stripe integration is intentionally not wired in this release.
    # Tier activation is manual (see `apps/dashboard/src/lib/data/tier.ts`
    # and `tier-lock.tsx`): installers contact ops via mailto CTA, ops
    # flips the tenant's tier in Supabase. When billing is reintroduced
    # add `stripe_secret_key`, `stripe_webhook_secret`,
    # `stripe_publishable_key` here together with
    # `services/billing_service.py` and migration
    # `0037_tenants_subscription.sql`. Leaving half-wired config fields
    # here would only tempt callers to assume the webhook route works —
    # and it doesn't.

    # ---- Monitoring ----
    sentry_dsn: str = ""
    posthog_key: str = ""
    posthog_host: str = "https://eu.posthog.com"

    # ---- Security ----
    jwt_secret: str = "development-secret-change-me-min-32-chars"
    encryption_key: str = ""
    # Fernet key for encrypting OAuth refresh tokens at rest in
    # tenant_inboxes.oauth_refresh_token_encrypted. Must be a urlsafe
    # base64-encoded 32-byte key (generate with `Fernet.generate_key()`).
    # Leave empty in dev if Gmail OAuth isn't being tested; required when
    # any inbox has provider='gmail_oauth' or 'm365_oauth'.
    app_secret_key: str = ""

    # ---- Google OAuth (Gmail API cold outreach) ----
    # Obtained from https://console.cloud.google.com → OAuth 2.0 Client IDs.
    # Redirect URI to register (static — inbox_id travels in the signed JWT state):
    #   {api_base_url}/v1/inboxes/oauth/gmail/callback
    # Scope: https://www.googleapis.com/auth/gmail.send
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    # ---- Microsoft 365 OAuth (Graph API Mail.Send) ----
    # Reserved for Sprint 6.1 phase B (Office365 tenants). Azure AD app
    # registration with Mail.Send delegated scope.
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""
    microsoft_oauth_tenant_id: str = "common"  # "common" allows any Microsoft account

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
        # Meta Marketing checks are conditional — a tenant can run
        # without the B2C Meta channel. But if they've configured the
        # app id they've opted into the integration and the other
        # secrets must be present too, otherwise the webhook handler
        # rejects every POST in staging.
        if self.meta_app_id:
            if not self.meta_app_secret:
                errors.append(
                    "META_APP_SECRET must be set when META_APP_ID is."
                )
            if not self.meta_app_verify_token:
                errors.append(
                    "META_APP_VERIFY_TOKEN must be set when META_APP_ID is."
                )
        # Supabase URL sanity — a missing anon key means the public
        # portal won't boot. The service-role key is checked above.
        if not self.next_public_supabase_url:
            errors.append("NEXT_PUBLIC_SUPABASE_URL must be set.")
        if not self.next_public_supabase_anon_key:
            errors.append("NEXT_PUBLIC_SUPABASE_ANON_KEY must be set.")

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
