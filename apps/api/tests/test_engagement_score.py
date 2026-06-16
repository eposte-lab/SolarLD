"""Tests for ``compute_score`` — la formula v3 a 3 fasce di intenzione.

Verificano i confini che contano per il business: la sola attenzione
non rende "caldo" un lead, serve un'azione di intenzione; e i segnali
aggiunti di recente (audio, schermo intero, reply email) ora pesano.
"""

from __future__ import annotations

from src.services.engagement_service import (
    TIER_ATTENTION_CAP,
    TIER_ENGAGEMENT_CAP,
    LeadEngagementStats,
    compute_score,
)

HOT_THRESHOLD = 60


def _stats(**kw: object) -> LeadEngagementStats:
    return LeadEngagementStats(lead_id="lead", tenant_id="tenant", **kw)


def test_empty_stats_score_zero() -> None:
    assert compute_score(_stats()) == 0


def test_single_session_is_cold() -> None:
    # Aprire il portale una volta vale +10 — interesse, non "caldo".
    score = compute_score(_stats(sessions={"s1"}))
    assert score == 10
    assert score < HOT_THRESHOLD


def test_attention_tier_is_capped() -> None:
    # Tanta attenzione (sessioni, scroll, tempo) → tetto fascia.
    stats = _stats(
        sessions={"s1", "s2", "s3"},
        scroll_50=5,
        scroll_90=5,
        heartbeats=400,
    )
    assert compute_score(stats) == TIER_ATTENTION_CAP


def test_engagement_tier_is_capped() -> None:
    stats = _stats(
        video_play=4,
        video_complete=4,
        audio_on=4,
        video_fullscreen=4,
        roi_viewed=4,
        outreach_opened=True,
    )
    assert compute_score(stats) == TIER_ENGAGEMENT_CAP


def test_attention_plus_engagement_never_reaches_hot() -> None:
    # Lead che ha consumato tutto ma non ha mai alzato la mano: resta
    # sotto la soglia "caldo". Serve un'azione di intenzione.
    stats = _stats(
        sessions={"s1", "s2", "s3"},
        scroll_50=5,
        scroll_90=5,
        heartbeats=400,
        video_play=4,
        video_complete=4,
        audio_on=4,
        video_fullscreen=4,
        roi_viewed=4,
        outreach_opened=True,
    )
    score = compute_score(stats)
    assert score == TIER_ATTENTION_CAP + TIER_ENGAGEMENT_CAP
    assert score < HOT_THRESHOLD


def test_one_contact_click_makes_lead_hot() -> None:
    # Apertura portale (+10) + click "Contattaci subito" (+50) = 60, poi
    # il floor "richiesta di contatto" lo porta a 70.
    score = compute_score(_stats(sessions={"s1"}, appointment_click=1))
    assert score >= HOT_THRESHOLD


def test_appointment_requested_floors_to_hot_without_portal_activity() -> None:
    # Segnale autorevole dalla colonna appointment_requested_at: una
    # richiesta di contatto inviata rende il lead "caldo" anche senza
    # alcun evento sul portale (es. eventi fuori dalla finestra di 30
    # giorni — caso backfill dei lead che hanno già richiesto contatto).
    score = compute_score(_stats(appointment_requested=True))
    assert score >= HOT_THRESHOLD
    assert score >= 70


def test_appointment_signal_is_binary_either_source() -> None:
    # +50 una volta sola, identico che arrivi dall'evento portal
    # (appointment_click) o dalla colonna autorevole (appointment_requested).
    from_click = compute_score(_stats(sessions={"s1"}, appointment_click=1))
    from_column = compute_score(_stats(sessions={"s1"}, appointment_requested=True))
    assert from_click == from_column
    assert from_click >= 70


def test_contact_funnel_adds_engagement() -> None:
    # Aprire il form e iniziare a compilarlo sono segnali di
    # coinvolgimento crescente, sotto la soglia "caldo" da soli.
    base = compute_score(_stats(sessions={"s1"}))
    opened = compute_score(_stats(sessions={"s1"}, contact_view=1))
    started = compute_score(_stats(sessions={"s1"}, contact_view=1, contact_started=1))
    assert opened > base
    assert started > opened
    assert started < HOT_THRESHOLD


def test_audio_and_fullscreen_now_count() -> None:
    # Regressione: con la formula v2 audio_on/video_fullscreen erano
    # ignorati dal rollup notturno e i punti sparivano. Ora pesano.
    base = compute_score(_stats(sessions={"s1"}))
    with_extras = compute_score(_stats(sessions={"s1"}, audio_on=1, video_fullscreen=1))
    assert with_extras > base


def test_email_reply_click_counts_as_intent() -> None:
    score = compute_score(_stats(sessions={"s1"}, email_reply_click=1))
    assert score >= HOT_THRESHOLD


def test_score_clamped_to_100() -> None:
    stats = _stats(
        sessions={"s1", "s2", "s3"},
        scroll_50=9,
        scroll_90=9,
        heartbeats=999,
        video_play=9,
        video_complete=9,
        audio_on=9,
        video_fullscreen=9,
        roi_viewed=9,
        whatsapp_click=9,
        appointment_click=9,
        email_reply_click=9,
        bolletta_uploaded=9,
        outreach_opened=True,
        outreach_clicked=True,
    )
    assert compute_score(stats) == 100
