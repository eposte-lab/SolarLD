"""Hunter.io — finds decision-maker emails from a company domain.

Two endpoints we use:
  - `/domain-search` → list of all public emails for a domain
  - `/email-finder` → (first_name, last_name, domain) → verified email

We prefer `/email-finder` when we already know a decision-maker name (from
Visura / Atoka) because the single-email cost is $0.049 vs $0.34 for a full
domain scrape.

Docs: https://hunter.io/api-documentation/v2
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

HUNTER_BASE_URL = "https://api.hunter.io/v2"

# Stated pricing (2025): $49/mo plan = 500 verifications → ~10¢ per verification.
HUNTER_COST_PER_CALL_CENTS = 10


class HunterIoError(Exception):
    """Non-retryable Hunter.io error (bad key, domain format, etc.)."""


@dataclass(slots=True)
class HunterEmailResult:
    email: str | None
    first_name: str | None
    last_name: str | None
    position: str | None
    linkedin_url: str | None
    confidence_score: int  # 0-100
    sources_count: int
    verified: bool  # Hunter's internal flag (syntax + SMTP + not catch-all)
    raw: dict[str, Any]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
async def find_email(
    *,
    domain: str,
    first_name: str | None = None,
    last_name: str | None = None,
    company: str | None = None,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> HunterEmailResult | None:
    """Call `/email-finder` for a known person at a domain.

    Returns `None` when Hunter finds no verified match (caller should then
    try `/domain-search` as a broader fallback).
    """
    key = api_key or settings.hunter_api_key
    if not key:
        raise HunterIoError("HUNTER_API_KEY not configured")

    params: dict[str, str] = {"api_key": key}
    if domain:
        params["domain"] = domain
    if company:
        params["company"] = company
    if first_name:
        params["first_name"] = first_name
    if last_name:
        params["last_name"] = last_name

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(f"{HUNTER_BASE_URL}/email-finder", params=params)
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code == 404:
        return None
    if resp.status_code == 400:
        log.warning("hunter_io_bad_request", body=resp.text[:300])
        raise HunterIoError(f"bad request: {resp.text[:200]}")
    if resp.status_code >= 400:
        raise HunterIoError(f"status={resp.status_code} body={resp.text[:200]}")

    body = resp.json()
    data = body.get("data") or {}
    email = data.get("email")
    if not email:
        return None

    return HunterEmailResult(
        email=email,
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
        position=data.get("position"),
        linkedin_url=data.get("linkedin"),
        confidence_score=int(data.get("score", 0) or 0),
        sources_count=len(data.get("sources") or []),
        verified=bool(data.get("verification", {}).get("status") == "valid"),
        raw=data,
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
async def domain_search(
    domain: str,
    *,
    seniority: str = "executive,senior",
    department: str = "executive,management,sales",
    limit: int = 5,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> list[HunterEmailResult]:
    """Broad `/domain-search` fallback when we don't have a person name.

    Ranks returned emails by `confidence_score` desc; caller picks the top
    result (typically the CEO / owner for Italian SMEs).
    """
    key = api_key or settings.hunter_api_key
    if not key:
        raise HunterIoError("HUNTER_API_KEY not configured")

    params = {
        "api_key": key,
        "domain": domain,
        "seniority": seniority,
        "department": department,
        "limit": str(limit),
    }

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(f"{HUNTER_BASE_URL}/domain-search", params=params)
    finally:
        if owns_client:
            await client.aclose()

    if resp.status_code >= 400:
        raise HunterIoError(f"status={resp.status_code} body={resp.text[:200]}")

    body = resp.json()
    emails = (body.get("data") or {}).get("emails") or []
    results: list[HunterEmailResult] = []
    for e in emails:
        results.append(
            HunterEmailResult(
                email=e.get("value"),
                first_name=e.get("first_name"),
                last_name=e.get("last_name"),
                position=e.get("position"),
                linkedin_url=e.get("linkedin"),
                confidence_score=int(e.get("confidence", 0) or 0),
                sources_count=len(e.get("sources") or []),
                verified=bool(e.get("verification", {}).get("status") == "valid"),
                raw=e,
            )
        )
    # Sort descending by confidence
    results.sort(key=lambda r: r.confidence_score, reverse=True)
    return results
