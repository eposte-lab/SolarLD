"""FLUSSO 1 v3 — L6: Promote scan_candidates to subjects + leads.

Reads ``scan_candidates`` rows where:
  * ``funnel_version = 3``
  * ``solar_verdict = 'accepted'`` (passed L4 Solar gate, has roof_id)
  * a usable ``best_email`` survived L2 + extraction

The L5 score (``recommended_for_rendering``, ``overall_score``) is
recorded on the lead but does NOT gate promotion — the operator inspects
it on the detail page and decides per-lead whether to render/send.

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

Cost: near-zero. The only external call is the OPTIONAL premium
decision-maker lookup (§B) attempted once per NEW subject — capped by the
per-tenant premium budget and fail-open, so a miss/exhausted-budget keeps
the website email and never blocks promotion.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import TYPE_CHECKING, Any

from ...core.logging import get_logger
from ...core.queue import enqueue
from ...core.supabase_client import get_service_client
from ...services.national_chains import is_national_chain
from ...services.tenant_module_service import is_premium_contact_apply_to_send

if TYPE_CHECKING:
    from .types_v3 import FunnelV3Context, ScoredV3Candidate

log = get_logger(__name__)


# Quality bar for L6 promotion. Anything below this bar stays as a
# `scan_candidates` row but is NOT promoted to a `leads` row.
#
#   * `solar_verdict='accepted'`: roof passed the Solar API gate.
#   * `valid email`: at least one usable contact email after L2 +
#     extraction, AND it must be a well-formed address. L'email è un
#     requisito di convalida del contatto: un lead senza email valida
#     non è contattabile e non va promosso a `leads`.
#
# Score and predicted_sector were previously hard gates (score≥70, sector
# in tenant.target_wizard_groups). We dropped them because the demo flow
# needs every Solar-accepted+email candidate to be a clickable `leads`
# row — the operator opens it, inspects the score on the lead detail
# page, and decides whether to render/send manually. The L4 Solar gate
# is already a strong filter; gating again at L6 hides perfectly usable
# leads behind a "show scartati" toggle and confuses operators.
#
# The cap on total funnel-v3 leads per tenant uses
# `tenants.daily_target_send_cap` (default 10) as the upper bound — set
# during onboarding, mirrors the daily warehouse refill ceiling.
DEFAULT_LEAD_CAP_PER_TENANT = 10


# Validazione di formato dell'email prima della promozione a lead.
# `local@domain.tld` con TLD di almeno 2 lettere — esclude stringhe
# tronche tipo "info@" o "nome@dominio" senza TLD.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _is_valid_email(value: str | None) -> bool:
    """True solo se ``value`` è un indirizzo email ben formato."""
    if not value:
        return False
    return bool(_EMAIL_RE.fullmatch(value.strip()))


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

    # Resolve the lead cap (= the size of the *inventory pool* L6 may build).
    # This is DECOUPLED from the daily SEND cap: ``daily_target_send_cap``
    # governs how many leads the daily warehouse pick dispenses per day,
    # whereas the pool may legitimately hold many more leads waiting to be
    # sent organically over the warm-up ramp. When ``settings.lead_pool_cap``
    # is set we use it as the pool ceiling; otherwise we fall back to the
    # send cap (legacy behaviour), then to DEFAULT_LEAD_CAP_PER_TENANT.
    tenant_res = (
        sb.table("tenants")
        .select("daily_target_send_cap, settings")
        .eq("id", ctx.tenant_id)
        .maybe_single()
        .execute()
    )
    _tdata = tenant_res.data or {}
    _settings = _tdata.get("settings")
    pool_cap_raw = _settings.get("lead_pool_cap") if isinstance(_settings, dict) else None
    tenant_cap_raw = _tdata.get("daily_target_send_cap")
    if pool_cap_raw:
        lead_cap = int(pool_cap_raw)
    elif tenant_cap_raw:
        lead_cap = int(tenant_cap_raw)
    else:
        lead_cap = DEFAULT_LEAD_CAP_PER_TENANT

    # Quality bar — promote every Solar-accepted candidate that has at
    # least one usable email. Score and sector match are NOT gates; the
    # operator inspects them on the lead detail page and decides per-lead
    # whether to render/send. See module docstring for rationale.
    def _is_perfect(c: ScoredV3Candidate) -> bool:
        if c.solar_verdict != "accepted":
            return False
        # L'email deve essere presente E ben formata: senza un indirizzo
        # valido il lead non è contattabile.
        if not (bool(c.contact) and _is_valid_email(c.contact.best_email)):
            return False
        # National chains resolve to a corporate HQ mailbox, not the local
        # store's solar buyer (and many stores collapse onto the same address).
        # Never promote them.
        if is_national_chain(
            business_name=c.record.display_name, domain=c.contact.best_email
        ) or is_national_chain(domain=c.record.website):
            log.info(
                "level6_promote.skip_national_chain",
                tenant_id=ctx.tenant_id,
                business=c.record.display_name,
            )
            return False
        return True

    recommended = [s for s in scored if _is_perfect(s)]
    if not recommended:
        log.info(
            "level6_promote.no_perfect_candidate",
            tenant_id=ctx.tenant_id,
            scored=len(scored),
            recommended_pre_filter=sum(1 for s in scored if s.recommended_for_rendering),
        )
        return 0

    # Pre-cap check — count existing funnel_v3 leads for this tenant so
    # subsequent runs (e.g. the daily cron after a partial wipe) don't
    # squeeze past the cap.
    def _existing_v3_count() -> int:
        try:
            res = (
                sb.table("leads")
                .select("id, subjects:subjects(raw_data)")
                .eq("tenant_id", ctx.tenant_id)
                .execute()
            )
            n = 0
            for lr in res.data or []:
                sub = lr.get("subjects") or {}
                raw = (sub.get("raw_data") or {}) if isinstance(sub, dict) else {}
                if raw.get("source") == "funnel_v3":
                    n += 1
            return n
        except Exception:  # noqa: BLE001
            return 0

    existing = _existing_v3_count()
    if existing >= lead_cap:
        log.info(
            "level6_promote.cap_reached_pre_loop",
            tenant_id=ctx.tenant_id,
            existing=existing,
            cap=lead_cap,
        )
        return 0

    # Sort by overall_score DESC so the cap keeps the *best* candidates,
    # not whichever ones the orchestrator happened to pass first.
    recommended.sort(key=lambda c: int(c.overall_score), reverse=True)

    # Opt-in: only auto-enqueue the contact-enrichment waterfall when the tenant
    # has enabled it (default off → keep the website email). Read once per run.
    apply_premium = await is_premium_contact_apply_to_send(ctx.tenant_id)

    for cand in recommended:
        if existing + inserted >= lead_cap:
            log.info(
                "level6_promote.cap_reached_mid_loop",
                tenant_id=ctx.tenant_id,
                inserted=inserted,
                existing=existing,
                cap=lead_cap,
            )
            break
        try:
            # --- Look up the scan_candidate row for roof_id + scraped data ---
            sc_res = (
                sb.table("scan_candidates")
                .select(
                    "id, business_name, google_place_id, roof_id, "
                    "scraped_data, contact_extraction, enrichment, "
                    "predicted_sector, predicted_ateco_codes, proxy_score_data"
                )
                .eq("id", str(cand.record.candidate_id))
                .single()
                .execute()
            )
            sc = sc_res.data or {}
            roof_id = sc.get("roof_id")
            if not roof_id:
                # No solar roof → can't create lead (subjects.roof_id NOT NULL)
                log.debug("level6_promote.skip_no_roof", candidate_id=str(cand.record.candidate_id))
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

            # --- Subject: lookup-or-create, idempotent per AZIENDA ---
            # Keyed on pii_hash (= business_name|place_id), NOT on roof_id:
            # a single rooftop can host many distinct businesses (shopping
            # centres, commercial parks), and each must become its own
            # subject + lead. Keying on roof_id collapsed all co-located
            # companies into one lead.
            # NB: variabile distinta da `existing` (il conteggio lead usato
            # dal cap check a riga ~165) — riusare lo stesso nome qui
            # riassegnava `existing` a un APIResponse e l'iterazione
            # successiva crashava su `existing + inserted`.
            pii_hash_value = _pii_hash(business_name, place_id)
            existing_subj = (
                sb.table("subjects")
                .select("id")
                .eq("tenant_id", ctx.tenant_id)
                .eq("pii_hash", pii_hash_value)
                .limit(1)
                .execute()
            )
            if existing_subj.data:
                subject_id = existing_subj.data[0]["id"]
            else:
                ateco_codes = sc.get("predicted_ateco_codes") or []
                primary_ateco = ateco_codes[0] if ateco_codes else None

                # Promote with the website-scraped email. The contact-enrichment
                # waterfall (Hunter-first → role ladder) runs ASYNC after the
                # lead is inserted (enqueued below) — never blocks promotion and
                # fails open (a miss keeps this website email).
                website_email = contact.get("best_email") or scraped.get("best_email") or None

                subject_payload: dict[str, Any] = {
                    "tenant_id": ctx.tenant_id,
                    "roof_id": roof_id,
                    "type": "b2b",
                    "business_name": business_name,
                    "ateco_code": primary_ateco,
                    "decision_maker_email": website_email,
                    "decision_maker_name": None,
                    "decision_maker_role": None,
                    "decision_maker_email_verified": False,
                    "decision_maker_email_source": "website_scrape",
                    "decision_maker_email_fallback": None,
                    "decision_maker_phone": contact.get("phone")
                    or scraped.get("phone")
                    or place_blob.get("phone"),
                    # subjects.decision_maker_phone_source check constraint
                    # only allows {atoka, website_scrape, manual, NULL}. Map
                    # both v3 sources (web scraping + Google Places) to
                    # website_scrape — the closest semantic equivalent.
                    "decision_maker_phone_source": (
                        "website_scrape"
                        if (contact.get("phone") or scraped.get("phone") or place_blob.get("phone"))
                        else None
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
                        "scan_candidate_id": str(cand.record.candidate_id),
                        "predicted_sector": sc.get("predicted_sector"),
                        "proxy_score": score_blob,
                    },
                }
                ins = sb.table("subjects").insert(subject_payload).execute()
                subject_id = (ins.data or [{}])[0].get("id")
                if not subject_id:
                    log.warning(
                        "level6_promote.subject_insert_failed",
                        candidate_id=str(cand.record.candidate_id),
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
                    "contact_completeness": score_blob.get("contact_completeness_score"),
                    "overall": score,
                    "source": "funnel_v3_haiku",
                },
                # Promote to ready_to_send so the standard daily warehouse
                # picker (`daily_pipeline_cron`) routes the lead through the
                # production rendering + outreach chain. The customer-facing
                # demo is protected from real sends by `tenants.outreach_blocked`
                # (migration 0115), not by leaving the lead stuck in 'new'.
                "pipeline_status": "ready_to_send",
                # leads.source CHECK only allows
                # {cta_click, email_reply, whatsapp_reply, b2c_meta_ads, b2c_post_engagement}
                # or NULL. Proactively-discovered leads (this funnel) have no
                # inbound source — they're not from a CTA click or reply, so
                # leaving NULL is the correct semantic. The funnel version is
                # already tracked via score_breakdown.source='funnel_v3_haiku'
                # and via `funnel_version` on scan_candidates.
                "source": None,
            }
            lead_ins = sb.table("leads").insert(lead_payload).execute()
            inserted += 1

            # Contact-enrichment waterfall — ASYNC, fail-open, OPT-IN. Resolves
            # the best deliverable decision-maker/role contact (Hunter-first →
            # role ladder, catch-all gated) and updates the subject in place.
            # Never blocks promotion; a miss keeps the website email. Only
            # enqueued when the tenant opted in (else outreach stays on the
            # website email — the worker task also re-checks the flag).
            new_lead_id = (lead_ins.data or [{}])[0].get("id")
            if new_lead_id and apply_premium:
                try:
                    await enqueue(
                        "contact_enrichment_task",
                        {"tenant_id": ctx.tenant_id, "lead_id": new_lead_id},
                        job_id=f"contact-enrich:{ctx.tenant_id}:{new_lead_id}",
                    )
                except Exception as exc:  # noqa: BLE001 — enqueue best-effort
                    log.warning("level6_promote.enqueue_enrich_failed", err=type(exc).__name__)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "level6_promote.exception",
                candidate_id=str(cand.record.candidate_id),
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
