"""Email Extraction Agent — Phase 2 + 3 of the V2 pipeline.

This agent is the integration point between the Hunter funnel (L4 solar gate)
and the scoring/outreach pipeline. It is enqueued by level4_solar_gate.py for
every accepted subject instead of going directly to scoring_task.

Responsibilities
----------------
1. **Pilot gate**: When a tenant does NOT have `pipeline_v2_pilot=true`, the
   agent forwards directly to scoring_task unchanged — preserving legacy
   behaviour for non-pilot tenants during the gradual rollout.

2. **Phase 2 — Offline filters**: Runs the six zero-cost offline gates
   (affidabilità, trend, proprietà, anti-uffici, consumi, sede_operativa)
   against the candidate dict built in L4. Rejections are persisted to
   `lead_rejection_log` by the orchestrator.

3. **Phase 3 — Email extraction**: Runs the Atoka-first cascade (Atoka direct
   → website scraping → optional Hunter.io fallback). The outcome — success
   OR failure — is persisted to `email_extraction_log` for GDPR audit.
   A found email is written to `subjects.decision_maker_email`.

4. **Scoring enqueue**: Regardless of whether email was found (OutreachAgent
   handles the email-absent path), a `scoring_task` is enqueued for the
   subject so leads still enter the pipeline. The only case where scoring is
   NOT enqueued is Phase 2 rejection (candidate truly out of scope).

Pilot flag
----------
Set `tenants.pipeline_v2_pilot = true` for a specific tenant to enable V2.
After a 48-hour monitoring window, run:

    UPDATE tenants SET pipeline_v2_pilot = true;   -- global promotion

to promote all tenants to V2. Flip back to false to instant-rollback.

Candidate dict schema
---------------------
Built by level4_solar_gate._build_candidate_dict() from ScoredCandidate.
Keys used by this agent:

    legal_name           str
    vat_number           str | None
    ateco_code           str | None
    employees            int | None
    yearly_revenue_cents int | None
    email                str | None   — Atoka direct DM email
    website_domain       str | None   — for website scraping
    hq_province          str | None   — for sede_operativa filter
    hq_cap               str | None
    legal_status         str | None   — for affidabilità filter
    revenue_history_eur  list[float] | None
    building_ownership   str | None
    decision_maker_name  str | None
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from pydantic import BaseModel

from ..core.supabase_client import get_service_client
from ..services.pipeline_v2_orchestrator import run_pre_enrichment
from .base import AgentBase

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Pilot flag cache — avoid a DB query per candidate during high-volume scans
# ---------------------------------------------------------------------------

_pilot_cache: dict[str, tuple[float, bool]] = {}
_PILOT_CACHE_TTL_S: float = 300.0  # 5 minutes


async def _is_pilot_tenant(tenant_id: str, sb: Any) -> bool:
    """Return True when the tenant has pipeline_v2_pilot enabled.

    Cached per-process for 5 minutes so high-frequency scans don't hammer
    the DB. Cache is invalidated automatically on next TTL expiry.
    """
    import time as _t

    entry = _pilot_cache.get(tenant_id)
    if entry and _t.monotonic() - entry[0] < _PILOT_CACHE_TTL_S:
        return entry[1]

    try:
        res = await asyncio.to_thread(
            lambda: sb.table("tenants")
            .select("pipeline_v2_pilot")
            .eq("id", tenant_id)
            .limit(1)
            .execute()
        )
        result: bool = bool((res.data or [{}])[0].get("pipeline_v2_pilot", False))
    except Exception as exc:  # noqa: BLE001
        log.warning("email_extraction.pilot_check_failed", tenant_id=tenant_id, err=str(exc))
        result = False

    _pilot_cache[tenant_id] = (_t.monotonic(), result)
    return result


# ---------------------------------------------------------------------------
# Input / Output models
# ---------------------------------------------------------------------------


class EmailExtractionInput(BaseModel):
    tenant_id: str
    subject_id: str
    roof_id: str
    territory_id: str
    # Flat candidate dict — built by level4_solar_gate._build_candidate_dict()
    # Contains the fields that offline_filters + email_extractor consume.
    candidate: dict[str, Any]
    # Raw territory dict for the sede_operativa offline filter.
    # Keys: "provinces" list[str] | None, "caps" list[str] | None.
    # When None or empty, the sede_operativa filter is a no-op (permissive).
    territory: dict[str, Any] | None = None


class EmailExtractionOutput(BaseModel):
    subject_id: str
    # Phase 2 result
    phase2_passed: bool = True
    rejection_reason: str | None = None
    # Phase 3 result
    email_found: bool = False
    email_source: str | None = None
    email_confidence: float | None = None
    # Scoring
    scoring_enqueued: bool = False
    # Total cost attributed to Phase 3 extraction
    extraction_cost_cents: int = 0


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class EmailExtractionAgent(AgentBase[EmailExtractionInput, EmailExtractionOutput]):
    name = "agent.email_extraction"

    async def execute(self, payload: EmailExtractionInput) -> EmailExtractionOutput:
        sb = get_service_client()
        out = EmailExtractionOutput(subject_id=payload.subject_id)

        # ------------------------------------------------------------------
        # Pilot gate — non-pilot tenants skip V2 and go straight to scoring.
        # ------------------------------------------------------------------
        is_pilot = await _is_pilot_tenant(payload.tenant_id, sb)
        if not is_pilot:
            await self._enqueue_scoring(payload)
            out.scoring_enqueued = True
            log.debug(
                "email_extraction.bypassed_non_pilot",
                tenant_id=payload.tenant_id,
                subject_id=payload.subject_id,
            )
            return out

        log.info(
            "email_extraction.started",
            tenant_id=payload.tenant_id,
            subject_id=payload.subject_id,
            candidate=payload.candidate.get("legal_name"),
        )

        # ------------------------------------------------------------------
        # Phase 2 + 3 via pipeline_v2_orchestrator.run_pre_enrichment().
        # This function runs both phases atomically and writes the GDPR
        # audit rows (lead_rejection_log, email_extraction_log).
        # ------------------------------------------------------------------
        rejection, extraction = await run_pre_enrichment(
            payload.candidate,
            tenant_id=payload.tenant_id,
            lead_id=None,  # lead row doesn't exist yet (created by ScoringAgent)
            territory=payload.territory,
            sb=sb,
        )

        if rejection is not None:
            # Phase 2 rejected — mark subject, do NOT proceed to scoring.
            out.phase2_passed = False
            out.rejection_reason = rejection.rule
            await self._mark_subject_rejected(payload.subject_id, rejection.rule, sb)
            log.info(
                "email_extraction.phase2_rejected",
                tenant_id=payload.tenant_id,
                subject_id=payload.subject_id,
                rule=rejection.rule,
            )
            await self._emit_event(
                event_type="subject.offline_rejected",
                payload={
                    "subject_id": payload.subject_id,
                    "rule": rejection.rule,
                    "reason": rejection.reason,
                },
                tenant_id=payload.tenant_id,
            )
            return out

        # Phase 2 passed — process Phase 3 result.
        out.phase2_passed = True

        if extraction is not None:
            out.extraction_cost_cents = extraction.cost_cents

            if extraction.email:
                out.email_found = True
                out.email_source = extraction.source
                out.email_confidence = extraction.confidence
                # Persist email to subject row. decision_maker_email_verified stays
                # False — NeverBounce runs inside OutreachAgent at send time.
                await self._write_email_to_subject(
                    payload.subject_id,
                    extraction.email,
                    sb,
                )
                log.info(
                    "email_extraction.email_found",
                    tenant_id=payload.tenant_id,
                    subject_id=payload.subject_id,
                    source=extraction.source,
                    confidence=extraction.confidence,
                )
            else:
                log.info(
                    "email_extraction.email_not_found",
                    tenant_id=payload.tenant_id,
                    subject_id=payload.subject_id,
                    notes=extraction.notes,
                )

        # Always enqueue scoring — even without email (OutreachAgent falls
        # back to postal channel or skips gracefully if no channel is viable).
        await self._enqueue_scoring(payload)
        out.scoring_enqueued = True

        await self._emit_event(
            event_type="subject.email_extraction_complete",
            payload={
                "subject_id": payload.subject_id,
                "email_found": out.email_found,
                "source": out.email_source,
                "cost_cents": out.extraction_cost_cents,
            },
            tenant_id=payload.tenant_id,
        )
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _enqueue_scoring(self, payload: EmailExtractionInput) -> None:
        """Enqueue scoring_task. Idempotent via deterministic job_id."""
        from ..core.queue import enqueue

        job_id = f"scoring:{payload.tenant_id}:{payload.roof_id}:{payload.subject_id}"
        try:
            await enqueue(
                "scoring_task",
                {
                    "tenant_id": payload.tenant_id,
                    "roof_id": payload.roof_id,
                    "subject_id": payload.subject_id,
                },
                job_id=job_id,
            )
        except Exception as exc:  # noqa: BLE001
            # Scoring enqueue failure must not block the agent — the manual
            # POST /v1/leads/score-pending-subjects endpoint is the fallback.
            log.warning(
                "email_extraction.scoring_enqueue_failed",
                subject_id=payload.subject_id,
                err=str(exc),
            )

    async def _write_email_to_subject(
        self, subject_id: str, email: str, sb: Any
    ) -> None:
        try:
            await asyncio.to_thread(
                lambda: sb.table("subjects")
                .update(
                    {
                        "decision_maker_email": email,
                        "decision_maker_email_verified": False,
                    }
                )
                .eq("id", subject_id)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "email_extraction.subject_email_write_failed",
                subject_id=subject_id,
                err=str(exc),
            )

    async def _mark_subject_rejected(
        self, subject_id: str, rule: str, sb: Any
    ) -> None:
        """Tag the subject so the dashboard waterfall shows Phase 2 rejects."""
        try:
            await asyncio.to_thread(
                lambda: sb.table("subjects")
                .update({"extraction_status": f"rejected_offline:{rule}"})
                .eq("id", subject_id)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log.debug(
                "email_extraction.subject_reject_tag_failed",
                subject_id=subject_id,
                err=str(exc),
            )


# ---------------------------------------------------------------------------
# Utility: build the candidate dict from a ScoredCandidate at L4 time.
# Called by level4_solar_gate._build_candidate_dict() to keep the payload
# construction co-located with the agent that consumes it.
# ---------------------------------------------------------------------------


def build_candidate_dict_from_profile(
    profile: Any,  # AtokaProfile — avoiding circular import
    enrichment: Any | None,  # EnrichmentSignals | None
) -> dict[str, Any]:
    """Flatten AtokaProfile + EnrichmentSignals into the candidate dict
    that offline_filters and email_extractor expect.

    This function lives here (not in level4_solar_gate.py) so the V2
    contract is documented alongside the agent that owns it.
    """
    raw: dict[str, Any] = profile.raw if profile.raw else {}

    # Atoka DM email: not stored as a typed attribute on AtokaProfile;
    # extract from the raw Atoka API response.
    raw_contacts: list[dict] = raw.get("decisionMakers") or []
    dm_email: str | None = raw_contacts[0].get("email") if raw_contacts else None

    # Revenue history: Atoka sometimes provides 3-year series under
    # "revenueHistory" or "revenues_history" in the raw response.
    revenue_history: list[float] | None = None
    history_raw = raw.get("revenueHistory") or raw.get("revenues_history")
    if isinstance(history_raw, list) and len(history_raw) >= 3:
        try:
            revenue_history = [float(x) for x in history_raw[-3:]]
        except (TypeError, ValueError):
            revenue_history = None

    return {
        # Core identity
        "legal_name": profile.legal_name,
        "vat_number": profile.vat_number,
        "ateco_code": profile.ateco_code,
        "employees": profile.employees,
        "yearly_revenue_cents": profile.yearly_revenue_cents,
        # Email extraction fields
        "email": dm_email,
        "website_domain": (
            (enrichment.website if enrichment and enrichment.website else None)
            or profile.website_domain
        ),
        # Geo for sede_operativa filter
        "hq_province": profile.hq_province,
        "hq_cap": profile.hq_cap,
        "hq_address": profile.hq_address,
        # Offline filter fields from Atoka raw
        "legal_status": (
            raw.get("legalStatus") or raw.get("stato") or raw.get("legal_status")
        ),
        "stato_attivita": raw.get("statoAttivita") or raw.get("stato_attivita"),
        "revenue_history_eur": revenue_history,
        "building_ownership": (
            raw.get("buildingOwnership") or raw.get("building_ownership")
        ),
        # Display / GDPR audit
        "decision_maker_name": profile.decision_maker_name,
        "decision_maker_role": profile.decision_maker_role,
    }
