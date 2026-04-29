"""Scoring Agent — computes 0-100 score + tier (hot/warm/cold/rejected).

Pipeline (Sprint 3):

    (tenant_id, roof_id, subject_id)
        ↓
    load roof, subject, tenant.settings (for HQ lat/lng)
        ↓
    load active scoring_weights row (jsonb weights)
        ↓
    if subject.ateco_code → lookup ateco_consumption_profiles
    incentives = regional_incentives WHERE region=subject.region
                 AND active=true
        ↓
    breakdown = {
        technical    = technical_score(roof),
        consumption  = consumption_score(subject, roof, ateco),
        incentives   = incentives_score(incentives, subject.type),
        solvency     = solvency_score(subject),
        distance     = distance_score(roof.lat/lng, tenant.settings.hq),
    }
    score = combine_breakdown(breakdown, weights)   # 0..100
    tier  = tier_for(score)                         # hot|warm|cold|rejected
        ↓
    upsert leads(tenant_id, roof_id, subject_id)
        - public_slug: url-safe 16-byte token
        - score_breakdown: jsonb
    update roof.status = 'scored' (unless 'rejected' tier → stay as-is
    and mark lead pipeline_status='new' so it doesn't clutter dashboards
    — Creative Agent filters out the REJECTED tier).
        ↓
    emit lead.scored event

Degraded paths:
  - Missing tenant HQ → distance defaults to 50 (see distance.py).
  - Missing ATECO row → consumption falls back on employee count or roof
    area proxy (see consumption.py).
  - Missing `regional_incentives` rows (tenant in a region we haven't
    scraped yet) → incentives scores 20, keeping leads alive until the
    scraper catches up.
  - All scoring_weights inactive (shouldn't happen, migration seeds V1)
    → we use the PRD default via ScoringWeights.from_jsonb(None).
"""

from __future__ import annotations

import secrets
from typing import Any

from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..models.enums import LeadScoreTier, RoofStatus
from ..services.scoring import (
    ScoringBreakdown,
    ScoringWeights,
    combine_breakdown,
    consumption_score,
    distance_score,
    incentives_score,
    province_to_region,
    solvency_score,
    technical_score,
    tier_for,
)
from ..services.tenant_config_service import get_for_tenant
from .base import AgentBase

log = get_logger(__name__)


class ScoringInput(BaseModel):
    tenant_id: str
    roof_id: str
    subject_id: str


class ScoringOutput(BaseModel):
    lead_id: str | None = None
    score: int = Field(default=0, ge=0, le=100)
    tier: LeadScoreTier = LeadScoreTier.REJECTED
    breakdown: dict[str, int] = Field(default_factory=dict)
    weights_version: int | None = None


class ScoringAgent(AgentBase[ScoringInput, ScoringOutput]):
    name = "agent.scoring"

    async def execute(self, payload: ScoringInput) -> ScoringOutput:
        sb = get_service_client()

        # 1) Load the entities we need
        roof = _load_single(sb, "roofs", payload.roof_id, payload.tenant_id)
        subject = _load_single(sb, "subjects", payload.subject_id, payload.tenant_id)
        if not roof:
            raise ValueError(f"roof {payload.roof_id} not found")
        if not subject:
            raise ValueError(f"subject {payload.subject_id} not found")
        if subject.get("roof_id") != payload.roof_id:
            raise ValueError(
                f"subject {payload.subject_id} does not belong to roof {payload.roof_id}"
            )

        tenant = (
            sb.table("tenants")
            .select(
                "id, settings, daily_target_send_cap, daily_send_cap_min, "
                "daily_send_cap_max, warehouse_buffer_days, lead_expiration_days, "
                "atoka_survival_target"
            )
            .eq("id", payload.tenant_id)
            .single()
            .execute()
        )
        tenant_row = tenant.data or {}
        hq_lat, hq_lng = _extract_hq(tenant_row.get("settings"))

        # 2) Active scoring weights (single row where active=true)
        weights_res = (
            sb.table("scoring_weights")
            .select("version, weights")
            .eq("active", True)
            .limit(1)
            .execute()
        )
        weights_row = (weights_res.data or [None])[0]
        weights = ScoringWeights.from_jsonb(
            weights_row["weights"] if weights_row else None
        )
        weights_version = weights_row["version"] if weights_row else None

        # 3) ATECO lookup (B2B only)
        ateco_profile: dict[str, Any] | None = None
        if subject.get("type") == "b2b" and subject.get("ateco_code"):
            ateco_res = (
                sb.table("ateco_consumption_profiles")
                .select("*")
                .eq("ateco_code", subject["ateco_code"])
                .limit(1)
                .execute()
            )
            ateco_profile = (ateco_res.data or [None])[0]

        # 4) Regional incentives (active, matching the subject's region)
        subject_province = subject.get("postal_province") or roof.get("provincia")
        region = province_to_region(subject_province)
        incentives: list[dict[str, Any]] = []
        if region:
            inc_res = (
                sb.table("regional_incentives")
                .select("id, region, target, deadline, active")
                .eq("region", region)
                .eq("active", True)
                .execute()
            )
            incentives = inc_res.data or []

        # 5) Compute each subscore
        breakdown = ScoringBreakdown(
            technical=technical_score(roof),
            consumption=consumption_score(subject, roof, ateco_profile),
            incentives=incentives_score(
                incentives, subject.get("type") or "unknown"
            ),
            solvency=solvency_score(subject),
            distance=distance_score(
                roof.get("lat"), roof.get("lng"), hq_lat, hq_lng
            ),
        )
        final_score = combine_breakdown(breakdown, weights)

        # Sprint 9: per-tenant floor from the onboarding wizard. A lead
        # below `scoring_threshold` collapses to REJECTED even if it
        # would otherwise land in COLD/WARM, so OutreachAgent skips it.
        tenant_config = await get_for_tenant(payload.tenant_id)
        final_tier = tier_for(final_score, tenant_config.scoring_threshold)

        # 6) Upsert lead row
        # Sprint 11 — qualified leads enter the warehouse (`ready_to_send`)
        # rather than going straight to rendering. The daily orchestrator
        # picks them in FIFO order and only then triggers Solar+Kling.
        # Rejected-tier leads are inserted in their pre-existing `new`
        # state so the dashboard can still surface them; OutreachAgent
        # skips them on tier.
        from datetime import datetime, timedelta, timezone as _tz
        from ..services.warehouse_policy import policy_for as _policy_for

        is_qualified = final_tier != LeadScoreTier.REJECTED
        warehouse_now = datetime.now(_tz.utc)
        warehouse_policy = _policy_for(tenant_row)
        warehouse_expires = (
            warehouse_now + timedelta(days=warehouse_policy.lead_expiration_days)
            if is_qualified
            else None
        )
        warehouse_status = "ready_to_send" if is_qualified else "new"

        existing_lead = (
            sb.table("leads")
            .select("id, public_slug, pipeline_status")
            .eq("tenant_id", payload.tenant_id)
            .eq("roof_id", payload.roof_id)
            .eq("subject_id", payload.subject_id)
            .limit(1)
            .execute()
        )
        if existing_lead.data:
            lead_id = existing_lead.data[0]["id"]
            existing_status = existing_lead.data[0].get("pipeline_status")
            update_payload: dict[str, Any] = {
                "score": final_score,
                "score_breakdown": breakdown.to_dict(),
                "score_tier": final_tier.value,
                "last_status_transition_at": warehouse_now.isoformat(),
            }
            # Only push back into the warehouse if the lead hasn't already
            # progressed past it. A lead that's `picked` / `rendering` /
            # `sent` should keep its forward state — we just refreshed
            # the score for analytics, not to re-stage the same email.
            if (
                is_qualified
                and existing_status in {None, "new", "discovered", "enriched", "scored", "qualified", "expired"}
            ):
                update_payload["pipeline_status"] = "ready_to_send"
                update_payload["enqueued_to_warehouse_at"] = warehouse_now.isoformat()
                if warehouse_expires is not None:
                    update_payload["expires_at"] = warehouse_expires.isoformat()
            sb.table("leads").update(update_payload).eq("id", lead_id).execute()
        else:
            public_slug = secrets.token_urlsafe(16)
            insert_payload: dict[str, Any] = {
                "tenant_id": payload.tenant_id,
                "roof_id": payload.roof_id,
                "subject_id": payload.subject_id,
                "public_slug": public_slug,
                "score": final_score,
                "score_breakdown": breakdown.to_dict(),
                "score_tier": final_tier.value,
                "pipeline_status": warehouse_status,
                "last_status_transition_at": warehouse_now.isoformat(),
            }
            if is_qualified:
                insert_payload["enqueued_to_warehouse_at"] = warehouse_now.isoformat()
                if warehouse_expires is not None:
                    insert_payload["expires_at"] = warehouse_expires.isoformat()
            insert_res = sb.table("leads").insert(insert_payload).execute()
            lead_id = (insert_res.data or [{}])[0].get("id")

        # 7) Transition roof.status → scored (unless already downstream)
        current_status = roof.get("status")
        if current_status in (RoofStatus.IDENTIFIED.value, RoofStatus.DISCOVERED.value):
            sb.table("roofs").update(
                {"status": RoofStatus.SCORED.value}
            ).eq("id", payload.roof_id).execute()

        out = ScoringOutput(
            lead_id=lead_id,
            score=final_score,
            tier=final_tier,
            breakdown=breakdown.to_dict(),
            weights_version=weights_version,
        )

        await self._emit_event(
            event_type="lead.scored",
            payload=out.model_dump(),
            tenant_id=payload.tenant_id,
            lead_id=lead_id,
        )
        return out

    # Backwards-compat alias for older callers; new code should use
    # `services.scoring.tier_for` directly.
    @staticmethod
    def tier_for(score: int, min_threshold: int | None = None) -> LeadScoreTier:
        return tier_for(score, min_threshold)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_single(
    sb: Any, table: str, row_id: str, tenant_id: str
) -> dict[str, Any] | None:
    res = (
        sb.table(table)
        .select("*")
        .eq("id", row_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    data = res.data or []
    return data[0] if data else None


def _extract_hq(settings: Any) -> tuple[float | None, float | None]:
    """Pull HQ coords out of the tenants.settings JSONB, if present."""
    if not isinstance(settings, dict):
        return None, None
    lat = settings.get("hq_lat")
    lng = settings.get("hq_lng")
    try:
        lat_f = float(lat) if lat is not None else None
        lng_f = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        return None, None
    return lat_f, lng_f
