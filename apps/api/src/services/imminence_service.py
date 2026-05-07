"""Lead Imminence Predictor — daily ranking of "leads to call today".

Computed nightly by ``imminence_predictions_cron`` (06:30 UTC, before
the 07:30 follow_up_cron so the scoring sees fresh engagement values
from the 04:00 engagement_rollup_cron).

The score 0-100 is a weighted combination of four deterministic
sub-scores. Top candidates (>=60) get a Haiku-generated rationale
(``primary_reasons``, ``suggested_action``, ``talking_points``) so
the operator can read "perché chiamarlo oggi" in plain Italian.

Schema mapping vs. spec:
    spec field            real column                comment
    ────────────────────  ─────────────────────────  ──────────────
    azienda_data          subjects.business_name +   no JSONB blob; we
                          subjects.predicted_sector  derive at query
                          + subjects.employees       time
    proxy_score           leads.score (smallint)     same intent
    bolletta_uploaded_at  portal_events of kind      no leads column
                          'portal.bolletta_uploaded'
    derivations.kw_*      roofs.estimated_kwp,       same intent
                          subjects.solar_kw_installable
    dimensione_categoria  derived bucket from        micro/small/
                          subjects.employees         medium/large
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)

# Weights — sum to 1.0. See spec §"Combinazione finale".
W_BEHAVIORAL = 0.40
W_TEMPORAL = 0.20
W_CONTEXTUAL = 0.20
W_COMPARATIVE = 0.20

# Eligibility filter — only leads that have already shown a sign of
# attention but haven't been "claimed" by sales yet.
ELIGIBLE_PIPELINE_STATUSES = ("opened", "clicked", "engaged")
EXCLUDED_PIPELINE_STATUSES = (
    "whatsapp",
    "appointment",
    "closed_won",
    "closed_lost",
    "blacklisted",
)

# A lead must have been first-contacted by the system (outreach_sent_at)
# to be eligible — otherwise no signal to be "imminent" about.
# Lookback to limit the working set per tenant per day.
ELIGIBILITY_LOOKBACK_DAYS = 120

# Haiku reasoning is generated only for serious candidates — saves cost
# on long-tail leads whose score is already too low to surface.
LLM_REASONING_THRESHOLD = 60


# ── Behavioral helpers ─────────────────────────────────────────────


def _portal_event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    """Bucket portal events by kind for fast scoring."""
    counts: dict[str, int] = {}
    for ev in events:
        kind = ev.get("event_kind")
        if not kind:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _video_seconds(events: list[dict[str, Any]]) -> int:
    """Best-effort total time on video.

    The portal doesn't emit a per-event "watched seconds" field, so we
    approximate: each ``portal.video_play`` event = 30s assumed
    (median of demo telemetry); a ``portal.video_complete`` adds another
    60s. This proxies the spec's `get_total_video_watch_time`.
    """
    play = sum(1 for e in events if e.get("event_kind") == "portal.video_play")
    complete = sum(
        1 for e in events if e.get("event_kind") == "portal.video_complete"
    )
    return play * 30 + complete * 60


def _portal_session_count(events: list[dict[str, Any]]) -> int:
    """Distinct ``session_id`` values in the event window."""
    return len({e.get("session_id") for e in events if e.get("session_id")})


def _bolletta_uploaded_recently(
    events: list[dict[str, Any]], *, within_days: int = 7, now: datetime
) -> bool:
    cutoff = now - timedelta(days=within_days)
    return any(
        e.get("event_kind") == "portal.bolletta_uploaded"
        and _parse_ts(e.get("occurred_at")) >= cutoff
        for e in events
    )


def _parse_ts(s: str | None) -> datetime:
    if not s:
        return datetime.min.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)


# ── Sub-score 1: Behavioral (weight 40%) ───────────────────────────


def compute_behavioral_score(
    events_last_7d: list[dict[str, Any]], *, now: datetime
) -> int:
    """Recent engagement signal — see spec §1."""
    events_last_3d = [
        e
        for e in events_last_7d
        if _parse_ts(e.get("occurred_at")) >= now - timedelta(days=3)
    ]
    visits_3d = _portal_session_count(events_last_3d)
    visits_7d = _portal_session_count(events_last_7d)

    score = 0
    if visits_3d >= 3:
        score += 35
    elif visits_3d >= 2:
        score += 25
    elif visits_3d >= 1:
        score += 15
    elif visits_7d >= 1:
        score += 8

    if _bolletta_uploaded_recently(events_last_7d, within_days=7, now=now):
        score += 30

    vsec = _video_seconds(events_last_7d)
    if vsec >= 60:
        score += 15
    elif vsec >= 30:
        score += 10
    elif vsec >= 10:
        score += 5

    counts = _portal_event_counts(events_last_7d)
    cta_clicks = (
        counts.get("portal.whatsapp_click", 0)
        + counts.get("portal.appointment_click", 0)
        + counts.get("portal.email_reply_click", 0)
    )
    if cta_clicks >= 2:
        score += 15
    elif cta_clicks >= 1:
        score += 10

    repeated = (
        counts.get("portal.scroll_50", 0)
        + counts.get("portal.scroll_90", 0)
        + counts.get("portal.roi_viewed", 0)
    )
    if repeated >= 3:
        score += 5

    return min(score, 100)


# ── Sub-score 2: Temporal (weight 20%) ─────────────────────────────


def compute_temporal_score(
    events_last_7d: list[dict[str, Any]],
    events_prev_5d: list[dict[str, Any]],
    *,
    now: datetime,
) -> int:
    """Engagement timing + acceleration — see spec §2."""
    if not events_last_7d:
        return 0

    last_event_ts = max(_parse_ts(e.get("occurred_at")) for e in events_last_7d)
    hours_since = (now - last_event_ts).total_seconds() / 3600.0

    score = 0
    if 6 <= hours_since <= 48:
        score += 50
    elif 48 < hours_since <= 72:
        score += 35
    elif 72 < hours_since <= 120:
        score += 20

    # Acceleration: more events in last 48h than in the prior 5 days.
    events_48h = [
        e
        for e in events_last_7d
        if _parse_ts(e.get("occurred_at")) >= now - timedelta(hours=48)
    ]
    if len(events_prev_5d) == 0:
        if len(events_48h) >= 2:
            score += 30
    elif len(events_48h) > len(events_prev_5d):
        score += 30
    elif len(events_48h) >= len(events_prev_5d):
        score += 15

    # Business-hour pattern (Europe/Rome ≈ UTC+1/+2). If most events
    # happen 9-18 local, the lead is reachable in those hours too.
    local_hours = [
        (_parse_ts(e.get("occurred_at")).hour + 2) % 24  # naive +2h shift
        for e in events_last_7d
    ]
    if local_hours and sum(1 for h in local_hours if 9 <= h <= 18) / len(
        local_hours
    ) >= 0.6:
        score += 10

    return min(score, 100)


# ── Sub-score 3: Contextual (weight 20%) ───────────────────────────


def _dimensione_categoria(employees: int | None) -> str:
    if employees is None:
        return "unknown"
    if employees < 10:
        return "micro"
    if employees < 50:
        return "small"
    if employees < 250:
        return "medium"
    return "large"


def compute_contextual_score(
    *,
    lead_score: int | None,
    score_tier: str | None,
    sector_conversion_rate: float,
    employees: int | None,
    estimated_kwp: float | None,
) -> int:
    """Static lead attributes — see spec §3."""
    score = 0

    if lead_score is not None:
        if lead_score >= 80:
            score += 30
        elif lead_score >= 70:
            score += 25
        elif lead_score >= 60:
            score += 15

    if score_tier == "hot":
        score += 25
    elif score_tier == "warm":
        score += 15

    if sector_conversion_rate >= 0.30:
        score += 20
    elif sector_conversion_rate >= 0.20:
        score += 15
    elif sector_conversion_rate >= 0.10:
        score += 8

    dim = _dimensione_categoria(employees)
    if dim in ("medium", "large"):
        score += 15

    if estimated_kwp and estimated_kwp >= 200:
        score += 10

    return min(score, 100)


# ── Sub-score 4: Comparative (weight 20%) ──────────────────────────


@dataclass
class SimilarClosedLead:
    engagement_score_at_close: int
    days_from_first_contact_to_close: int


def compute_comparative_score(
    *,
    similar_closed: list[SimilarClosedLead],
    lead_engagement_score: int,
    days_since_first_contact: int,
) -> int:
    """Similarity to recently-closed leads — see spec §4."""
    if not similar_closed:
        return 30  # neutral: no benchmark, don't punish

    avg_eng = sum(s.engagement_score_at_close for s in similar_closed) / len(
        similar_closed
    )
    avg_days = sum(
        s.days_from_first_contact_to_close for s in similar_closed
    ) / len(similar_closed)

    score = 0
    if avg_days > 0 and days_since_first_contact >= avg_days * 0.8:
        score += 35
    elif avg_days > 0 and days_since_first_contact >= avg_days * 0.5:
        score += 20

    if avg_eng > 0 and lead_engagement_score >= avg_eng * 0.8:
        score += 35
    elif avg_eng > 0 and lead_engagement_score >= avg_eng * 0.5:
        score += 20

    n = len(similar_closed)
    if n >= 5:
        score += 30
    elif n >= 3:
        score += 20

    return min(score, 100)


# ── Combination ────────────────────────────────────────────────────


@dataclass
class ImminenceScores:
    behavioral: int
    temporal: int
    contextual: int
    comparative: int
    final: int


def combine_scores(
    behavioral: int, temporal: int, contextual: int, comparative: int
) -> ImminenceScores:
    final = int(
        behavioral * W_BEHAVIORAL
        + temporal * W_TEMPORAL
        + contextual * W_CONTEXTUAL
        + comparative * W_COMPARATIVE
    )
    return ImminenceScores(
        behavioral=behavioral,
        temporal=temporal,
        contextual=contextual,
        comparative=comparative,
        final=max(0, min(100, final)),
    )


# ── Orchestration: per-tenant batch ────────────────────────────────


@dataclass
class LeadInputs:
    """Bag-of-everything used by the scoring pipeline. Built once per
    eligible lead before sub-score computation, lets the algorithms
    stay pure (no I/O)."""

    lead_id: str
    tenant_id: str
    lead_score: int | None
    score_tier: str | None
    pipeline_status: str | None
    engagement_score: int
    outreach_sent_at: datetime | None
    estimated_kwp: float | None
    employees: int | None
    predicted_sector: str | None
    business_name: str | None
    portal_events_last_7d: list[dict[str, Any]] = field(default_factory=list)
    portal_events_prev_5d: list[dict[str, Any]] = field(default_factory=list)


def _build_lead_inputs(
    lead_row: dict[str, Any],
    *,
    events_by_lead: dict[str, list[dict[str, Any]]],
    now: datetime,
) -> LeadInputs:
    subjects = lead_row.get("subjects") or {}
    roofs = lead_row.get("roofs") or {}
    all_events = events_by_lead.get(lead_row["id"], [])

    seven_d_cutoff = now - timedelta(days=7)
    twelve_d_cutoff = now - timedelta(days=12)
    last_7d = [
        e for e in all_events if _parse_ts(e.get("occurred_at")) >= seven_d_cutoff
    ]
    prev_5d = [
        e
        for e in all_events
        if twelve_d_cutoff <= _parse_ts(e.get("occurred_at")) < seven_d_cutoff
    ]

    return LeadInputs(
        lead_id=lead_row["id"],
        tenant_id=lead_row["tenant_id"],
        lead_score=lead_row.get("score"),
        score_tier=lead_row.get("score_tier"),
        pipeline_status=lead_row.get("pipeline_status"),
        engagement_score=lead_row.get("engagement_score") or 0,
        outreach_sent_at=_parse_ts(lead_row.get("outreach_sent_at"))
        if lead_row.get("outreach_sent_at")
        else None,
        estimated_kwp=(roofs.get("estimated_kwp")) or subjects.get(
            "solar_kw_installable"
        ),
        employees=subjects.get("employees"),
        predicted_sector=subjects.get("predicted_sector"),
        business_name=subjects.get("business_name"),
        portal_events_last_7d=last_7d,
        portal_events_prev_5d=prev_5d,
    )


def _compute_sector_conversion_rates(
    sb: Any, tenant_id: str, *, days_back: int = 90
) -> dict[str, float]:
    """closed_won / total per predicted_sector for this tenant."""
    since = (datetime.now(UTC) - timedelta(days=days_back)).isoformat()
    rows_resp = (
        sb.table("leads")
        .select("subject_id, pipeline_status, subjects(predicted_sector)")
        .eq("tenant_id", tenant_id)
        .gte("created_at", since)
        .execute()
    )
    by_sector: dict[str, dict[str, int]] = {}
    for r in rows_resp.data or []:
        sec = (r.get("subjects") or {}).get("predicted_sector")
        if not sec:
            continue
        bucket = by_sector.setdefault(sec, {"won": 0, "total": 0})
        bucket["total"] += 1
        if r.get("pipeline_status") == "closed_won":
            bucket["won"] += 1
    return {
        sec: (b["won"] / b["total"]) if b["total"] > 0 else 0.0
        for sec, b in by_sector.items()
    }


def _fetch_similar_closed(
    sb: Any,
    *,
    tenant_id: str,
    predicted_sector: str | None,
    lead_score: int | None,
    employees: int | None,
    days_back: int = 90,
    max_results: int = 20,
) -> list[SimilarClosedLead]:
    """Closed-won leads in the same sector / size / score band."""
    if not predicted_sector or lead_score is None:
        return []
    since = (datetime.now(UTC) - timedelta(days=days_back)).isoformat()
    dim_target = _dimensione_categoria(employees)

    rows = (
        sb.table("leads")
        .select(
            "id, engagement_score, outreach_sent_at, updated_at, subjects(predicted_sector, employees)"
        )
        .eq("tenant_id", tenant_id)
        .eq("pipeline_status", "closed_won")
        .gte("updated_at", since)
        .limit(200)
        .execute()
    )

    out: list[SimilarClosedLead] = []
    for r in rows.data or []:
        s = r.get("subjects") or {}
        if s.get("predicted_sector") != predicted_sector:
            continue
        if _dimensione_categoria(s.get("employees")) != dim_target:
            continue
        sent = _parse_ts(r.get("outreach_sent_at"))
        closed = _parse_ts(r.get("updated_at"))
        if sent.year < 2000:
            continue
        out.append(
            SimilarClosedLead(
                engagement_score_at_close=r.get("engagement_score") or 0,
                days_from_first_contact_to_close=max(
                    0, int((closed - sent).total_seconds() / 86400)
                ),
            )
        )
        if len(out) >= max_results:
            break
    return out


# ── Eligible-leads loader ──────────────────────────────────────────


def fetch_eligible_leads(sb: Any, tenant_id: str) -> list[dict[str, Any]]:
    """Leads that already engaged at least once but aren't claimed yet."""
    since = (
        datetime.now(UTC) - timedelta(days=ELIGIBILITY_LOOKBACK_DAYS)
    ).isoformat()
    res = (
        sb.table("leads")
        .select(
            "id, tenant_id, score, score_tier, pipeline_status, engagement_score, "
            "outreach_sent_at, last_portal_event_at, "
            "subjects(business_name, predicted_sector, employees, solar_kw_installable), "
            "roofs(estimated_kwp)"
        )
        .eq("tenant_id", tenant_id)
        .in_("pipeline_status", list(ELIGIBLE_PIPELINE_STATUSES))
        .not_.is_("outreach_sent_at", "null")
        .gte("created_at", since)
        .execute()
    )
    return res.data or []


def fetch_portal_events_window(
    sb: Any, lead_ids: list[str], *, days: int = 12
) -> dict[str, list[dict[str, Any]]]:
    """One round-trip for all eligible leads' portal events."""
    if not lead_ids:
        return {}
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    res = (
        sb.table("portal_events")
        .select("lead_id, session_id, event_kind, occurred_at")
        .in_("lead_id", lead_ids)
        .gte("occurred_at", since)
        .execute()
    )
    by_lead: dict[str, list[dict[str, Any]]] = {}
    for ev in res.data or []:
        by_lead.setdefault(ev["lead_id"], []).append(ev)
    return by_lead


# ── Per-lead scoring (no I/O) ──────────────────────────────────────


def score_lead(
    inputs: LeadInputs,
    *,
    sector_conversion_rate: float,
    similar_closed: list[SimilarClosedLead],
    now: datetime,
) -> ImminenceScores:
    behavioral = compute_behavioral_score(
        inputs.portal_events_last_7d, now=now
    )
    temporal = compute_temporal_score(
        inputs.portal_events_last_7d, inputs.portal_events_prev_5d, now=now
    )
    contextual = compute_contextual_score(
        lead_score=inputs.lead_score,
        score_tier=inputs.score_tier,
        sector_conversion_rate=sector_conversion_rate,
        employees=inputs.employees,
        estimated_kwp=inputs.estimated_kwp,
    )
    days_since_first = 0
    if inputs.outreach_sent_at:
        days_since_first = max(
            0, (now - inputs.outreach_sent_at).days
        )
    comparative = compute_comparative_score(
        similar_closed=similar_closed,
        lead_engagement_score=inputs.engagement_score,
        days_since_first_contact=days_since_first,
    )
    return combine_scores(behavioral, temporal, contextual, comparative)


# ── Persistence ────────────────────────────────────────────────────


def upsert_prediction(
    sb: Any,
    *,
    tenant_id: str,
    lead_id: str,
    prediction_date: date,
    scores: ImminenceScores,
    reasoning: dict[str, Any] | None,
) -> None:
    """One row per (tenant, lead, prediction_date) — UPSERT on
    ``(tenant_id, lead_id, prediction_date)``."""
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "prediction_date": prediction_date.isoformat(),
        "imminence_score": scores.final,
        "behavioral_score": scores.behavioral,
        "temporal_score": scores.temporal,
        "contextual_score": scores.contextual,
        "comparative_score": scores.comparative,
        "primary_reasons": (reasoning or {}).get("primary_reasons") or [],
        "talking_points": (reasoning or {}).get("talking_points") or [],
        "suggested_action": (reasoning or {}).get("suggested_action"),
        "suggested_channel": (reasoning or {}).get("suggested_channel"),
        "best_time_to_contact": (reasoning or {}).get("best_time_to_contact"),
    }
    sb.table("lead_imminence_predictions").upsert(
        payload, on_conflict="tenant_id,lead_id,prediction_date"
    ).execute()

    # Mirror onto leads for fast list ordering.
    sb.table("leads").update(
        {
            "last_imminence_score": scores.final,
            "last_imminence_predicted_at": datetime.now(UTC).isoformat(),
        }
    ).eq("id", lead_id).execute()


# ── Entry point used by the cron ───────────────────────────────────


async def run_imminence_predictions_for_tenant(
    tenant_id: str,
    *,
    prediction_date: date | None = None,
    reasoning_fn: Any = None,  # async (LeadInputs, ImminenceScores) -> dict
) -> dict[str, Any]:
    """Compute today's predictions for one tenant.

    ``reasoning_fn`` is injected (DI) so tests don't need to mock the
    Anthropic client. The cron passes the real Haiku-backed function.
    """
    sb = get_service_client()
    now = datetime.now(UTC)
    pdate = prediction_date or now.date()

    eligible = fetch_eligible_leads(sb, tenant_id)
    if not eligible:
        log.info("imminence.no_eligible_leads", tenant_id=tenant_id)
        return {"tenant_id": tenant_id, "scored": 0, "reasoned": 0}

    lead_ids = [r["id"] for r in eligible]
    events_by_lead = fetch_portal_events_window(sb, lead_ids, days=12)
    sector_rates = _compute_sector_conversion_rates(sb, tenant_id)

    scored = 0
    reasoned = 0
    for row in eligible:
        inputs = _build_lead_inputs(
            row, events_by_lead=events_by_lead, now=now
        )
        similar = _fetch_similar_closed(
            sb,
            tenant_id=tenant_id,
            predicted_sector=inputs.predicted_sector,
            lead_score=inputs.lead_score,
            employees=inputs.employees,
        )
        scores = score_lead(
            inputs,
            sector_conversion_rate=sector_rates.get(
                inputs.predicted_sector or "", 0.0
            ),
            similar_closed=similar,
            now=now,
        )

        reasoning = None
        if scores.final >= LLM_REASONING_THRESHOLD and reasoning_fn is not None:
            try:
                reasoning = await reasoning_fn(inputs, scores)
                reasoned += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "imminence.reasoning_failed",
                    lead_id=inputs.lead_id,
                    err=str(exc),
                )

        upsert_prediction(
            sb,
            tenant_id=tenant_id,
            lead_id=inputs.lead_id,
            prediction_date=pdate,
            scores=scores,
            reasoning=reasoning,
        )
        scored += 1

    log.info(
        "imminence.tenant_done",
        tenant_id=tenant_id,
        scored=scored,
        reasoned=reasoned,
    )
    return {"tenant_id": tenant_id, "scored": scored, "reasoned": reasoned}
