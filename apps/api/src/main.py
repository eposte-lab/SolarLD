"""FastAPI app entry point.

Wires routers, middleware, exception handlers, and lifespan events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from starlette.responses import JSONResponse

from .core.config import settings
from .core.logging import configure_logging, get_logger
from .core.queue import close_pool as close_queue_pool
from .core.redis import close_redis
from .core.tier import BudgetExceededError, TierGateError
from .routes import (
    acquisition_campaigns,
    admin,
    analytics,
    auth,
    b2c_exports,
    b2c_outreach,
    branding,
    campaigns,
    cluster_ab,
    contatti,
    crm_webhooks,
    demo,
    email_domains,
    events,
    experiments,
    followup,
    health,
    inboxes,
    leads,
    modules,
    notifications,
    onboarding,
    outreach_sends,
    practices,
    prospector,
    public,
    quarantine,
    quotes,
    sector_news,
    sectors,
    tenants,
    territories,
    territory,
    unsubscribe,
    usage,
    warehouse,
    webhooks,
)

configure_logging()
log = get_logger(__name__)

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks."""
    log.info("api_starting", env=settings.app_env)
    yield
    await close_queue_pool()
    await close_redis()
    log.info("api_stopped")


app = FastAPI(
    title="SolarLead API",
    description="Agentic Lead Generation Platform — REST API + Webhooks",
    version="0.1.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

if settings.is_production:
    # Production: only explicitly whitelisted origins + the regex pattern.
    # allow_credentials=True is required because the browser sends the
    # Authorization Bearer header (custom header → preflight).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # Regex catches Vercel preview URLs / Railway preview URLs / custom
        # domains whose subdomain changes per-deploy.
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # Development / staging: accept all origins so Vercel preview URLs,
    # Railway preview URLs and local dev don't need manual whitelisting.
    # Safe because we use Authorization Bearer (not cookies) — the browser
    # sends the header explicitly regardless of allow_credentials.
    # NOTE: allow_credentials must be False when allow_origins=["*"].
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ---- Routers ----
app.include_router(health.router)
app.include_router(auth.router, prefix="/v1/auth", tags=["auth"])
app.include_router(tenants.router, prefix="/v1/tenants", tags=["tenants"])
app.include_router(modules.router, prefix="/v1/modules", tags=["modules"])
app.include_router(onboarding.router, prefix="/v1/onboarding", tags=["onboarding"])
app.include_router(territories.router, prefix="/v1/territories", tags=["territories"])
# FLUSSO 1 v3 — geocentric territory mapping (L0). Co-exists with
# /v1/territories (legacy Atoka-based scan endpoints) until v3 reaches
# production.
app.include_router(territory.router, prefix="/v1/territory", tags=["territory"])
app.include_router(leads.router, prefix="/v1/leads", tags=["leads"])
# Lead → Preventivo (formal quote PDF) endpoints. Mounted at /v1 (no
# prefix) because routes carry their own /leads/{lead_id}/quote path —
# keeps the URL surface consistent with how the UI thinks about a quote
# as a thing-belonging-to-a-lead rather than a top-level resource.
app.include_router(quotes.router, prefix="/v1", tags=["quotes"])
# GSE Practice (post-firma) endpoints. Same /v1 mount as quotes — the
# routes carry their own /leads/{id}/practice and /practices paths so
# the URL surface mirrors the resource hierarchy.
app.include_router(practices.router, prefix="/v1", tags=["practices"])
# /v1/campaigns kept for backward compat (returns outreach_sends data)
app.include_router(campaigns.router, prefix="/v1/campaigns", tags=["campaigns"])
# New primary endpoints
app.include_router(
    outreach_sends.router, prefix="/v1/outreach-sends", tags=["outreach-sends"]
)
app.include_router(
    acquisition_campaigns.router,
    prefix="/v1/acquisition-campaigns",
    tags=["acquisition-campaigns"],
)
app.include_router(contatti.router, prefix="/v1/contatti", tags=["contatti"])
app.include_router(prospector.router, prefix="/v1/prospector", tags=["prospector"])
app.include_router(events.router, prefix="/v1/events", tags=["events"])
app.include_router(webhooks.router, prefix="/v1/webhooks", tags=["webhooks"])
app.include_router(public.router, prefix="/v1/public", tags=["public"])
app.include_router(analytics.router, prefix="/v1/analytics", tags=["analytics"])
app.include_router(admin.router, prefix="/v1/admin", tags=["admin"])
# Customer-facing "Avvia test pipeline" CTA on the demo tenant.
# Auth is per-tenant (any role); the endpoint itself enforces the
# `is_demo` flag and the per-tenant 3-attempt counter (migration 0077).
app.include_router(demo.router, prefix="/v1/demo", tags=["demo"])
app.include_router(
    crm_webhooks.router, prefix="/v1/crm-webhooks", tags=["crm-webhooks"]
)
app.include_router(
    notifications.router, prefix="/v1/notifications", tags=["notifications"]
)
app.include_router(
    experiments.router, prefix="/v1/experiments", tags=["experiments"]
)
# Sprint 9 B.6: cluster-level A/B variant management
app.include_router(cluster_ab.router, prefix="/v1", tags=["cluster-ab"])
app.include_router(sector_news.router, prefix="/v1", tags=["sector-news"])
# Sprint C.2 — sector palette catalogue (read-only, public)
app.include_router(sectors.router, prefix="/v1/sectors", tags=["sectors"])
app.include_router(branding.router, prefix="/v1/branding", tags=["branding"])
app.include_router(inboxes.router, prefix="/v1/inboxes", tags=["inboxes"])
app.include_router(email_domains.router, prefix="/v1/email-domains", tags=["email-domains"])
app.include_router(b2c_outreach.router, prefix="/v1/b2c", tags=["b2c"])
app.include_router(b2c_exports.router, prefix="/v1/b2c", tags=["b2c-exports"])
# HMAC-signed unsubscribe endpoint (Task 12 / RFC 8058 one-click).
# No prefix — the routes are already /v1/unsubscribe (GET + POST).
app.include_router(unsubscribe.router)
app.include_router(usage.router, prefix="/v1/usage", tags=["usage"])
app.include_router(warehouse.router, prefix="/v1/warehouse", tags=["warehouse"])
app.include_router(quarantine.router, prefix="/v1/quarantine", tags=["quarantine"])
app.include_router(followup.router, prefix="/v1/followup", tags=["followup"])


@app.exception_handler(TierGateError)
async def tier_gate_handler(_req: Request, exc: TierGateError) -> JSONResponse:
    """Convert tier-gate exceptions to a sanitized 403.

    The exception's ``__str__`` is English internal copy ("Capability
    'foo' requires tier 'pro'…") — useful for logs and ops, useless to
    a tenant. We expose a generic Italian ``detail`` for the toast and
    a structured ``error`` payload (code + parameters) so the dashboard
    can render a tier-specific upgrade nudge without parsing free-form
    strings.
    """
    log.info(
        "tier_gate_blocked",
        capability=exc.capability.value,
        current_tier=exc.current_tier,
        required_tier=exc.required_tier,
    )
    return JSONResponse(
        status_code=403,
        content={
            "detail": (
                "Funzionalità non disponibile sul tuo piano. "
                "Aggiorna il piano per sbloccarla."
            ),
            "error": {
                "code": "tier_gate",
                "capability": exc.capability.value,
                "current_tier": exc.current_tier,
                "required_tier": exc.required_tier,
            },
        },
    )


@app.exception_handler(BudgetExceededError)
async def budget_exceeded_handler(_req: Request, exc: BudgetExceededError) -> JSONResponse:
    """Convert budget-exhaustion exceptions to a sanitized 402.

    Same logic as the tier handler: the technical breakdown
    (used_cents / limit_cents / budget enum value) is internal — we log
    it, but the user sees a plain Italian message and the structured
    payload powers any in-app upgrade card.
    """
    log.info(
        "budget_exceeded",
        budget=exc.budget.value,
        current_tier=exc.current_tier,
        used_cents=exc.used_cents,
        limit_cents=exc.limit_cents,
    )
    return JSONResponse(
        status_code=402,
        content={
            "detail": (
                "Budget mensile esaurito per questa funzionalità. "
                "Aggiorna il piano o attendi il prossimo ciclo."
            ),
            "error": {
                "code": "budget_exceeded",
                "budget": exc.budget.value,
                "current_tier": exc.current_tier,
            },
        },
    )


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "name": "SolarLead API",
        "version": "0.1.0",
        "env": settings.app_env,
        "docs": "/docs",
    }
