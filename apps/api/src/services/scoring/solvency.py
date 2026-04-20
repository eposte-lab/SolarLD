"""Solvency subscore — can the subject afford a PV install?

For B2B we use the Atoka-derived financials (revenue + headcount). For
B2C we don't have personal income data, so we default to 50 (neutral) —
the Scoring agent could later overlay SOI / ISEE data if the tenant
connects those providers.

Score 0..100:
  * 100 — large enterprise (>5M€ revenue or >50 employees)
  * ~75 — mid-market
  * ~50 — small business / typical B2C household
  * ~25 — micro-enterprise, very low revenue
  *   0 — no data at all AND no way to make a guess
"""

from __future__ import annotations

from typing import Any


def solvency_score(subject: dict[str, Any]) -> int:
    subject_type = (subject.get("type") or "unknown").lower()

    if subject_type == "b2b":
        return _b2b_score(subject)
    if subject_type == "b2c":
        return 50  # neutral default
    # unknown
    return 35


def _b2b_score(subject: dict[str, Any]) -> int:
    revenue_cents = subject.get("yearly_revenue_cents")
    employees = subject.get("employees")

    revenue_eur: float | None = None
    if isinstance(revenue_cents, (int, float)) and revenue_cents > 0:
        revenue_eur = float(revenue_cents) / 100.0

    if revenue_eur is not None:
        if revenue_eur >= 5_000_000:
            return 100
        if revenue_eur >= 1_000_000:
            return 80
        if revenue_eur >= 200_000:
            return 55
        if revenue_eur >= 50_000:
            return 35
        return 20

    # Revenue missing — fall back on headcount.
    if isinstance(employees, (int, float)) and employees > 0:
        if employees >= 50:
            return 85
        if employees >= 10:
            return 65
        if employees >= 3:
            return 45
        return 25

    return 20  # B2B subject with no financials at all
