"""Unit tests for the active-lead notification email builder + helpers."""

from __future__ import annotations

from src.services.active_lead_notify_service import (
    ENGAGEMENT_OR,
    _one,
    _truthy,
    build_active_lead_email,
)

PORTAL = "https://solar-ld-lead-portal.vercel.app"

_LEAD = {
    "id": "lead-1",
    "public_slug": "U4AxelcGCG4EQZALOPwdzA",
    "pipeline_status": "appointment",
    "engagement_score": 100,
    "outreach_opened_at": "2026-06-18T08:00:00+00:00",
    "outreach_clicked_at": "2026-06-18T08:05:00+00:00",
    "last_portal_event_at": "2026-06-23T13:21:00+00:00",
    "roi_data": {"yearly_savings_eur": 20699},
    "subjects": {
        "business_name": "Hotel Vietri Coast",
        "decision_maker_email": "info@hotelvietricoast.it",
        "decision_maker_phone": "+39 089 210400",
    },
    "roofs": {"provincia": "SA", "estimated_kwp": 87.2},
}


def test_subject_names_the_business() -> None:
    subject, _ = build_active_lead_email(_LEAD, PORTAL)
    assert subject == "Nuovo lead attivo: Hotel Vietri Coast"


def test_html_has_all_the_pieces() -> None:
    _, html = build_active_lead_email(_LEAD, PORTAL)
    # Identity + dossier link
    assert "Hotel Vietri Coast" in html
    assert f"{PORTAL}/dossier/U4AxelcGCG4EQZALOPwdzA" in html
    # Status badge (appointment) + engagement + plant + saving
    assert "Appuntamento richiesto" in html
    assert "Engagement 100/100" in html
    assert "87 kWp" in html
    assert "20.699/anno" in html  # Italian thousands separator
    # All contacts present + tel href stripped of spaces
    assert "mailto:info@hotelvietricoast.it" in html
    assert "tel:+39089210400" in html


def test_engaged_status_maps_to_green_label() -> None:
    lead = {**_LEAD, "pipeline_status": "engaged"}
    _, html = build_active_lead_email(lead, PORTAL)
    assert "Engaged" in html
    assert "#16A34A" in html


def test_missing_phone_and_roof_degrade_gracefully() -> None:
    lead = {
        "public_slug": "abc",
        "pipeline_status": "to_call",
        "engagement_score": 47,
        "roi_data": {},
        "subjects": {
            "business_name": "Marinauto",
            "decision_maker_email": "info@marinauto.eu",
            "decision_maker_phone": None,
        },
        "roofs": None,
    }
    subject, html = build_active_lead_email(lead, PORTAL)
    assert subject == "Nuovo lead attivo: Marinauto"
    assert "Da chiamare" in html
    assert "mailto:info@marinauto.eu" in html
    # No phone line, no crash on missing roof/kwp/eur
    assert "tel:" not in html
    assert "kWp" not in html


def test_no_slug_means_no_dossier_button() -> None:
    lead = {**_LEAD, "public_slug": None}
    _, html = build_active_lead_email(lead, PORTAL)
    assert "/dossier/" not in html
    assert "Apri il dossier" not in html


def test_visited_only_activity_line() -> None:
    lead = {**_LEAD, "outreach_opened_at": None, "outreach_clicked_at": None}
    _, html = build_active_lead_email(lead, PORTAL)
    assert "Ha visitato il dossier" in html


def test_truthy_accepts_bool_and_string() -> None:
    assert _truthy(True) is True
    assert _truthy("true") is True
    assert _truthy("True") is True
    assert _truthy(False) is False
    assert _truthy("false") is False
    assert _truthy(None) is False
    assert _truthy(1) is False  # only explicit true/"true"


def test_one_handles_object_list_and_none() -> None:
    assert _one({"a": 1}) == {"a": 1}
    assert _one([{"a": 1}, {"a": 2}]) == {"a": 1}
    assert _one([]) == {}
    assert _one(None) == {}


def test_engagement_or_excludes_blacklist_and_covers_gate() -> None:
    # Sanity: the predicate is the dashboard gate; blacklist is filtered
    # separately (.neq), so it must NOT appear in the OR group.
    assert "pipeline_status.eq.engaged" in ENGAGEMENT_OR
    assert "engagement_score.gt.0" in ENGAGEMENT_OR
    assert "blacklisted" not in ENGAGEMENT_OR
