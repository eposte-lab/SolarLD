"""L2 — Multi-source scraping (FLUSSO 1 v3).

For each L1 candidate, this agent runs the three-source public scrape
(website → Pagine Bianche → OpenCorporates) in parallel, normalises the
result into ``ScrapedSignals`` + ``ContactExtraction``, and persists
both as JSONB on the candidate row.

Side effect: every contact extracted (email / phone / PEC) is also
written to ``contact_extraction_log`` so the GDPR export endpoint can
return the source URL on request.

Cost: zero (all sources free / public).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from ...services.web_scraper import (
    CombinedScrape,
    extract_best_email,
    scrape_all_for_candidate,
)
from .types_v3 import (
    ContactExtraction,
    FunnelV3Context,
    PlaceCandidateRecord,
    ScrapedCandidate,
    ScrapedSignals,
)

log = get_logger(__name__)


# Cap parallel scrapes per scan to avoid hammering the same target sites
# in tight loops. 10 concurrent scrapes is gentle for a 600-candidate
# scan and keeps total wall-clock under 5 min.
_MAX_PARALLEL_SCRAPES = 10


async def _scrape_one(
    cand: PlaceCandidateRecord,
    *,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> tuple[PlaceCandidateRecord, CombinedScrape]:
    """Bounded-concurrency wrapper around ``scrape_all_for_candidate``."""
    async with semaphore:
        # Crude city extraction from formatted_address. The Italian Places
        # `formattedAddress` typically ends with "<CAP> <city> <prov>, IT".
        city = None
        if cand.formatted_address:
            parts = [p.strip() for p in cand.formatted_address.split(",")]
            if parts:
                # Take the second-to-last part (the city + province bit)
                # if "IT" / "Italia" is the last; else the last part.
                last = parts[-1].lower()
                if last in {"it", "italia", "italy"} and len(parts) >= 2:
                    city_block = parts[-2].split()
                    if city_block:
                        city = " ".join(
                            t for t in city_block if not t.isdigit() and len(t) > 2
                        ).strip() or None
                else:
                    city = parts[-1] or None
        result = await scrape_all_for_candidate(
            website=cand.website,
            business_name=cand.display_name or "",
            city=city,
            client=client,
        )
        return cand, result


def _project_to_signals_and_contact(
    cand: PlaceCandidateRecord, combined: CombinedScrape
) -> tuple[ScrapedSignals, ContactExtraction]:
    """Translate the multi-source scrape result into our typed model."""
    sources: list[str] = []

    site = combined.site
    pb = combined.pb
    oc = combined.oc

    if site.url and not site.error:
        sources.append("website")
    if pb.found:
        sources.append("pagine_bianche")
    if oc.found:
        sources.append("opencorporates")

    signals = ScrapedSignals(
        website_emails=list(site.emails),
        website_phone=site.phone,
        website_pec=site.pec,
        website_address=site.address,
        website_decision_maker=site.decision_maker,
        pagine_bianche_phone=pb.phone,
        pagine_bianche_address=pb.address,
        pagine_bianche_category=pb.category,
        opencorporates_vat=oc.vat,
        opencorporates_legal_name=oc.legal_name,
        opencorporates_founding_date=oc.founding_date,
        opencorporates_status=oc.status,
        opencorporates_legal_form=oc.legal_form,
        site_signals=[],  # populated below from display_name/website
        sources_consulted=sources,
        scrape_ok=bool(site.emails or pb.found or oc.found),
        scrape_errors=[site.error] if site.error else [],
    )

    best = extract_best_email(site.emails)
    contact = ContactExtraction(
        best_email=best.value if best else None,
        best_email_confidence=best.confidence if best else None,
        best_email_type=best.type if best else None,
        # Phone preference: website first, Pagine Bianche fallback, then
        # the Place phone we already have on the L1 record.
        best_phone=site.phone or pb.phone or cand.phone,
        pec=site.pec,
        decision_maker_name=site.decision_maker,
    )
    return signals, contact


async def _log_contact_extractions(
    sb: Any,
    *,
    tenant_id: str,
    candidate_id: str,
    cand: PlaceCandidateRecord,
    site_url: str,
    signals: ScrapedSignals,
) -> None:
    """Insert one row per public contact into ``contact_extraction_log``.

    The table is created in migration 0104 (Sprint 4.1). When the
    migration hasn't shipped yet the call silently no-ops because we
    catch the PostgREST 404 — keeps the agent resilient during the
    rolling deploy.
    """
    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    for email in signals.website_emails:
        rows.append(
            {
                "tenant_id": tenant_id,
                "candidate_id": candidate_id,
                "contact_value": email,
                "contact_type": "email",
                "source_url": site_url or None,
                "source_type": "website",
                "extraction_method": "regex_html",
                "confidence": "alta"
                if any(role in email.lower() for role in ("direzione", "amministrazione"))
                else "media",
                "extracted_at": now,
            }
        )
    if signals.website_phone:
        rows.append(
            {
                "tenant_id": tenant_id,
                "candidate_id": candidate_id,
                "contact_value": signals.website_phone,
                "contact_type": "phone",
                "source_url": site_url or None,
                "source_type": "website",
                "extraction_method": "regex_html",
                "extracted_at": now,
            }
        )
    if signals.pagine_bianche_phone:
        rows.append(
            {
                "tenant_id": tenant_id,
                "candidate_id": candidate_id,
                "contact_value": signals.pagine_bianche_phone,
                "contact_type": "phone",
                "source_url": "https://www.paginebianche.it/",
                "source_type": "pagine_bianche",
                "extraction_method": "html_scrape",
                "extracted_at": now,
            }
        )
    if not rows:
        return

    try:
        sb.table("contact_extraction_log").insert(rows).execute()
    except Exception as exc:  # noqa: BLE001 — log only when target table is ready
        log.debug("level2_scraping.audit_log_skipped", err=type(exc).__name__)


async def run_level2_scraping(
    ctx: FunnelV3Context,
    candidates: list[PlaceCandidateRecord],
) -> list[ScrapedCandidate]:
    """Scrape every candidate in parallel (bounded), persist results."""
    if not candidates:
        return []

    sb = get_service_client()
    semaphore = asyncio.Semaphore(_MAX_PARALLEL_SCRAPES)

    async with httpx.AsyncClient(
        timeout=10.0,
        headers={"User-Agent": "solarlead-scraper/1.0 (+https://solarlead.it)"},
    ) as client:
        tasks = [_scrape_one(c, client=client, semaphore=semaphore) for c in candidates]
        results: list[Any] = await asyncio.gather(*tasks, return_exceptions=True)

    out: list[ScrapedCandidate] = []
    bulk_updates: list[dict[str, Any]] = []

    for item in results:
        if isinstance(item, BaseException):
            log.warning("level2_scraping.task_error", err=type(item).__name__)
            continue
        cand, combined = item  # type: ignore[misc]
        signals, contact = _project_to_signals_and_contact(cand, combined)

        out.append(ScrapedCandidate(record=cand, scraped=signals, contact=contact))

        bulk_updates.append(
            {
                "id": str(cand.candidate_id),
                "scraped_data": signals.to_jsonb(),
                "contact_extraction": contact.to_jsonb(),
                "stage": 2,
            }
        )

        await _log_contact_extractions(
            sb,
            tenant_id=ctx.tenant_id,
            candidate_id=str(cand.candidate_id),
            cand=cand,
            site_url=combined.site.url,
            signals=signals,
        )

    # Bulk update of scan_candidates rows. Upsert keeps existing columns
    # untouched and just patches scraped_data + contact_extraction.
    if bulk_updates:
        try:
            sb.table("scan_candidates").upsert(
                bulk_updates, on_conflict="id"
            ).execute()
        except Exception as exc:  # noqa: BLE001
            # When migration 0100 hasn't shipped these columns will not exist
            # yet — log a warning but don't crash the scan.
            log.warning(
                "level2_scraping.persist_failed",
                err=type(exc).__name__,
                msg=str(exc)[:200],
            )

    log.info(
        "level2_scraping.done",
        tenant_id=ctx.tenant_id,
        scanned=len(candidates),
        produced=len(out),
        with_email=sum(1 for r in out if r.contact.best_email),
        with_phone=sum(1 for r in out if r.contact.best_phone),
    )
    return out
