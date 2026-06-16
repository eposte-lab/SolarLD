"""Engagement scoring & nightly rollup (Part B.1 deep-tracking).

The score is the dashboard's "heat" metric — it tells the installer
which leads are worth calling *today*. It lives as a denormalised
integer 0-100 on ``leads.engagement_score``.

It is kept fresh two ways, both routed through ``compute_score`` so
the value is always the capped 3-tier score:
  * ``recompute_lead_engagement`` — called on every portal beacon
    (routes/public.py) so the score reacts in real time;
  * ``run_engagement_rollup`` — the nightly cron, a full reconcile
    across all leads with activity in the 30-day window.

Formula (v3 — ristrutturata in 3 fasce di intenzione):

    Fascia 1 — Attenzione (ha aperto e sfogliato), tetto 28:
      +10  per sessione distinta sul portale
      + 3  per portal.scroll_50
      + 6  per portal.scroll_90
      + 1  per 30s sul portale (tetto 12)
    Fascia 2 — Coinvolgimento (ha consumato i contenuti), tetto 30:
      + 8  per portal.video_play
      +16  per portal.video_complete
      + 6  per portal.audio_on
      + 6  per portal.video_fullscreen
      +10  per portal.roi_viewed
      + 8  per portal.contact_view (ha aperto il form di contatto)
      +12  per portal.contact_started (ha iniziato a compilarlo)
      + 5  se outreach_opened_at è valorizzato (email aperta)
    Fascia 3 — Intenzione (ha alzato la mano), tetto 70:
      +50  per portal.whatsapp_click
      +50  per portal.appointment_click
      +50  per portal.email_reply_click
      +35  se la bolletta è stata caricata (leads.bolletta_uploaded_at —
           segnale autorevole, indipendente dall'evento portal_events)
      +12  se outreach_clicked_at è valorizzato (link email cliccato)

Floor "richiesta di contatto": se il lead ha inviato il form (segnale
autorevole ``leads.appointment_requested_at``, indipendente dall'evento
portal_events e immune alla finestra di 30 giorni) lo score è forzato ad
almeno ``APPOINTMENT_HOT_FLOOR`` (70). Una richiesta di contatto è la
mano alzata più forte del funnel: deve sempre risultare "caldo", anche
mesi dopo e anche se gli eventi di navigazione sono usciti dalla
finestra. Vale sia per i tenant moderati (richiesta in coda) sia, in
backfill, per chi ha già richiesto contatto in passato.

Logica: la sola apertura del portale non rende "caldo" un lead — la
fascia Attenzione è limitata a 32 punti. Per arrivare a "caldo" (>=60)
serve consumare i contenuti e, soprattutto, un'azione di intenzione
(CTA, bolletta, reply). Così "caldo" significa davvero "ha alzato la
mano", non "ha aperto il link".

Clamped to [0, 100]. Inputs outside the 30-day window are dropped to
keep the score sensitive — a lead who was hot in January shouldn't
dominate the April "hot leads" list if they've since gone cold.

Rollup side-effects:
  * Sets ``engagement_score``, ``engagement_score_updated_at``,
    ``portal_sessions``, ``portal_total_time_sec``,
    ``deepest_scroll_pct`` on every lead that has at least one
    portal event in the window.
  * Leaves leads with zero portal activity untouched (their score
    stays at its previous value until they earn a new event). This
    avoids writing rows we have no new signal for.

Dashboard real-time companion (NOT in this file):
    ``get_hot_leads_now(tenant_id, minutes=60)`` reads
    ``portal_events`` directly for the last hour — see the dashboard
    side at ``apps/dashboard/src/lib/data/engagement.ts``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)

# Rolling window — 30 days matches the "caldi di oggi" use case: a
# lead active in the last month is a plausible sales target.
ROLLUP_WINDOW_DAYS = 30

# How often portal.heartbeat fires from the client (migration-level
# contract with apps/lead-portal/src/lib/tracking.ts). Used to convert
# heartbeat count → time-on-page seconds.
HEARTBEAT_INTERVAL_SEC = 15

# ---------------------------------------------------------------------------
# Pesi dello score — organizzati in 3 fasce di intenzione crescente.
# ``compute_score`` è l'unica funzione di calcolo: la usano sia il
# rollup notturno sia il recompute in tempo reale (``recompute_lead_
# engagement``), così il punteggio è sempre quello capato per fascia —
# niente accumulatori liberi che sforano i tetti.
# ---------------------------------------------------------------------------

# Fascia 1 — Attenzione
W_SESSION = 10  # per sessione distinta sul portale
W_SCROLL_50 = 3  # ha superato metà pagina
W_SCROLL_90 = 6  # ha letto (quasi) fino in fondo
W_TIME_PER_30S = 1  # +1 ogni 30s sul portale
W_TIME_CAP = 12  # tetto del bonus tempo
TIER_ATTENTION_CAP = 28  # tetto dell'intera fascia "attenzione"

# Fascia 2 — Coinvolgimento
W_VIDEO_PLAY = 8
W_VIDEO_COMPLETE = 16
W_AUDIO_ON = 6  # ha attivato l'audio del video
W_VIDEO_FULLSCREEN = 6  # ha messo il video a schermo intero
W_ROI_VIEWED = 10
W_CONTACT_VIEW = 8  # ha aperto il form "richiesta di contatto"
W_CONTACT_STARTED = 12  # ha iniziato a compilare il form (primo carattere)
W_EMAIL_OPENED = 5
TIER_ENGAGEMENT_CAP = 30  # tetto dell'intera fascia "coinvolgimento"

# Fascia 3 — Intenzione. I tetti di Attenzione (28) + Coinvolgimento
# (30) sommano 58: senza un'azione di intenzione un lead NON arriva mai
# a "caldo" (>=60). Un solo click su una CTA di contatto basta invece a
# superarlo (es. apertura portale +10 e click appuntamento +50 = 60).
W_WHATSAPP_CLICK = 50
W_APPOINTMENT_CLICK = 50
W_EMAIL_REPLY_CLICK = 50
W_BOLLETTA_UPLOADED = 35
W_EMAIL_CLICKED = 12  # ha cliccato un link nell'email di outreach
TIER_INTENT_CAP = 70  # tetto dell'intera fascia "intenzione"

# Floor applicato quando il lead ha INVIATO il form di contatto
# (``leads.appointment_requested_at``): una richiesta di contatto deve
# sempre risultare "caldo" (>=60). 70 la colloca saldamente in fascia
# calda lasciando margine per i segnali aggiuntivi.
APPOINTMENT_HOT_FLOOR = 70

SCORE_MAX = 100


@dataclass
class LeadEngagementStats:
    """Per-lead accumulator — shaped to match the nightly rollup SQL."""

    lead_id: str
    tenant_id: str

    sessions: set[str] = field(default_factory=set)
    scroll_50: int = 0
    scroll_90: int = 0
    roi_viewed: int = 0
    video_play: int = 0
    video_complete: int = 0
    audio_on: int = 0
    video_fullscreen: int = 0
    contact_view: int = 0
    contact_started: int = 0
    bolletta_uploaded: int = 0
    whatsapp_click: int = 0
    appointment_click: int = 0
    email_reply_click: int = 0
    heartbeats: int = 0
    deepest_scroll_pct: int = 0

    # Email-level signals from the leads row (not events). Populated
    # by the rollup after the loop.
    outreach_opened: bool = False
    outreach_clicked: bool = False
    # Authoritative "ha inviato il form di contatto" signal from
    # leads.appointment_requested_at — independent of portal_events and
    # of the 30-day window. Triggers the +50 intent credit AND the
    # hot-floor (see compute_score).
    appointment_requested: bool = False

    @property
    def total_time_sec(self) -> int:
        return self.heartbeats * HEARTBEAT_INTERVAL_SEC


def compute_score(stats: LeadEngagementStats) -> int:
    """Pure function — stats in, 0..100 out.

    Lo score è la somma di 3 fasce di intenzione crescente, ognuna con
    un proprio tetto: la sola "attenzione" non basta per "caldo", serve
    un'azione di "intenzione". Split dal rollup I/O-bound così i test
    possono passare uno stats costruito a mano e verificare i confini
    delle fasce senza Supabase.
    """
    # Fascia 1 — Attenzione: aperture, scroll, tempo.
    attention = 0
    attention += W_SESSION * len(stats.sessions)
    attention += W_SCROLL_50 * stats.scroll_50
    attention += W_SCROLL_90 * stats.scroll_90
    time_points = (stats.total_time_sec // 30) * W_TIME_PER_30S
    attention += min(int(time_points), W_TIME_CAP)
    attention = min(attention, TIER_ATTENTION_CAP)

    # Fascia 2 — Coinvolgimento: ha consumato i contenuti.
    engagement = 0
    engagement += W_VIDEO_PLAY * stats.video_play
    engagement += W_VIDEO_COMPLETE * stats.video_complete
    engagement += W_AUDIO_ON * stats.audio_on
    engagement += W_VIDEO_FULLSCREEN * stats.video_fullscreen
    engagement += W_ROI_VIEWED * stats.roi_viewed
    # Il funnel di contatto è binario: aprire/iniziare il form più volte
    # non vale di più — conta che l'abbia fatto.
    engagement += W_CONTACT_VIEW * min(1, stats.contact_view)
    engagement += W_CONTACT_STARTED * min(1, stats.contact_started)
    if stats.outreach_opened:
        engagement += W_EMAIL_OPENED
    engagement = min(engagement, TIER_ENGAGEMENT_CAP)

    # Fascia 3 — Intenzione: ha alzato la mano.
    intent = 0
    intent += W_WHATSAPP_CLICK * stats.whatsapp_click
    # Richiesta di contatto: form inviato (segnale autorevole dalla
    # colonna ``appointment_requested_at``) OPPURE click "Contattaci
    # subito" tracciato via portal_events. Binario: vale +50 una volta
    # sola, da qualunque delle due fonti provenga.
    requested_contact = stats.appointment_requested or stats.appointment_click > 0
    if requested_contact:
        intent += W_APPOINTMENT_CLICK
    intent += W_EMAIL_REPLY_CLICK * stats.email_reply_click
    # La bolletta è un segnale binario: caricarla due volte non vale +70.
    intent += W_BOLLETTA_UPLOADED * min(1, stats.bolletta_uploaded)
    if stats.outreach_clicked:
        intent += W_EMAIL_CLICKED
    intent = min(intent, TIER_INTENT_CAP)

    score = max(0, min(SCORE_MAX, attention + engagement + intent))
    # Floor: una richiesta di contatto inviata è la mano alzata più forte
    # del funnel — deve sempre risultare "calda", anche se gli eventi di
    # navigazione sono fuori dalla finestra di 30 giorni.
    if requested_contact:
        score = max(score, APPOINTMENT_HOT_FLOOR)
    return score


def _accumulate_event(stats: LeadEngagementStats, row: dict[str, Any]) -> None:
    """Fold one ``portal_events`` row into a lead's running stats.

    Shared by the nightly rollup and the real-time single-lead
    recompute so both score from exactly the same logic.
    """
    sid = row.get("session_id")
    # Server-generated session ids (``server:{uuid}``) mark backend
    # actions (e.g. the OCR-side bolletta fire) — not real browsing
    # sessions, so they don't count toward the session tally.
    if sid and not str(sid).startswith("server:"):
        stats.sessions.add(str(sid))

    kind = row.get("event_kind") or ""
    meta = row.get("metadata") or {}

    if kind == "portal.scroll_50":
        stats.scroll_50 += 1
        stats.deepest_scroll_pct = max(stats.deepest_scroll_pct, 50)
    elif kind == "portal.scroll_90":
        stats.scroll_90 += 1
        stats.deepest_scroll_pct = max(stats.deepest_scroll_pct, 90)
    elif kind == "portal.roi_viewed":
        stats.roi_viewed += 1
    elif kind == "portal.video_play":
        stats.video_play += 1
    elif kind == "portal.video_complete":
        stats.video_complete += 1
    elif kind == "portal.audio_on":
        stats.audio_on += 1
    elif kind == "portal.video_fullscreen":
        stats.video_fullscreen += 1
    elif kind == "portal.contact_view":
        stats.contact_view += 1
    elif kind in ("portal.contact_started", "portal.contact_abandoned"):
        # Abandoned-with-data is the same "began filling" signal — count it
        # so a lost ``contact_started`` beacon doesn't drop the +12.
        stats.contact_started += 1
    elif kind == "portal.email_reply_click":
        stats.email_reply_click += 1
    elif kind == "portal.whatsapp_click":
        stats.whatsapp_click += 1
    elif kind == "portal.appointment_click":
        stats.appointment_click += 1
    elif kind == "portal.bolletta_uploaded":
        stats.bolletta_uploaded += 1
    elif kind == "portal.heartbeat":
        stats.heartbeats += 1
    # portal.view / portal.leave contribute only via the session count.

    pct = meta.get("pct")
    if isinstance(pct, (int, float)) and pct > stats.deepest_scroll_pct:
        stats.deepest_scroll_pct = int(min(100, max(0, pct)))


async def recompute_lead_engagement(lead_id: str, *, now: datetime | None = None) -> int | None:
    """Recompute one lead's engagement score from its ``portal_events``.

    The real-time path used to blindly increment the score per event
    (uncapped, no dedup): re-visiting the portal a few times pushed a
    lead with zero intent action to 100/100. This instead recomputes
    the capped 3-tier score from the actual events — always correct and
    bounded — and is cheap enough to run on every portal beacon.

    Returns the new score, or ``None`` if the lead is missing / the
    write failed.
    """
    sb = get_service_client()
    now = now or datetime.now(UTC)
    window_start = now - timedelta(days=ROLLUP_WINDOW_DAYS)

    lead_res = (
        sb.table("leads")
        .select(
            "id, tenant_id, outreach_opened_at, outreach_clicked_at, "
            "bolletta_uploaded_at, appointment_requested_at, engagement_peak_score"
        )
        .eq("id", lead_id)
        .limit(1)
        .execute()
    )
    lead = (lead_res.data or [None])[0]
    if not lead:
        return None

    events_res = (
        sb.table("portal_events")
        .select("session_id, event_kind, metadata")
        .eq("lead_id", lead_id)
        .gte("occurred_at", window_start.isoformat())
        .execute()
    )
    stats = LeadEngagementStats(lead_id=lead_id, tenant_id=str(lead["tenant_id"]))
    for row in events_res.data or []:
        _accumulate_event(stats, row)
    stats.outreach_opened = bool(lead.get("outreach_opened_at"))
    stats.outreach_clicked = bool(lead.get("outreach_clicked_at"))
    stats.appointment_requested = bool(lead.get("appointment_requested_at"))
    # `leads.bolletta_uploaded_at` is the authoritative bolletta signal:
    # it's stamped synchronously the moment the upload lands, while the
    # `portal.bolletta_uploaded` event is best-effort and can be lost if
    # the request is cut short. Credit the upload from the column so the
    # score never under-reports it.
    if lead.get("bolletta_uploaded_at"):
        stats.bolletta_uploaded = max(stats.bolletta_uploaded, 1)

    score = compute_score(stats)
    prev_peak = int(lead.get("engagement_peak_score") or 0)
    try:
        sb.table("leads").update(
            {
                "engagement_score": score,
                "engagement_score_updated_at": now.isoformat(),
                "engagement_peak_score": max(prev_peak, score),
                "portal_sessions": len(stats.sessions),
                "portal_total_time_sec": stats.total_time_sec,
                "deepest_scroll_pct": stats.deepest_scroll_pct,
                "last_portal_event_at": now.isoformat(),
            }
        ).eq("id", lead_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("engagement.recompute.update_failed", lead_id=lead_id, err=str(exc))
        return None
    return score


async def run_engagement_rollup(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh ``leads.engagement_score`` for all active leads.

    Scans the last 30 days of ``portal_events`` + pulls the email
    engagement timestamps from ``leads`` and writes back the
    aggregated rollup columns in a single UPDATE-per-lead batch.

    Returns ``{"leads_updated": N, "scored_hot": M}`` for logging
    (``scored_hot`` counts leads whose new score is >=60, the UX
    "hot right now" threshold).
    """
    sb = get_service_client()
    now = now or datetime.now(UTC)
    window_start = now - timedelta(days=ROLLUP_WINDOW_DAYS)

    # ------------------------------------------------------------------
    # 1) Pull last-30d portal events — one pass, group in Python.
    # ------------------------------------------------------------------
    events_res = (
        sb.table("portal_events")
        .select("tenant_id, lead_id, session_id, event_kind, metadata")
        .gte("occurred_at", window_start.isoformat())
        .execute()
    )

    by_lead: dict[str, LeadEngagementStats] = {}
    for row in events_res.data or []:
        lid = row.get("lead_id")
        tid = row.get("tenant_id")
        if not lid or not tid:
            continue
        stats = by_lead.setdefault(lid, LeadEngagementStats(lead_id=lid, tenant_id=tid))
        _accumulate_event(stats, row)

    if not by_lead:
        log.info("engagement.rollup.no_events")
        return {"leads_updated": 0, "scored_hot": 0}

    # ------------------------------------------------------------------
    # 2) Pull email engagement timestamps for those leads in one shot.
    # ------------------------------------------------------------------
    lead_ids = list(by_lead.keys())
    leads_res = (
        sb.table("leads")
        .select(
            "id, outreach_opened_at, outreach_clicked_at, "
            "bolletta_uploaded_at, appointment_requested_at"
        )
        .in_("id", lead_ids)
        .execute()
    )
    for row in leads_res.data or []:
        lid = row.get("id")
        if lid not in by_lead:
            continue
        by_lead[lid].outreach_opened = bool(row.get("outreach_opened_at"))
        by_lead[lid].outreach_clicked = bool(row.get("outreach_clicked_at"))
        by_lead[lid].appointment_requested = bool(row.get("appointment_requested_at"))
        # Authoritative bolletta signal — see recompute_lead_engagement.
        if row.get("bolletta_uploaded_at"):
            by_lead[lid].bolletta_uploaded = max(by_lead[lid].bolletta_uploaded, 1)

    # ------------------------------------------------------------------
    # 3) Compute scores + write back. Supabase PostgREST doesn't
    # support a single UPDATE with per-row values, so we loop — but
    # the batch is bounded by "leads with activity in last 30 days"
    # which is inherently small (hundreds, not millions).
    # ------------------------------------------------------------------
    now_iso = now.isoformat()
    updated = 0
    hot = 0
    errors = 0
    for stats in by_lead.values():
        score = compute_score(stats)
        try:
            sb.table("leads").update(
                {
                    "engagement_score": score,
                    "engagement_score_updated_at": now_iso,
                    "portal_sessions": len(stats.sessions),
                    "portal_total_time_sec": stats.total_time_sec,
                    "deepest_scroll_pct": stats.deepest_scroll_pct,
                }
            ).eq("id", stats.lead_id).execute()
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.warning(
                "engagement.rollup.update_failed",
                lead_id=stats.lead_id,
                err=str(exc),
            )
            continue
        updated += 1
        if score >= 60:
            hot += 1

    log.info(
        "engagement.rollup.done",
        leads_updated=updated,
        scored_hot=hot,
        errors=errors,
        window_start=window_start.isoformat(),
    )
    return {
        "leads_updated": updated,
        "scored_hot": hot,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Real-time helper — NOT called by the cron; exposed for ad-hoc tests
# and potential future admin endpoints. The dashboard implements its
# own TypeScript version of this query in ``lib/data/engagement.ts``.
# ---------------------------------------------------------------------------


async def get_hot_leads_now(
    tenant_id: str,
    *,
    minutes: int = 60,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Leads with the most portal_events in the last ``minutes`` minutes.

    Useful for a watchdog script or CLI debugging; the dashboard reads
    the same signal from its own TypeScript fetcher so we don't bounce
    through the API for every page load.
    """
    sb = get_service_client()
    since = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    res = (
        sb.table("portal_events")
        .select("lead_id")
        .eq("tenant_id", tenant_id)
        .gte("occurred_at", since)
        .execute()
    )
    counts: dict[str, int] = defaultdict(int)
    for row in res.data or []:
        lid = row.get("lead_id")
        if lid:
            counts[lid] += 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"lead_id": lid, "recent_events": n} for lid, n in ranked]
