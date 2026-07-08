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
    #   - any *.solarld.app          (legacy custom domain)
    #   - solarlead.it + sottodomini (dashboard su solarlead.it, portale
    #     lead su portale.solarlead.it — produzione)
    #   - localhost / 127.0.0.1 with any port
    cors_origin_regex: str = (
        r"^https://([a-z0-9-]+\.)*vercel\.app$"
        r"|^https://([a-z0-9-]+\.)*up\.railway\.app$"
        r"|^https://([a-z0-9-]+\.)*solarld\.app$"
        r"|^https://([a-z0-9-]+\.)*solarlead\.it$"
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

    # Client-side throttle on Replicate prediction CREATES (per-account limit).
    # Replicate rate-limits "creating predictions" per-account; this account has
    # been reduced to ~6/min, burst 1, so firing the render pipeline at arq
    # concurrency self-inflicted 429s (mass lead.render_skipped → starved sends,
    # 2026-06-17). ``services/replicate_throttle`` serialises + spaces every
    # create POST to stay at/under this rate. Raise it (via env
    # REPLICATE_CREATES_PER_MIN) when the Replicate plan/limit is restored.
    replicate_creates_per_min: int = 6

    # When set, the CreativeAgent skips ALL Replicate-dependent steps
    # (nano-banana panel paint AND Kling 1.6-Pro video transition) and
    # falls back to a fully-local rendering: PIL-geometric panel
    # overlay drawn on the real Google Solar aerial. The "after"
    # image is still produced — just deterministically rather than
    # via instruction-edit AI — and the video step is bypassed
    # entirely (the email shows the static after-image as the hero
    # instead of a GIF/MP4). Use this when the Replicate account is
    # out of credits but Solar API + a usable static after-image are
    # still wanted.
    creative_skip_replicate: bool = False

    # ---- Worker / send-pipeline resilience ----
    # arq concurrency cap. The worker runs on a small Railway instance; at the
    # old hardcoded 10 a morning creative burst loaded ~10 multi-MB Google
    # ``raw_data`` blobs at once and the process was OOM-killed mid-burst,
    # restarted, re-drained the same backlog, and died again — leaving the
    # deferred outreach_tasks unprocessed and the daily sends at zero
    # (2026-06-18 incident). A lower cap trades a little throughput for a worker
    # that survives the burst. Raise via WORKER_MAX_JOBS once the instance is
    # bigger.
    worker_max_jobs: int = 4
    # Event-loop watchdog: if a single sync-blocking job wedges the worker's
    # event loop (the 2026-06-18 silent freeze — no crash, so Railway never
    # restarted it, and sends stopped until noticed), a daemon thread that the
    # wedged loop can't block force-exits the process after this many seconds
    # of no heartbeat, so the container restarts and the stranded-pick rescue
    # cron re-fires the sends. 0 disables it.
    worker_watchdog_timeout_seconds: int = 180
    # Funnel-stall hardening (2026-06-26). The watchdog above only sees a GIL/sync
    # wedge — it is BLIND to a coroutine parked on an unbounded ``await``. On
    # 2026-06-26 the L4 existing-PV vision call had no explicit client timeout
    # (Anthropic SDK default read=600s, pool=600s), so ONE hung request stalled the
    # whole sequential L4 batch and froze consumption for ~3h while crons kept
    # firing. An explicit per-request timeout caps that single call so the batch
    # keeps moving; the run itself stays bounded by arq job_timeout=600 (sized for
    # the 120-candidate batch — do NOT add a tighter whole-run timeout or normal
    # batches get cut and re-looped), and funnel_stall_recovery re-enqueues if
    # consumption stalls anyway.
    vision_request_timeout_seconds: int = 30  # per Anthropic vision request
    # Recovery cron: if work is available (active scan job + consumable un-processed
    # candidates) but nothing has been consumed in this long, re-enqueue the funnel.
    # ALERT + RE-ENQUEUE only — never a process bail, to avoid the 2026-06-18
    # restart-loop.
    funnel_stall_seconds: int = 1800
    # When an outreach send is skipped for a TRANSIENT reason (per-inbox /
    # domain rate-limit), the lead used to be left in ``picked`` forever with
    # no retry — a backlog of overdue sends draining at once collided on the
    # 180s inter-send floor and every loser was stranded (the recurring
    # "zombie picked" leads). Instead we re-enqueue the outreach_task deferred
    # by ``outreach_retry_delay_seconds``, up to ``outreach_retry_max`` times,
    # so it rides out the rate window and still goes today.
    outreach_retry_max: int = 12
    outreach_retry_delay_seconds: int = 300
    # Send to NeverBounce "unknown" results too (block only confirmed
    # invalid/disposable). "unknown" is what NeverBounce returns for catch-all
    # domains it can't probe — usually a reachable mailbox the L6 waterfall
    # already found on the company site, so skipping it threw away ~2/3 of the
    # B2B leads (2026-06-18). Flip to False to restore the strict "verified
    # only" behaviour if the warming domain's bounce rate climbs.
    outreach_send_to_unknown_email: bool = True
    # FROM address for INTERNAL operator notifications (e.g. the "new contact
    # request" email to tenants.contact_email). These must NOT go through the
    # tenant's warm-up outreach inbox: when the operator's own mailbox is on the
    # same root domain as the outreach subdomain (info@totaltrade.it vs
    # commerciale@commerciale.totaltrade.it), the receiving server (Aruba)
    # rejects it as a spoof — "501 invalid sender domain" (2026-06-18). Routing
    # them through the platform's own verified transactional domain fixes
    # deliverability AND keeps non-cold mail off the warm-up reputation. The
    # tenant's business name is prepended as the display name; reply-to stays
    # the prospect. Must be a Resend-verified domain.
    notification_from_email: str = "notifiche@agenda-pro.it"

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
    # Maps Static API key for the satellite fallback when Solar dataLayers
    # has no coverage. Falls back to google_solar_api_key in code, so the
    # operator can either set a dedicated key or just enable "Maps Static
    # API" on the existing Solar key's project.
    google_maps_static_api_key: str = ""
    mapbox_access_token: str = ""

    # Set GOOGLE_SOLAR_MOCK_MODE=true to bypass the real Solar API and
    # generate plausible synthetic roof data.  Only active when
    # google_solar_api_key is not configured; real key always wins.
    google_solar_mock_mode: bool = False

    # ---- Realistic roof sizing (anti over-placement) ----
    # Google's solarPanels list is its MAXIMUM array — on complex urban roofs it
    # fills every sliver/structure (e.g. 857 panels across 102 segments), which
    # inflates kWp/€ and over-promises. When enabled, the parsed RoofInsight is
    # trimmed to the genuinely installable roof (drop slivers + steep faces) and
    # estimated_kwp / estimated_yearly_kwh / panels are recomputed from the kept
    # subset — so layout AND quoted numbers stay honest. Tunable; fail-open.
    realistic_sizing_enabled: bool = True
    # Keep a roof segment only if its panel count is at least this FRACTION of
    # the largest segment's — i.e. keep the main roof planes, drop the scattered
    # fill Google spreads across the whole complex. 0.30 ≈ "main roof", trims
    # ~23% of panels on average for Total Trade (the over-placement cases are the
    # complex multi-segment roofs; simple roofs are untouched).
    realistic_sizing_min_segment_fraction: float = 0.30
    # Drop a roof segment steeper than this (likely a facade/wall, not a roof).
    realistic_sizing_max_pitch_deg: float = 50.0

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
    # ---- Contact-enrichment waterfall (0152) ----
    hunter_confidence_min: int = 80
    max_verifications_per_lead: int = 6
    domain_intel_ttl_days: int = 60
    per_run_budget_eur: float = 25.0
    contact_enrichment_concurrency: int = 3
    # ---- "Persona responsabile" delta (0165) — reach the real decision-maker.
    # All default OFF so the live funnel is unchanged until the operator opts in.
    # Modifica 1: resolve the decision-maker from the Registro Imprese
    # (OpenAPI IT-stakeholders) BEFORE the Hunter/website path (LinkedIn/Hunter
    # become confirmation, never discard when absent).
    decision_maker_registro_first: bool = False
    # Modifica 2: build name-based email permutations + verify them in ONE
    # NeverBounce batch, pick the best deliverable.
    email_permutations_enabled: bool = False
    # Modifica 2 sub-policy: if no permutation is 'valid' but the domain is
    # accept-all/unknown, keep the most-probable one at medium confidence.
    acceptall_as_medium_confidence: bool = True
    # Modifica 3: when there is no personal email, fall back to the company PEC
    # (OpenAPI IT-pec) with a sober tone before dropping to a phone-only task.
    pec_fallback_enabled: bool = False
    # Modifica 4: personalise the cold (step-1) subject line with the decision-
    # maker name / lead company instead of the generic vendor-led default
    # ("{tenant} — analisi fotovoltaica…"), which leads with the sender name and
    # opens poorly. Falls back to the generic when neither is available.
    personalized_subject_enabled: bool = False
    # ---- Energivori Delta 2 — geo Centro-Sud + contact gate ----
    # Change A: service-area regions (Centro-Sud + Isole). The geo pass keeps
    # only companies whose registered province falls in these regions.
    energivori_regions: list[str] = [
        "Lazio",
        "Abruzzo",
        "Molise",
        "Campania",
        "Puglia",
        "Basilicata",
        "Calabria",
        "Sicilia",
        "Sardegna",
    ]
    # Rome (RM) is huge + often out-of-area; flip False to exclude just RM.
    energivori_include_roma: bool = True
    # OpenAPI.it (https://console.openapi.com) — pay-as-you-go REST
    # access to the Italian company registry. Used by the prospector
    # for sectors where Google Places returns the wrong category
    # (es. amministratori condominio: ATECO 68.32 / 81.10). Empty
    # token disables the integration: the prospector falls back to
    # Google Places for every sector.
    openapi_it_token: str = ""
    # Sandbox vs production base URL. The sandbox returns the same
    # shape but with synthetic data and no billing impact — useful
    # during integration smoke tests.
    openapi_it_use_sandbox: bool = False

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

    # Demo email routing — when set, ALL emails sent during a demo pipeline
    # run (is_demo=true tenant) are delivered to this address instead of the
    # prospect's typed email. Set to your QA inbox (e.g. qa@agenda-pro.it)
    # so you can visually inspect the rendered email without spamming real
    # inboxes during testing. Leave empty to deliver to the prospect's typed
    # address (normal behaviour during live sales demos).
    #
    # The decision-maker email stored on the `subjects` row is still set to
    # the prospect's typed value so copy personalisation ("Caro Mario,…")
    # works correctly — only the OutreachAgent delivery address is overridden.
    #
    # Example: DEMO_EMAIL_RECIPIENT_OVERRIDE=qa@agenda-pro.it
    demo_email_recipient_override: str = ""

    # ---- CDN (Cloudflare R2 — Sprint 9: rendering GIF public delivery) ----
    # Public base URL for the R2 bucket (no trailing slash).
    # Example: https://cdn.solarld.app
    cdn_base_url: str = ""
    # Cloudflare R2 S3-compatible credentials. Endpoint must be the full URL:
    # https://<account_id>.r2.cloudflarestorage.com
    r2_endpoint: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = "solarld-renderings"

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
    def _validate_non_dev_env_secrets(self) -> Settings:
        if self.app_env not in {"staging", "production"}:
            return self

        errors: list[str] = []
        if self.jwt_secret == self._DEV_JWT_DEFAULT or len(self.jwt_secret) < 32:
            errors.append(
                "JWT_SECRET must be set to a strong random value (≥32 chars, not the dev default)."
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
            errors.append("REDIS_URL must point at the managed Redis instance, not localhost.")
        # Meta Marketing checks are conditional — a tenant can run
        # without the B2C Meta channel. But if they've configured the
        # app id they've opted into the integration and the other
        # secrets must be present too, otherwise the webhook handler
        # rejects every POST in staging.
        if self.meta_app_id:
            if not self.meta_app_secret:
                errors.append("META_APP_SECRET must be set when META_APP_ID is.")
            if not self.meta_app_verify_token:
                errors.append("META_APP_VERIFY_TOKEN must be set when META_APP_ID is.")
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
