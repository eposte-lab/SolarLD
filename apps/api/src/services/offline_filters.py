"""Offline pre-API qualification filters (Phase 2 of the 9-phase pipeline).

Why
---
Before paying for Solar API (€0.05) + AI render (€0.04) + Kling video
(€0.49) + email send (€0.0004) for every candidate, we drop ~80 % of
the funnel using six purely-offline checks. Each filter is fast, pure,
and operates on data we already have from the L1 (Atoka discovery)
phase. Total cost: 0 €.

The six filters
---------------
1. **consumi**          Estimated load too low for B2B PV (delegates to
                        `consumption_estimator.stima_potenza_FV`, threshold
                        recommended_kwp >= 30).
2. **proprietà**        Atoka flags the building as rented / not owned —
                        we don't pitch leased facilities (decision-maker
                        for the install isn't the tenant). Falls back to
                        a permissive PASS when the field is absent so
                        we don't reject everyone with incomplete data.
3. **affidabilità**     Insolvency / liquidation / cease-of-business
                        flags from Atoka's `legal_status` field. Hard reject.
4. **trend**            Three-year revenue trend monotonically negative
                        AND last revenue < €500k → company is dying,
                        skip the cold pitch.
5. **sede operativa**   Mismatch between `sede_legale` and `sede_operativa`
                        when the territory configuration restricts to
                        a province/CAP — we want the OPERATIONAL site
                        in-territory, not the registered HQ in Milano
                        for a factory in Bari.
6. **anti-uffici**      Detect "office tower" / "co-working" /
                        "professional studio" patterns where the
                        rooftop isn't owned by the candidate (multi-
                        tenant building). Heuristic: ATECO ∈ {69, 70,
                        71, 73, 74} AND revenue < €5M AND building isn't
                        flagged as `proprio`.

Architecture
------------
* Each filter is a plain function `azienda → FilterResult | None`
  (None = PASS, FilterResult = REJECT with reason).
* The orchestrator `apply_offline_filters` runs all six and returns
  the first rejection (short-circuit) OR `None` (= candidate proceeds
  to Phase 3).
* Caller logs every rejection to `lead_rejection_log` (migration 0057)
  for filter-tuning analytics.
* Pure, no IO, no DB. Tests in apps/api/tests/test_offline_filters.py.

Permissive default
------------------
When data is missing the rule of thumb is **let it through** rather
than reject. Phase 3+ (online filters, MX check, etc.) will catch
most of what we'd miss; rejecting on missing data biases against
companies with sparse Atoka coverage which correlates with regional
provenance — bad both for fairness and for our success rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .consumption_estimator import (
    MIN_QUALIFYING_KWP,
    ConsumptionEstimate,
    stima_potenza_FV,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilterResult:
    """Returned by a sub-filter when the candidate is rejected."""

    rule: str                   # short stable label, used as DB key
    reason: str                 # human-friendly explanation
    rule_threshold: dict[str, Any]
    candidate_value: dict[str, Any]


# ---------------------------------------------------------------------------
# Filter 1 — consumi
# ---------------------------------------------------------------------------


def filter_consumi(azienda: dict[str, Any]) -> FilterResult | None:
    """Reject when estimated PV size < `MIN_QUALIFYING_KWP` kWp.

    Delegates the actual estimation to `consumption_estimator`. We do
    NOT reject when revenue is missing — that's a different signal
    handled by the affidabilità + trend filters, and rejecting here
    would over-prune SMEs whose Atoka revenue is partial.
    """

    est: ConsumptionEstimate = stima_potenza_FV(azienda)

    # No revenue → don't reject from this filter; let downstream phases
    # decide. Treats missing data as "permissive PASS".
    if est.intensity_source == "no_revenue":
        return None

    if est.qualifies_for_solar:
        return None

    return FilterResult(
        rule="consumi_below_threshold",
        reason=(
            f"Recommended PV size {est.recommended_kwp_min} kWp "
            f"is below B2B threshold {MIN_QUALIFYING_KWP} kWp "
            f"(estimated yearly load {est.estimated_yearly_kwh} kWh)."
        ),
        rule_threshold={
            "min_kwp": MIN_QUALIFYING_KWP,
            "intensity_source": est.intensity_source,
        },
        candidate_value={
            "estimated_yearly_kwh": est.estimated_yearly_kwh,
            "recommended_kwp_min": est.recommended_kwp_min,
            "intensity_used": est.intensity_used,
        },
    )


# ---------------------------------------------------------------------------
# Filter 2 — proprietà (building ownership)
# ---------------------------------------------------------------------------

# Atoka / our internal taxonomy. Adapt as we learn the actual field
# values populated by the discovery agent.
_OWNERSHIP_REJECT_VALUES = {
    "affittato",
    "locazione",
    "leased",
    "rented",
    "tenant",
    "comodato",
}


def filter_proprieta(azienda: dict[str, Any]) -> FilterResult | None:
    """Reject when the building is explicitly flagged as not owned.

    Permissive default: when the field is absent we PASS — older Atoka
    rows often don't carry ownership data and rejecting on absence
    would wipe the funnel.

    Three input shapes are accepted (post-Atoka tutto-in-uno per
    ADR-002):
      • ``building_ownership`` / ``proprieta_immobile`` strings
        — historical free-form labels ("affittato", "leased", …).
      • ``proprieta_immobile_sede`` boolean — the native flag
        delivered by the Atoka all-in-one endpoint. ``False`` is an
        explicit "rents the office" signal and is rejected; ``True``
        passes.

    Order: boolean check first (cheapest, most authoritative) so the
    string fallback only runs for legacy rows.
    """

    # 1. Native Atoka boolean — strict reject when explicitly False.
    boolean_flag = azienda.get("proprieta_immobile_sede")
    if isinstance(boolean_flag, bool):
        if not boolean_flag:
            return FilterResult(
                rule="building_not_owned",
                reason=(
                    "Atoka flagged property as not owned — install "
                    "decision sits with landlord."
                ),
                rule_threshold={"required": "proprieta_immobile_sede=true"},
                candidate_value={"proprieta_immobile_sede": False},
            )
        # True → no further checks needed.
        return None

    # 2. Legacy string fields — keep the reject-set semantics.
    raw = azienda.get("building_ownership") or azienda.get("proprieta_immobile")
    if raw is None:
        return None

    val = str(raw).strip().lower()
    if val in _OWNERSHIP_REJECT_VALUES:
        return FilterResult(
            rule="building_not_owned",
            reason="Building is rented / leased — install decision sits with landlord.",
            rule_threshold={"reject_values": sorted(_OWNERSHIP_REJECT_VALUES)},
            candidate_value={"building_ownership": val},
        )
    return None


# ---------------------------------------------------------------------------
# Filter 3 — affidabilità (financial distress / dead company)
# ---------------------------------------------------------------------------

_DISTRESS_LEGAL_STATUSES = {
    "fallimento",
    "fallita",
    "liquidazione",
    "in liquidazione",
    "concordato preventivo",
    "amministrazione straordinaria",
    "cancellata",
    "cessata",
    "inattiva",
    "sospesa",
    "scioglimento",
}


def filter_affidabilita(azienda: dict[str, Any]) -> FilterResult | None:
    """Hard reject when the legal status indicates the company is dead.

    No permissive fallback here — these statuses only appear when Atoka
    actively flagged the company. If the field is empty we PASS.
    """

    raw = azienda.get("legal_status") or azienda.get("stato_attivita")
    if raw is None:
        return None

    val = str(raw).strip().lower()
    for needle in _DISTRESS_LEGAL_STATUSES:
        if needle in val:
            return FilterResult(
                rule="company_in_distress",
                reason=f"Legal status indicates distress / cessation: '{val}'.",
                rule_threshold={"distress_keywords": sorted(_DISTRESS_LEGAL_STATUSES)},
                candidate_value={"legal_status": val},
            )
    return None


# ---------------------------------------------------------------------------
# Filter 4 — trend (3-year monotonic revenue decline + small size)
# ---------------------------------------------------------------------------

_TREND_REVENUE_FLOOR_EUR: float = 500_000.0


def filter_trend(azienda: dict[str, Any]) -> FilterResult | None:
    """Reject companies with monotonic 3-year revenue decline AND
    last-year revenue below €500k.

    Both conditions are required: a big company with declining revenue
    might still need to invest in cost cuts (=PV); a tiny company with
    a normal year-over-year fluctuation shouldn't be wiped out.

    `revenue_history_eur` is expected as a list of three floats
    `[year_n_minus_2, year_n_minus_1, year_n]` (most recent last).
    Permissive when missing.
    """

    history = azienda.get("revenue_history_eur") or azienda.get("revenue_3y")
    if not isinstance(history, (list, tuple)) or len(history) < 3:
        return None

    try:
        a, b, c = float(history[-3]), float(history[-2]), float(history[-1])
    except (TypeError, ValueError):
        return None

    monotonic_decline = a > b > c and a > 0
    too_small = c < _TREND_REVENUE_FLOOR_EUR

    if monotonic_decline and too_small:
        return FilterResult(
            rule="revenue_trend_declining_and_small",
            reason=(
                f"Revenue declined {a:.0f} → {b:.0f} → {c:.0f} EUR over 3 years "
                f"and last year is below €{_TREND_REVENUE_FLOOR_EUR:.0f}."
            ),
            rule_threshold={"min_last_year_eur": _TREND_REVENUE_FLOOR_EUR},
            candidate_value={"revenue_history_eur": [a, b, c]},
        )
    return None


# ---------------------------------------------------------------------------
# Filter 5 — sede operativa (operational site in-territory)
# ---------------------------------------------------------------------------


def filter_sede_operativa(
    azienda: dict[str, Any],
    territory: dict[str, Any] | None,
) -> FilterResult | None:
    """When tenant's territory restricts to specific provinces/CAPs,
    the OPERATIONAL site (sede_operativa) — not the legal HQ — must
    fall inside it.

    `territory` is the resolved tenant territory config:
      {
        "provinces": ["NA", "SA", ...] | None,
        "caps":      ["80017", ...]    | None,
      }

    Permissive when no territory restriction is configured.
    Permissive when sede_operativa is missing — the Hunter funnel L4
    will handle the address-precision rejection later.
    """

    if territory is None:
        return None

    allowed_provinces = {
        p.upper() for p in (territory.get("provinces") or []) if isinstance(p, str)
    }
    allowed_caps = {
        c.strip() for c in (territory.get("caps") or []) if isinstance(c, str)
    }

    if not allowed_provinces and not allowed_caps:
        return None  # no restriction

    op_province = (azienda.get("sede_operativa_province") or azienda.get("hq_province"))
    op_cap = azienda.get("sede_operativa_cap") or azienda.get("hq_cap")

    if op_province is None and op_cap is None:
        return None  # missing data — permissive

    province_ok = (
        not allowed_provinces
        or (isinstance(op_province, str) and op_province.upper() in allowed_provinces)
    )
    cap_ok = (
        not allowed_caps
        or (isinstance(op_cap, str) and op_cap.strip() in allowed_caps)
    )

    if province_ok and cap_ok:
        return None

    return FilterResult(
        rule="sede_operativa_out_of_territory",
        reason=(
            "Operational site sits outside the tenant's contracted territory."
        ),
        rule_threshold={
            "allowed_provinces": sorted(allowed_provinces),
            "allowed_caps": sorted(allowed_caps),
        },
        candidate_value={
            "sede_operativa_province": op_province,
            "sede_operativa_cap": op_cap,
        },
    )


# ---------------------------------------------------------------------------
# Filter 6 — anti-uffici (office-only tenants in multi-tenant buildings)
# ---------------------------------------------------------------------------

# ATECO 2-digit roots that are typically office-only services.
_OFFICE_ATECO_ROOTS = {"69", "70", "71", "73", "74", "78"}

# Below this revenue an office-only ATECO is almost certainly a
# multi-tenant studio (not a building owner).
_OFFICE_REVENUE_FLOOR_EUR: float = 5_000_000.0


def filter_anti_uffici(azienda: dict[str, Any]) -> FilterResult | None:
    """Reject when the candidate looks like a small professional studio
    in a multi-tenant office building (ATECO ∈ services, revenue small,
    no proprietà flag set).

    Permissive when ATECO is missing or building flagged 'proprio'.
    """

    ateco = azienda.get("ateco_code")
    if not isinstance(ateco, str) or not ateco.strip():
        return None

    root = ateco.strip().replace(".", "")[:2]
    if root not in _OFFICE_ATECO_ROOTS:
        return None

    # Building explicitly owned → not multi-tenant, let it through.
    ownership = (
        azienda.get("building_ownership") or azienda.get("proprieta_immobile")
    )
    if isinstance(ownership, str) and ownership.strip().lower() in {
        "proprio",
        "owned",
        "proprieta",
    }:
        return None

    revenue_eur = _resolve_revenue_eur(azienda)
    if revenue_eur is None:
        return None  # missing — permissive
    if revenue_eur >= _OFFICE_REVENUE_FLOOR_EUR:
        return None  # large enough to plausibly own its building

    return FilterResult(
        rule="office_only_multi_tenant",
        reason=(
            f"ATECO {ateco} (services) with revenue €{revenue_eur:,.0f} suggests "
            f"a small studio in a multi-tenant building — owner of rooftop "
            f"is not the candidate."
        ),
        rule_threshold={
            "office_ateco_roots": sorted(_OFFICE_ATECO_ROOTS),
            "min_revenue_eur": _OFFICE_REVENUE_FLOOR_EUR,
        },
        candidate_value={"ateco_code": ateco, "revenue_eur": revenue_eur},
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


# Order matters: cheaper / more decisive filters first.
# `filter_sede_operativa` needs an extra `territory` argument so it's
# wrapped at call time below — the registry stores plain `azienda → ?`
# callables.
_PURE_FILTERS: list[tuple[str, Callable[[dict[str, Any]], FilterResult | None]]] = [
    ("affidabilita", filter_affidabilita),
    ("trend",        filter_trend),
    ("proprieta",    filter_proprieta),
    ("anti_uffici",  filter_anti_uffici),
    ("consumi",      filter_consumi),
]


def apply_offline_filters(
    azienda: dict[str, Any],
    *,
    territory: dict[str, Any] | None = None,
) -> FilterResult | None:
    """Run all six offline filters in order; return the FIRST rejection.

    Returns ``None`` when the candidate passes every filter (= proceed
    to Phase 3 of the orchestrator).

    The caller is responsible for persisting the rejection to
    ``lead_rejection_log`` when this function returns a non-None
    `FilterResult`. Keeping the persistence outside the pure module
    keeps it test-friendly and side-effect-free.
    """

    # Sede operativa first — rejecting on territory mismatch is the
    # cheapest / most decisive check (avoids running consumption math
    # on candidates the tenant can't legally serve anyway).
    territory_result = filter_sede_operativa(azienda, territory)
    if territory_result is not None:
        return territory_result

    for _label, fn in _PURE_FILTERS:
        result = fn(azienda)
        if result is not None:
            return result

    return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_revenue_eur(azienda: dict[str, Any]) -> float | None:
    val = azienda.get("revenue_eur")
    if isinstance(val, (int, float)) and val > 0:
        return float(val)
    cents = azienda.get("yearly_revenue_cents")
    if isinstance(cents, (int, float)) and cents > 0:
        return float(cents) / 100.0
    return None
