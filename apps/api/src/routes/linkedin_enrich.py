"""On-demand LinkedIn enrichment endpoint (FLUSSO 1 v3, Sprint 4.3).

Triggered from the lead detail UI when the operator clicks "Cerca su
LinkedIn". Backed by Proxycurl (LinkedIn-as-a-Service, ToS-compliant).
Result is cached on `subjects.linkedin_data` for 60 days so a second
click on the same lead is free.

Cost gate: Proxycurl bills ~$0.005-0.01 per Company Lookup. We surface
the cache hit/miss in the response so the operator can see when a paid
call happened.

Auth: same tenant-scoped JWT as the rest of /v1/leads. We re-check
that the lead belongs to the caller's tenant before unlocking the
expensive Proxycurl call.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services.linkedin_proxycurl import (
    LinkedInCompany,
    lookup_company_cached,
)

log = get_logger(__name__)
router = APIRouter()


class LinkedInEnrichResponse(BaseModel):
    lead_id: str
    subject_id: str
    found: bool
    cache_hit: bool
    linkedin_url: str | None = None
    name: str | None = None
    description: str | None = None
    employee_count_range: str | None = None
    industry: str | None = None
    founded_year: int | None = None
    hq_country: str | None = None
    hq_city: str | None = None
    website: str | None = None


def _to_response(
    *,
    lead_id: str,
    subject_id: str,
    record: LinkedInCompany,
    cache_hit: bool,
) -> LinkedInEnrichResponse:
    return LinkedInEnrichResponse(
        lead_id=lead_id,
        subject_id=subject_id,
        found=record.found,
        cache_hit=cache_hit,
        linkedin_url=record.linkedin_url,
        name=record.name,
        description=record.description,
        employee_count_range=record.employee_count_range,
        industry=record.industry,
        founded_year=record.founded_year,
        hq_country=record.hq_country,
        hq_city=record.hq_city,
        website=record.website,
    )


@router.post(
    "/leads/{lead_id}/enrich/linkedin", response_model=LinkedInEnrichResponse
)
async def enrich_linkedin(
    ctx: CurrentUser,
    lead_id: str,
    force_refresh: bool = Query(
        default=False,
        description="If true, ignore the cache and re-call Proxycurl (costs $0.005-0.01).",
    ),
) -> LinkedInEnrichResponse:
    """Resolve the lead's company on LinkedIn via Proxycurl.

    Cache: `subjects.linkedin_data` JSONB, TTL 60 days. Set
    ``force_refresh=true`` to ignore.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # 1) Load lead → subject + tenant guard.
    lead_res = (
        sb.table("leads")
        .select(
            "id, subject_id, tenant_id, "
            "subjects:subjects(id, business_name, sede_operativa_city)"
        )
        .eq("id", lead_id)
        .maybeSingle()
        .execute()
    )
    lead = lead_res.data
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")
    if lead.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="lead belongs to another tenant")

    subject = lead.get("subjects") or {}
    subject_id = subject.get("id") or lead.get("subject_id")
    if not subject_id:
        raise HTTPException(status_code=400, detail="lead has no subject")

    business_name = subject.get("business_name") or ""
    if not business_name:
        raise HTTPException(
            status_code=400, detail="subject has no business_name to resolve"
        )

    # Best-effort domain extraction from website to make Proxycurl precise.
    website = (lead.get("subjects") or {}).get("website")
    domain: str | None = None
    if isinstance(website, str) and "://" in website:
        try:
            domain = website.split("://", 1)[1].split("/", 1)[0]
        except Exception:
            domain = None

    location = subject.get("sede_operativa_city")

    record, cache_hit = await lookup_company_cached(
        sb=sb,
        subject_id=str(subject_id),
        company_name=business_name,
        company_domain=domain,
        location=location,
        force_refresh=force_refresh,
    )

    log.info(
        "linkedin_enrich.done",
        lead_id=lead_id,
        cache_hit=cache_hit,
        found=record.found,
    )
    return _to_response(
        lead_id=lead_id,
        subject_id=str(subject_id),
        record=record,
        cache_hit=cache_hit,
    )
