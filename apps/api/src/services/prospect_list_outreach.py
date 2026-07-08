"""On-demand outreach launch for a /scoperta saved list.

Triggered by `POST /v1/prospector/lists/{id}/launch-outreach`. For each
item with `validation_status='accepted'` AND `scan_candidate_id IS NOT
NULL`:

  1. Promote scan_candidate → subjects + leads (idempotent).
  2. Enqueue `creative_task` for the rendering.
  3. Enqueue `outreach_task` for the email send.

Both tasks pass through the existing daily-cap gate (Redis-backed,
Rome timezone) — over-cap items defer naturally to the next day's
cron retry. We do NOT schedule sends here; the OutreachAgent decides
based on the send window (Mon-Fri 08-12 + 14-18 Rome).

This is a thin wrapper that reuses the same promotion logic as
`level6_promote_to_leads.py` — see that module for the full
documentation of subject/lead schema choices.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..core.logging import get_logger
from ..core.queue import enqueue
from ..core.supabase_client import get_service_client

log = get_logger(__name__)


# Mirror the L6 default — leads with score>=60 are "ready_to_send",
# everything else stays "new" and won't auto-fire outreach.
QUALIFY_SCORE = 60


@dataclass(slots=True)
class LaunchResult:
    list_id: str
    promoted: int
    skipped: int
    failed: int
    creative_queued: int
    outreach_queued: int


def _tier_for(score: int) -> str:
    if score >= 75:
        return "hot"
    if score >= 60:
        return "warm"
    if score >= 40:
        return "cold"
    return "rejected"


def _pii_hash(business_name: str, place_id: str) -> str:
    raw = f"{business_name.lower().strip()}|{place_id.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def launch_outreach_for_list(
    *, tenant_id: str, list_id: str, only_accepted: bool = True
) -> LaunchResult:
    """Promote accepted items to leads and queue creative + outreach.

    For ``generic_outreach`` lists:
    - The ``creative_task`` is skipped (no Solar rendering needed).
    - The ``email_template_id`` stored on the list is forwarded in the
      ``outreach_task`` payload so OutreachAgent can render the DB template.
    """
    sb = get_service_client()

    sb.table("prospect_lists").update({"outreach_started_at": datetime.utcnow().isoformat()}).eq(
        "id", list_id
    ).eq("tenant_id", tenant_id).execute()

    # Load list metadata: campaign_type + email_template_id.
    list_res = (
        sb.table("prospect_lists")
        .select("campaign_type, email_template_id")
        .eq("id", list_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    list_row = (list_res.data or [{}])[0]
    is_generic = list_row.get("campaign_type") == "generic_outreach"
    email_template_id: str | None = list_row.get("email_template_id")

    target_status = "accepted" if only_accepted else None
    q = (
        sb.table("prospect_list_items")
        .select("id, scan_candidate_id, validation_status, legal_name, vat_number")
        .eq("tenant_id", tenant_id)
        .eq("list_id", list_id)
    )
    if target_status:
        q = q.eq("validation_status", target_status)
    items = q.execute().data or []

    if not items:
        sb.table("prospect_lists").update(
            {"outreach_completed_at": datetime.utcnow().isoformat()}
        ).eq("id", list_id).execute()
        return LaunchResult(
            list_id=list_id,
            promoted=0,
            skipped=0,
            failed=0,
            creative_queued=0,
            outreach_queued=0,
        )

    promoted = 0
    skipped = 0
    failed = 0
    creative_queued = 0
    outreach_queued = 0

    for item in items:
        candidate_id = item.get("scan_candidate_id")
        if not candidate_id:
            skipped += 1
            continue

        try:
            lead_id = await _promote_to_lead(
                sb,
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                list_id=list_id,
                email_template_id=email_template_id if is_generic else None,
                item_vat=(item.get("vat_number") or None),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "prospect_outreach.promote_failed",
                item_id=item["id"],
                err=type(exc).__name__,
                msg=str(exc)[:200],
            )
            failed += 1
            continue

        if lead_id is None:
            skipped += 1
            continue

        promoted += 1

        # Enqueue creative + outreach. Idempotent job IDs collapse
        # double-clicks. The cap gate is downstream in OutreachAgent.
        #
        # For generic_outreach lists: skip creative_task (no Solar rendering)
        # and forward email_template_id so OutreachAgent uses the DB template.
        if not is_generic:
            try:
                await enqueue(
                    "creative_task",
                    {"tenant_id": tenant_id, "lead_id": lead_id, "force": False},
                    job_id=f"creative:{tenant_id}:{lead_id}",
                )
                creative_queued += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "prospect_outreach.creative_enqueue_failed",
                    lead_id=lead_id,
                    err=type(exc).__name__,
                )

        outreach_payload: dict[str, Any] = {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "channel": "email",
            "force": False,
            "sequence_step": 1,
        }
        if is_generic and email_template_id:
            outreach_payload["email_template_id"] = email_template_id
            outreach_payload["list_id"] = list_id

        try:
            await enqueue(
                "outreach_task",
                outreach_payload,
                job_id=f"outreach:{tenant_id}:{lead_id}:email",
            )
            outreach_queued += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "prospect_outreach.outreach_enqueue_failed",
                lead_id=lead_id,
                err=type(exc).__name__,
            )

    sb.table("prospect_lists").update({"outreach_completed_at": datetime.utcnow().isoformat()}).eq(
        "id", list_id
    ).execute()

    log.info(
        "prospect_outreach.done",
        tenant_id=tenant_id,
        list_id=list_id,
        promoted=promoted,
        skipped=skipped,
        failed=failed,
        creative_queued=creative_queued,
        outreach_queued=outreach_queued,
    )
    return LaunchResult(
        list_id=list_id,
        promoted=promoted,
        skipped=skipped,
        failed=failed,
        creative_queued=creative_queued,
        outreach_queued=outreach_queued,
    )


async def _promote_to_lead(
    sb: Any,
    *,
    tenant_id: str,
    candidate_id: str,
    list_id: str | None = None,
    email_template_id: str | None = None,
    item_vat: str | None = None,
) -> str | None:
    """Promote one scan_candidate to subjects + leads. Idempotent.

    Returns the lead_id (existing or newly created), or None when the
    candidate cannot be promoted (e.g. missing roof_id).

    ``list_id`` and ``email_template_id`` are stored in ``leads.raw_data``
    so the OutreachAgent can recover the custom template even when the
    task was re-enqueued by the warehouse orchestrator (which doesn't know
    about templates).

    Mirrors the schema choices of `level6_promote_to_leads.py` but
    works on a single row and doesn't need a `FunnelV3Context`.
    """
    sc_res = (
        sb.table("scan_candidates")
        .select(
            "id, business_name, google_place_id, roof_id, "
            "scraped_data, contact_extraction, enrichment, "
            "predicted_sector, proxy_score_data, building_quality_score"
        )
        .eq("id", candidate_id)
        .eq("tenant_id", tenant_id)
        .single()
        .execute()
    )
    sc = sc_res.data or {}
    roof_id = sc.get("roof_id")
    if not roof_id:
        log.debug("prospect_outreach.skip_no_roof", candidate_id=candidate_id)
        return None

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

    # Subject upsert
    existing_subj = (
        sb.table("subjects")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("roof_id", roof_id)
        .limit(1)
        .execute()
    )
    if existing_subj.data:
        subject_id = existing_subj.data[0]["id"]
    else:
        phone_value = (
            contact.get("best_phone")
            or contact.get("decision_maker_phone")
            or scraped.get("phone")
            or place_blob.get("phone")
        )
        subject_payload: dict[str, Any] = {
            "tenant_id": tenant_id,
            "roof_id": roof_id,
            "type": "b2b",
            "business_name": business_name,
            # P.IVA — enables the registro-first decision-maker lookup
            # (IT-stakeholders) in the contact waterfall. From the prospect_list
            # item (energivori) or, failing that, the one L2 scraped from the
            # site (OpenCorporates) so Places leads can use it too.
            "vat_number": item_vat or scraped.get("opencorporates_vat"),
            "decision_maker_email": contact.get("best_email"),
            "decision_maker_email_verified": False,
            "decision_maker_phone": phone_value,
            "decision_maker_phone_source": "website_scrape" if phone_value else None,
            "linkedin_url": scraped.get("linkedin_url"),
            "sede_operativa_address": place_blob.get("formatted_address"),
            "sede_operativa_lat": place_blob.get("lat"),
            "sede_operativa_lng": place_blob.get("lng"),
            "sede_operativa_source": "google_places",
            "sede_operativa_confidence": "high",
            "data_sources": [
                {"source": "google_places", "place_id": place_id},
                {"source": "scraping_v3"},
                {"source": "prospector_list"},
            ],
            "pii_hash": _pii_hash(business_name, place_id),
            "legal_basis": "legitimate_interest_b2b",
            "raw_data": {
                "source": "prospector_list_v3",
                "scan_candidate_id": candidate_id,
                "predicted_sector": sc.get("predicted_sector"),
                "proxy_score": score_blob,
                # Generic-outreach metadata — lets the OutreachAgent recover
                # the template even when re-enqueued by the warehouse cron.
                **({"prospect_list_id": list_id} if list_id else {}),
                **({"email_template_id": email_template_id} if email_template_id else {}),
            },
        }
        ins = sb.table("subjects").insert(subject_payload).execute()
        subject_id = (ins.data or [{}])[0].get("id")
        if not subject_id:
            log.warning(
                "prospect_outreach.subject_insert_failed",
                candidate_id=candidate_id,
            )
            return None

    # Lead upsert (one per subject)
    existing_lead = (
        sb.table("leads")
        .select("id")
        .eq("tenant_id", tenant_id)
        .eq("subject_id", subject_id)
        .limit(1)
        .execute()
    )
    if existing_lead.data:
        return existing_lead.data[0]["id"]

    score_value = score_blob.get("overall_score")
    score = max(0, min(100, int(score_value))) if isinstance(score_value, (int, float)) else 60
    tier = _tier_for(score)
    qualified = score >= QUALIFY_SCORE

    lead_payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "roof_id": roof_id,
        "subject_id": subject_id,
        "public_slug": secrets.token_urlsafe(16),
        "score": score,
        "score_tier": tier,
        "score_breakdown": {
            "icp_fit": score_blob.get("icp_fit_score"),
            "building_quality": score_blob.get("building_quality_score"),
            "solar_potential": score_blob.get("solar_potential_score"),
            "contact_completeness": score_blob.get("contact_completeness_score"),
            "overall": score,
            "source": "prospect_list_v3",
        },
        "pipeline_status": "ready_to_send" if qualified else "new",
        # leads.source NULL for proactively-discovered leads (no inbound CTA).
        "source": None,
    }
    ins = sb.table("leads").insert(lead_payload).execute()
    lead_id = (ins.data or [{}])[0].get("id")
    return lead_id
