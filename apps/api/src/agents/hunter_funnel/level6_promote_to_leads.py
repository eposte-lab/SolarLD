"""FLUSSO 1 v3 — L6: Promote scan_candidates to subjects + leads.

Reads ``scan_candidates`` rows where:
  * ``funnel_version = 3``
  * ``recommended_for_rendering = true``
  * ``stage = 5``
  * a ``roof_id`` was assigned at L4 (Solar accepted)

For each candidate:
  1. Builds a ``subject`` row from the scraped business signals + Places
     metadata (no Atoka, no VAT — pii_hash uses business_name|place_id).
  2. Creates a ``lead`` row linking the subject + roof, with the L5
     overall score and a deterministic public_slug.
  3. Idempotency: if the subject already exists for (tenant_id, roof_id)
     we reuse it; if a lead already exists for that subject we skip.

Downstream: the existing creative + outreach agents (FLUSSO 3) pick up
``leads`` rows with ``pipeline_status='ready_to_send'`` automatically —
no further wiring needed inside the v3 funnel.

Cost: zero (no external API calls, only DB writes).
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from .types_v3 import FunnelV3Context, ScoredV3Candidate

log = get_logger(__name__)


# Threshold above which a recommended candidate is considered "qualified"
# for the warehouse → ready_to_send pipeline. Mirrors the v2 scoring
# convention: hot ≥ 75, warm ≥ 60, cold otherwise.
QUALIFY_SCORE = 60


def _tier_for(score: int) -> str:
    """Map an overall_score 0-100 to a `lead_score_tier` enum label."""
    if score >= 75:
        return "hot"
    if score >= 60:
        return "warm"
    if score >= 30:
        return "cold"
    return "rejected"


def _pii_hash(business_name: str, place_id: str) -> str:
    """Deterministic SHA256 of "business_name|google_place_id".

    Mirrors the convention used by routes/admin.py and b2c_qualify_service
    so GDPR erase + global_blacklist lookups still work the same way.
    """
    raw = f"{business_name.lower().strip()}|{place_id.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def run_level6_promote_to_leads(
    ctx: FunnelV3Context,
    scored: list[ScoredV3Candidate],
) -> int:
    """Promote recommended candidates to subjects + leads.

    Returns the count of leads actually inserted (skipped duplicates
    don't count). Failures are logged but don't abort the loop —
    one bad candidate shouldn't kill the whole batch.
    """
    sb = get_service_client()
    inserted = 0
    skipped = 0
    failed = 0

    recommended = [s for s in scored if s.recommended_for_rendering]
    if not recommended:
        log.info("level6_promote.no_recommended", tenant_id=ctx.tenant_id)
        return 0

    for cand in recommended:
        try:
            # --- Look up the scan_candidate row for roof_id + scraped data ---
            sc_res = (
                sb.table("scan_candidates")
                .select(
                    "id, business_name, google_place_id, roof_id, "
                    "scraped_data, contact_extraction, enrichment, "
                    "predicted_sector, predicted_ateco_codes, proxy_score_data"
                )
                .eq("id", str(cand.candidate_id))
                .single()
                .execute()
            )
            sc = sc_res.data or {}
            roof_id = sc.get("roof_id")
            if not roof_id:
                # No solar roof → can't create lead (subjects.roof_id NOT NULL)
                log.debug(
                    "level6_promote.skip_no_roof", candidate_id=str(cand.candidate_id)
                )
                skipped += 1
                continue

            place_blob = (sc.get("enrichment") or {}).get("places") or {}
            scraped = sc.get("scraped_data") or {}
            contact = sc.get("contact_extraction") or {}
            score_blob = sc.get("proxy_score_data") or {}

            business_name = (
                sc.get("business_name")
                or place_blob.get("display_name")
                or scraped.get("business_name")
                or "Azienda sconosciuta"
            )
            place_id = sc.get("google_place_id") or ""

            # --- Subject: lookup-or-create (idempotent on tenant_id+roof_id) ---
            existing = (
                sb.table("subjects")
                .select("id")
                .eq("tenant_id", ctx.tenant_id)
                .eq("roof_id", roof_id)
                .limit(1)
                .execute()
            )
            if existing.data:
                subject_id = existing.data[0]["id"]
            else:
                ateco_codes = sc.get("predicted_ateco_codes") or []
                primary_ateco = ateco_codes[0] if ateco_codes else None
                pii_hash_value = _pii_hash(business_name, place_id)

                subject_payload: dict[str, Any] = {
                    "tenant_id": ctx.tenant_id,
                    "roof_id": roof_id,
                    "type": "b2b",
                    "business_name": business_name,
                    "ateco_code": primary_ateco,
                    "decision_maker_email": contact.get("best_email")
                    or scraped.get("best_email"),
                    "decision_maker_email_verified": False,
                    "decision_maker_phone": contact.get("phone")
                    or scraped.get("phone")
                    or place_blob.get("phone"),
                    "decision_maker_phone_source": (
                        "scraping_v3"
                        if (contact.get("phone") or scraped.get("phone"))
                        else ("places" if place_blob.get("phone") else None)
                    ),
                    "linkedin_url": scraped.get("linkedin_url"),
                    "sede_operativa_address": place_blob.get("formatted_address"),
                    "sede_operativa_lat": place_blob.get("lat"),
                    "sede_operativa_lng": place_blob.get("lng"),
                    "sede_operativa_source": "google_places",
                    "sede_operativa_confidence": "high",
                    "data_sources": [
                        {"source": "google_places", "place_id": place_id},
                        {"source": "scraping_v3"},
                    ],
                    "pii_hash": pii_hash_value,
                    "legal_basis": "legitimate_interest_b2b",
                    "raw_data": {
                        "source": "funnel_v3",
                        "scan_candidate_id": str(cand.candidate_id),
                        "predicted_sector": sc.get("predicted_sector"),
                        "proxy_score": score_blob,
                    },
                }
                ins = sb.table("subjects").insert(subject_payload).execute()
                subject_id = (ins.data or [{}])[0].get("id")
                if not subject_id:
                    log.warning(
                        "level6_promote.subject_insert_failed",
                        candidate_id=str(cand.candidate_id),
                    )
                    failed += 1
                    continue

            # --- Lead: skip if one already exists for this subject ---
            existing_lead = (
                sb.table("leads")
                .select("id")
                .eq("tenant_id", ctx.tenant_id)
                .eq("subject_id", subject_id)
                .limit(1)
                .execute()
            )
            if existing_lead.data:
                skipped += 1
                continue

            score = max(0, min(100, int(cand.overall_score)))
            tier = _tier_for(score)
            qualified = score >= QUALIFY_SCORE

            lead_payload: dict[str, Any] = {
                "tenant_id": ctx.tenant_id,
                "roof_id": roof_id,
                "subject_id": subject_id,
                "public_slug": secrets.token_urlsafe(16),
                "score": score,
                "score_tier": tier,
                "score_breakdown": {
                    "icp_fit": score_blob.get("icp_fit_score"),
                    "building_quality": score_blob.get("building_quality_score"),
                    "solar_potential": score_blob.get("solar_potential_score"),
                    "contact_completeness": score_blob.get(
                        "contact_completeness_score"
                    ),
                    "overall": score,
                    "source": "funnel_v3_haiku",
                },
                "pipeline_status": "ready_to_send" if qualified else "new",
                "source": "funnel_v3",
            }
            sb.table("leads").insert(lead_payload).execute()
            inserted += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "level6_promote.exception",
                candidate_id=str(cand.candidate_id),
                err=type(exc).__name__,
                msg=str(exc)[:200],
            )
            failed += 1

    log.info(
        "level6_promote.done",
        tenant_id=ctx.tenant_id,
        recommended=len(recommended),
        inserted=inserted,
        skipped=skipped,
        failed=failed,
    )
    return inserted
