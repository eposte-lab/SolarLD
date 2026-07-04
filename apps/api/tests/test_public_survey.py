"""Dossier survey widget — the progressive quiz that captures a hot phone.

Covers the submission model + that the two public endpoints are wired to write
the self-provided phone (source='survey') and emit distinct tracking events
(survey_step / survey_completed / phone_captured), rate-limited per slug.
"""

from __future__ import annotations

import inspect

from src.routes import public
from src.routes.public import SurveySubmission


def test_survey_submission_model_defaults() -> None:
    s = SurveySubmission()
    assert s.answers == {}
    assert s.phone is None

    s2 = SurveySubmission(answers={"interesse": "bolletta"}, phone="+39 333 1234567")
    assert s2.answers["interesse"] == "bolletta"
    assert s2.phone is not None
    assert s2.phone.startswith("+39")


def test_survey_endpoints_wired_and_events() -> None:
    src = inspect.getsource(public)
    # Two public, slug-gated endpoints: completion + per-step tracking.
    assert '@router.post("/lead/{slug}/survey")' in src
    assert '@router.post("/lead/{slug}/survey/step")' in src
    # The completion writes the self-provided phone with clear provenance —
    # the hottest contact we can get (beats scraped/Atoka).
    assert '"decision_maker_phone_source": "survey"' in src
    # Distinct tracking events (NOT conflated with the plain dossier visit).
    assert 'event_type="lead.survey_completed"' in src
    assert 'event_type="lead.survey_step"' in src
    assert 'event_type="lead.phone_captured"' in src
    # Public write → rate-limited per slug like the other public endpoints.
    assert "_survey_rate_allows" in src
