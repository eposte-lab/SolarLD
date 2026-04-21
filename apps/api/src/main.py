"""FastAPI app entry point.

Wires routers, middleware, exception handlers, and lifespan events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from .core.config import settings
from .core.logging import configure_logging, get_logger
from .core.queue import close_pool as close_queue_pool
from .core.redis import close_redis
from .routes import (
    admin,
    analytics,
    auth,
    b2c_exports,
    b2c_outreach,
    branding,
    campaigns,
    crm_webhooks,
    events,
    experiments,
    health,
    leads,
    modules,
    notifications,
    public,
    tenants,
    territories,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Routers ----
app.include_router(health.router)
app.include_router(auth.router, prefix="/v1/auth", tags=["auth"])
app.include_router(tenants.router, prefix="/v1/tenants", tags=["tenants"])
app.include_router(modules.router, prefix="/v1/modules", tags=["modules"])
app.include_router(territories.router, prefix="/v1/territories", tags=["territories"])
app.include_router(leads.router, prefix="/v1/leads", tags=["leads"])
app.include_router(campaigns.router, prefix="/v1/campaigns", tags=["campaigns"])
app.include_router(events.router, prefix="/v1/events", tags=["events"])
app.include_router(webhooks.router, prefix="/v1/webhooks", tags=["webhooks"])
app.include_router(public.router, prefix="/v1/public", tags=["public"])
app.include_router(analytics.router, prefix="/v1/analytics", tags=["analytics"])
app.include_router(admin.router, prefix="/v1/admin", tags=["admin"])
app.include_router(
    crm_webhooks.router, prefix="/v1/crm-webhooks", tags=["crm-webhooks"]
)
app.include_router(
    notifications.router, prefix="/v1/notifications", tags=["notifications"]
)
app.include_router(
    experiments.router, prefix="/v1/experiments", tags=["experiments"]
)
app.include_router(branding.router, prefix="/v1/branding", tags=["branding"])
app.include_router(b2c_outreach.router, prefix="/v1/b2c", tags=["b2c"])
app.include_router(b2c_exports.router, prefix="/v1/b2c", tags=["b2c-exports"])


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "name": "SolarLead API",
        "version": "0.1.0",
        "env": settings.app_env,
        "docs": "/docs",
    }
