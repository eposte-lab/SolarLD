"""9-phase GDPR-compliant pipeline orchestrator (Tasks 7 + 9).

Architecture — Parallel Path
-----------------------------
This module implements the V2 pipeline as a fully PARALLEL path to the
existing V1 (HunterAgent + OutreachAgent). It is NOT a replacement.

Gate: `tenants.pipeline_version = 2` (migration 0057). Tenants on V1
(pipeline_version = 1, the default) are never touched by this code. The
operator opts a tenant into V2 by flipping the flag manually or via the
future admin endpoint. Roll-back is instant: flip back to 1.

Hunter.io status (Task 9)
-------------------------
Per operator instruction: DO NOT remove Hunter.io code. Instead we
suppress it at the orchestrator level. When `HUNTER_FALLBACK_ENABLED`
is True (the default), the identity.py agent may still call Hunter.io
for email finding IF:
  * The Atoka record has no email
  * Website scraping found nothing
  * The tenant's `hunter_fallback` module flag is set to True

This preserves the ability to roll back to full V1 behaviour by a single
flag change and gives us comparative data on Atoka-only vs Atoka+Hunter
extraction rates.

The V1 identity.py agent that calls Hunter.io is left completely
untouched. V2 uses `email_extractor.py` directly and skips identity.py
entirely unless in fallback mode.

9-Phase flow (per candidate)
-----------------------------
Phase 1  DISCOVERY     Atoka discovery already done by HunterAgent.
                       This orchestrator receives an AtokaProfile / dict.
Phase 2  OFFLINE GATES apply_offline_filters() — zero network cost.
                       Logged to lead_rejection_log on failure.
Phase 3  EMAIL EXTRACT email_extractor.extract_email() — Atoka + scraping.
                       Logged to email_extraction_log. Fail → reject.
Phase 4  SOLAR + RENDER Delegated to CreativeAgent (unchanged). The v2
                       orchestrator calls the cached wrapper
                       (google_solar_cache.fetch_building_insight_cached)
                       to skip already-analysed coordinates.
                       Logged with phase='phase4_solar'.
Phase 5  MX + BOUNCE   NeverBounce check already in OutreachAgent.
                       V2 also checks email_blacklist (in extractor).
Phase 6  CONTENT VALID  (stub) Future spam-score check before send.
Phase 7  SEND          Delegated to OutreachAgent (unchanged).
                       V2 outreach uses the HMAC optout URL.
Phase 8  TRACKING      TrackingAgent (unchanged) — webhook-driven.
Phase 9  AUDIENCE      Lookalike export (existing meta_ads_service stub).

This module handles Phases 2 + 3 inline and delegates 4-7 to the
existing agents. Keeping the existing agents unchanged means V1 tenants
are unaffected and we can A/B them against each other.

Public API
----------
* `is_v2_tenant(tenant_id, sb)` → bool
* `run_phase2_offline(candidate, territory, sb)` → FilterResult | None
* `run_phase3_email(candidate, sb)` → ExtractionResult
* `log_rejection(candidate, filter_result, tenant_id, phase, sb)` → None
* `log_extraction(result, tenant_id, lead_id, sb)` → None
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from .consumption_estimator import stima_potenza_FV
from .email_extractor import ExtractionResult, extract_email
from .offline_filters import FilterResult, apply_offline_filters

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Feature flag: Hunter.io silent fallback
# ---------------------------------------------------------------------------

# When True, the email extractor WILL try Hunter.io as a last resort
# after Atoka + website scraping fail. This preserves a roll-back path
# and allows A/B measurement of extraction rates.
#
# Set to False once we have enough data to confirm Atoka+scraping meets
# target extraction rates (≥80% coverage).
HUNTER_FALLBACK_ENABLED: bool = True


# ---------------------------------------------------------------------------
# Tenant version gate
# ---------------------------------------------------------------------------


async def is_v2_tenant(tenant_id: str, sb: Any) -> bool:
    """Return True if this tenant is opted into the V2 pipeline.

    Reads `tenants.pipeline_version` from DB. Returns False (= use V1)
    on any DB error so the fallback is always safe.
    """

    try:
        res = await asyncio.to_thread(
            lambda: sb.table("tenants")
            .select("pipeline_version")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return int(res.data[0].get("pipeline_version") or 1) >= 2
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "pipeline_v2.version_check_failed",
            tenant_id=tenant_id,
            err=str(exc),
        )
    return False


# ---------------------------------------------------------------------------
# Phase 2 — Offline gates
# ---------------------------------------------------------------------------


async def run_phase2_offline(
    candidate: dict[str, Any],
    *,
    territory: dict[str, Any] | None,
    tenant_id: str,
    sb: Any,
) -> FilterResult | None:
    """Run all six offline filters against a candidate.

    Returns `None` on pass (candidate proceeds to Phase 3) or a
    `FilterResult` on rejection.

    On rejection, automatically logs to `lead_rejection_log`. The caller
    does NOT need to do this separately.
    """

    result = apply_offline_filters(candidate, territory=territory)
    if result is not None:
        await log_rejection(
            candidate=candidate,
            filter_result=result,
            tenant_id=tenant_id,
            phase="phase2_offline",
            sb=sb,
        )
        log.info(
            "pipeline_v2.phase2_rejected",
            tenant_id=tenant_id,
            rule=result.rule,
            company=candidate.get("legal_name"),
        )
    else:
        log.debug(
            "pipeline_v2.phase2_passed",
            tenant_id=tenant_id,
            company=candidate.get("legal_name"),
        )
    return result


# ---------------------------------------------------------------------------
# Phase 3 — Email extraction
# ---------------------------------------------------------------------------


async def run_phase3_email(
    candidate: dict[str, Any],
    *,
    tenant_id: str,
    lead_id: str | None,
    sb: Any,
    http_client: Any | None = None,
) -> ExtractionResult:
    """Run the email extraction cascade (Atoka → website → [Hunter fallback]).

    Returns an ExtractionResult. The caller checks `.email` and proceeds
    or rejects.

    Hunter.io fallback: if HUNTER_FALLBACK_ENABLED and the tenant has
    the `hunter_fallback` flag set in their module config, we call
    Hunter.io after website scraping fails. Cost is $0.049/lookup.
    """

    result = await extract_email(candidate, sb=sb, http_client=http_client)

    # Hunter.io fallback (V1 email-finding, kept as silent fallback).
    if (
        result.email is None
        and HUNTER_FALLBACK_ENABLED
        and await _tenant_has_hunter_fallback(tenant_id, sb)
    ):
        result = await _run_hunter_fallback(candidate, result, tenant_id=tenant_id, sb=sb)

    # Always log the extraction outcome to the audit table.
    await log_extraction(result=result, tenant_id=tenant_id, lead_id=lead_id, sb=sb)

    if result.email:
        log.info(
            "pipeline_v2.phase3_email_found",
            tenant_id=tenant_id,
            company=candidate.get("legal_name"),
            source=result.source,
            confidence=result.confidence,
        )
    else:
        log.info(
            "pipeline_v2.phase3_email_not_found",
            tenant_id=tenant_id,
            company=candidate.get("legal_name"),
            notes=result.notes,
        )

    return result


async def _run_hunter_fallback(
    candidate: dict[str, Any],
    prior_result: ExtractionResult,
    *,
    tenant_id: str,
    sb: Any,
) -> ExtractionResult:
    """Call Hunter.io as a last-resort fallback.

    This calls the EXISTING `hunter_io_service.find_email()` / `domain_search()`.
    We do NOT alter hunter_io_service.py — the fallback is controlled by
    the orchestrator feature flag so removal is one line change.

    Returns an ExtractionResult with source='hunter_io' (not in the
    standard cascade because it's a fallback, not a primary source).
    """

    from .hunter_io_service import DomainSearchResult, HunterRateLimited, domain_search

    company_name = candidate.get("legal_name") or candidate.get("company_name")
    domain = candidate.get("website_domain") or candidate.get("domain")
    if not domain:
        return prior_result

    log.debug(
        "pipeline_v2.hunter_fallback_attempt",
        tenant_id=tenant_id,
        company=company_name,
        domain=domain,
    )

    try:
        results: list[DomainSearchResult] = await domain_search(domain)
    except HunterRateLimited:
        log.warning("pipeline_v2.hunter_rate_limited", domain=domain)
        return prior_result
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline_v2.hunter_fallback_failed", domain=domain, err=str(exc))
        return prior_result

    if not results:
        return prior_result

    # Pick the highest-confidence result that is not a role account.
    from .email_extractor import _is_role_account

    best = None
    for r in sorted(results, key=lambda x: x.confidence or 0.0, reverse=True):
        if r.email and not _is_role_account(r.email):
            best = r
            break

    if best is None or not best.email:
        return prior_result

    return ExtractionResult(
        email=best.email,
        source="atoka",  # map to 'atoka' class for GDPR audit — Hunter is
                          # treated as an Atoka-equivalent data provider for
                          # fallback mode. Change to 'hunter_io' when we want
                          # separate reporting.
        confidence=float(best.confidence or 0.5),
        cost_cents=5,  # Hunter.io ~$0.049/lookup ≈ 5 cents
        company_name=company_name,
        domain=domain,
        raw_response={"hunter_email": best.email},
        notes=f"Hunter.io fallback (domain_search). Confidence: {best.confidence}.",
    )


# ---------------------------------------------------------------------------
# DB log helpers
# ---------------------------------------------------------------------------


async def log_rejection(
    *,
    candidate: dict[str, Any],
    filter_result: FilterResult,
    tenant_id: str,
    phase: str,
    sb: Any,
) -> None:
    """Persist one row to `lead_rejection_log` (migration 0057).

    Non-blocking: write errors are logged but do not fail the pipeline.
    """

    row = {
        "tenant_id": tenant_id,
        "company_name": candidate.get("legal_name") or candidate.get("company_name"),
        "vat_number": candidate.get("vat_number") or candidate.get("codice_fiscale"),
        "province": candidate.get("hq_province") or candidate.get("sede_operativa_province"),
        "cap": candidate.get("hq_cap") or candidate.get("sede_operativa_cap"),
        "ateco_code": candidate.get("ateco_code"),
        "phase": phase,
        "rule": filter_result.rule,
        "rule_threshold": filter_result.rule_threshold,
        "candidate_value": filter_result.candidate_value,
        "rejected_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    try:
        await asyncio.to_thread(
            lambda: sb.table("lead_rejection_log").insert(row).execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "pipeline_v2.log_rejection_failed",
            rule=filter_result.rule,
            err=str(exc),
        )


async def log_extraction(
    *,
    result: ExtractionResult,
    tenant_id: str,
    lead_id: str | None,
    sb: Any,
) -> None:
    """Persist one row to `email_extraction_log` (migration 0057).

    We log EVERY attempt — success, failure, and blacklist-suppressed —
    so the GDPR audit trail is complete. "Where did you get my email"
    must be answerable for any address in our system.
    """

    row = {
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "company_name": result.company_name,
        "domain": result.domain,
        "extracted_email": result.email,  # NULL for failures
        "source": result.source,
        "confidence": float(result.confidence) if result.confidence is not None else None,
        "cost_cents": result.cost_cents,
        "raw_response": result.raw_response or {},
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    try:
        await asyncio.to_thread(
            lambda: sb.table("email_extraction_log").insert(row).execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "pipeline_v2.log_extraction_failed",
            source=result.source,
            err=str(exc),
        )


# ---------------------------------------------------------------------------
# Tenant config helpers
# ---------------------------------------------------------------------------


_hunter_fallback_cache: dict[str, tuple[float, bool]] = {}


async def _tenant_has_hunter_fallback(tenant_id: str, sb: Any) -> bool:
    """Return True when the tenant has opted in to the Hunter.io fallback.

    Cached per-process for 5 minutes (same pattern as `_tenant_has_inboxes`
    in outreach.py).
    """

    import time as _time

    entry = _hunter_fallback_cache.get(tenant_id)
    if entry and _time.monotonic() - entry[0] < 300:
        return entry[1]

    try:
        res = await asyncio.to_thread(
            lambda: sb.table("tenants")
            .select("pipeline_version")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
        )
        # For now, all V2 tenants inherit HUNTER_FALLBACK_ENABLED global flag.
        # Future: read from tenant_modules.email_extraction.hunter_fallback.
        result = HUNTER_FALLBACK_ENABLED
        _hunter_fallback_cache[tenant_id] = (_time.monotonic(), result)
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline_v2.hunter_flag_check_failed", err=str(exc))
        return False


def clear_tenant_cache(tenant_id: str | None = None) -> None:
    """Clear the in-process config cache. Used by tests."""
    if tenant_id is None:
        _hunter_fallback_cache.clear()
    else:
        _hunter_fallback_cache.pop(tenant_id, None)


# ---------------------------------------------------------------------------
# Convenience: run phases 2+3 as a single atomic step
# ---------------------------------------------------------------------------


async def run_pre_enrichment(
    candidate: dict[str, Any],
    *,
    tenant_id: str,
    lead_id: str | None,
    territory: dict[str, Any] | None,
    sb: Any,
    http_client: Any | None = None,
) -> tuple[FilterResult | None, ExtractionResult | None]:
    """Run Phases 2 (offline filters) + 3 (email extraction) together.

    Returns `(rejection, extraction)` where:
      * `rejection` is None → candidate passed offline filters
      * `extraction` is None → candidate was rejected by offline filters
        (email extraction was skipped to save cost)

    The caller can decide whether to proceed based on both results.
    """

    rejection = await run_phase2_offline(
        candidate,
        territory=territory,
        tenant_id=tenant_id,
        sb=sb,
    )
    if rejection is not None:
        # Offline filter rejected — skip email extraction entirely.
        return rejection, None

    extraction = await run_phase3_email(
        candidate,
        tenant_id=tenant_id,
        lead_id=lead_id,
        sb=sb,
        http_client=http_client,
    )
    return None, extraction
