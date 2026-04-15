"""Health & readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from ..core.redis import get_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict[str, object]:
    """Readiness probe: checks Redis connectivity."""
    checks: dict[str, object] = {"redis": "unknown"}
    try:
        r = get_redis()
        pong = await r.ping()
        checks["redis"] = "ok" if pong else "fail"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"

    ok = all(v == "ok" for v in checks.values())
    return {"status": "ready" if ok else "degraded", "checks": checks}
