"""LinkedIn on-demand enrichment via Proxycurl (FLUSSO 1 v3).

Triggered on-demand from the lead detail UI, NOT in batch.
Reasons (per product decision):
  * Proxycurl charges ~$0.01 per Person Lookup, ~$0.005 per Company.
    Doing it batched on every L2 candidate would cost €5-10/day per tenant.
  * Operator already has Places + scraped data; LinkedIn is needed only
    when the operator wants to "deep-research" a specific lead.

Caching: results are stored on `subjects.linkedin_data JSONB` so a
second click on the same lead is free. The cache invalidates after 60
days (LinkedIn data drifts: employees, decision makers, status).

Conformance: Proxycurl is LinkedIn-as-a-Service — they handle the
scraping legality. We pay; they deliver. ToS-compliant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


# Proxycurl endpoints (https://nubela.co/proxycurl/docs).
PROXYCURL_COMPANY_URL = (
    "https://nubela.co/proxycurl/api/linkedin/company/resolve/"
)
PROXYCURL_PERSON_URL = (
    "https://nubela.co/proxycurl/api/linkedin/profile/resolve/"
)

# Per-call costs in cents.
PROXYCURL_COMPANY_CENTS = 1   # ~$0.005
PROXYCURL_PERSON_CENTS = 1    # ~$0.01

# Cache TTL: data drifts (employees, founder titles).
CACHE_TTL_DAYS = 60


@dataclass(slots=True)
class LinkedInCompany:
    found: bool = False
    linkedin_url: str | None = None
    name: str | None = None
    description: str | None = None
    employee_count_range: str | None = None
    employee_count: int | None = None
    industry: str | None = None
    founded_year: int | None = None
    hq_country: str | None = None
    hq_city: str | None = None
    website: str | None = None
    raw: dict[str, Any] | None = None


def _is_cache_fresh(cached_at: str | None) -> bool:
    if not cached_at:
        return False
    try:
        ts = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return datetime.now(timezone.utc) - ts < timedelta(days=CACHE_TTL_DAYS)


def _parse_company(payload: dict[str, Any]) -> LinkedInCompany:
    """Normalize Proxycurl Company resolve response."""
    if not payload or not isinstance(payload, dict):
        return LinkedInCompany(found=False)

    return LinkedInCompany(
        found=True,
        linkedin_url=payload.get("linkedin_internal_id")
        and f"https://www.linkedin.com/company/{payload.get('linkedin_internal_id')}",
        name=payload.get("name"),
        description=(payload.get("description") or "")[:1000] or None,
        employee_count_range=payload.get("company_size_on_linkedin")
        or (
            f"{payload['company_size'][0]}-{payload['company_size'][1]}"
            if isinstance(payload.get("company_size"), list)
            and len(payload["company_size"]) == 2
            else None
        ),
        employee_count=payload.get("company_size_on_linkedin"),
        industry=payload.get("industry"),
        founded_year=payload.get("founded_year"),
        hq_country=(payload.get("hq") or {}).get("country") if isinstance(payload.get("hq"), dict) else None,
        hq_city=(payload.get("hq") or {}).get("city") if isinstance(payload.get("hq"), dict) else None,
        website=payload.get("website"),
        raw=payload,
    )


async def lookup_company(
    *,
    company_name: str,
    company_domain: str | None = None,
    location: str | None = None,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> LinkedInCompany:
    """Resolve a LinkedIn Company page from name / domain / location.

    `company_domain` is the highest-precision signal (e.g. acme.it →
    LinkedIn page acme-srl). When missing, name + city is the fallback.
    """
    key = api_key or getattr(settings, "proxycurl_api_key", None)
    if not key:
        log.debug("linkedin_proxycurl.skip_no_key")
        return LinkedInCompany(found=False)

    if not company_name and not company_domain:
        return LinkedInCompany(found=False)

    params: dict[str, str] = {}
    if company_domain:
        params["company_domain"] = company_domain
    if company_name:
        params["company_name"] = company_name
    if location:
        params["location"] = location
    params["enrich_profile"] = "enrich"  # ask for the full profile

    headers = {"Authorization": f"Bearer {key}"}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        try:
            resp = await client.get(
                PROXYCURL_COMPANY_URL, params=params, headers=headers
            )
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            log.warning(
                "linkedin_proxycurl.network_error",
                err=type(exc).__name__,
            )
            return LinkedInCompany(found=False)
        if resp.status_code == 404:
            return LinkedInCompany(found=False)
        if resp.status_code >= 400:
            log.warning(
                "linkedin_proxycurl.bad_status",
                status=resp.status_code,
                body=resp.text[:200],
            )
            return LinkedInCompany(found=False)
        try:
            data = resp.json()
        except ValueError:
            return LinkedInCompany(found=False)
        return _parse_company(data)
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Cache wrapper for the lead-detail endpoint
# ---------------------------------------------------------------------------


async def lookup_company_cached(
    *,
    sb: Any,
    subject_id: str,
    company_name: str,
    company_domain: str | None = None,
    location: str | None = None,
    force_refresh: bool = False,
) -> tuple[LinkedInCompany, bool]:
    """Lookup with `subjects.linkedin_data` JSONB cache.

    Returns ``(record, hit_from_cache)``. The endpoint logs cache hits
    so the operator can see whether a Proxycurl call was billed.
    """
    if not force_refresh:
        try:
            res = (
                sb.table("subjects")
                .select("linkedin_data")
                .eq("id", subject_id)
                .maybeSingle()
                .execute()
            )
            cached = (res.data or {}).get("linkedin_data") or {}
        except Exception:
            cached = {}
        if cached and _is_cache_fresh(cached.get("cached_at")):
            return _parse_company(cached.get("raw") or {}), True

    found = await lookup_company(
        company_name=company_name,
        company_domain=company_domain,
        location=location,
    )

    # Persist (even if not found, store an empty stub to avoid re-billing
    # for 60 days on businesses that just aren't on LinkedIn).
    try:
        sb.table("subjects").update(
            {
                "linkedin_data": {
                    "found": found.found,
                    "raw": found.raw or {},
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        ).eq("id", subject_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "linkedin_proxycurl.cache_write_failed",
            subject_id=subject_id,
            err=type(exc).__name__,
        )

    return found, False
