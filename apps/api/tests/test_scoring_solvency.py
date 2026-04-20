"""Unit tests for the solvency subscore."""

from __future__ import annotations

from src.services.scoring import solvency_score


def test_b2b_huge_revenue_scores_100() -> None:
    subject = {"type": "b2b", "yearly_revenue_cents": 10_000_000_00, "employees": 200}
    assert solvency_score(subject) == 100


def test_b2b_1m_revenue_scores_80() -> None:
    subject = {"type": "b2b", "yearly_revenue_cents": 2_500_000_00, "employees": 20}
    assert solvency_score(subject) == 80


def test_b2b_200k_revenue_scores_55() -> None:
    subject = {"type": "b2b", "yearly_revenue_cents": 400_000_00}
    assert solvency_score(subject) == 55


def test_b2b_tiny_revenue_scores_low() -> None:
    subject = {"type": "b2b", "yearly_revenue_cents": 10_000_00}
    # 10k€ — below 50k threshold
    assert solvency_score(subject) == 20


def test_b2b_no_revenue_falls_back_on_headcount() -> None:
    subject = {"type": "b2b", "yearly_revenue_cents": None, "employees": 75}
    assert solvency_score(subject) == 85

    subject = {"type": "b2b", "yearly_revenue_cents": None, "employees": 15}
    assert solvency_score(subject) == 65

    subject = {"type": "b2b", "yearly_revenue_cents": None, "employees": 1}
    assert solvency_score(subject) == 25


def test_b2b_zero_data_scores_20() -> None:
    subject = {"type": "b2b", "yearly_revenue_cents": None, "employees": None}
    assert solvency_score(subject) == 20


def test_b2c_neutral_default() -> None:
    assert solvency_score({"type": "b2c"}) == 50


def test_unknown_subject_conservative() -> None:
    assert solvency_score({"type": "unknown"}) == 35


def test_b2b_zero_revenue_treated_as_missing() -> None:
    subject = {"type": "b2b", "yearly_revenue_cents": 0, "employees": 20}
    # Revenue == 0 → falls back to headcount band (10..49 → 65)
    assert solvency_score(subject) == 65
