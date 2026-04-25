"""Pre-API consumption estimator (Phase 2 of the 9-phase pipeline).

Why this exists
---------------
The legacy pipeline pays Atoka + Solar API + AI render BEFORE knowing
whether the business has the energy load to justify a PV install.
Result: ~80 % of spend is on companies that bought €40k of grid power
in 2024 and would never recoup a 50-kWp rooftop array.

This module turns three OFFLINE signals — `ateco_code`, `revenue_eur`,
`employees` — into:
  * `estimated_yearly_kwh` (top-down, sector-driven)
  * `recommended_kwp_min`  (target PV size to cover ~70 % of load)
  * `qualifies_for_solar`  (boolean, threshold = 30 kWp recommended)

It runs in microseconds with zero network IO. The 9-phase orchestrator
calls it before any paid API to discard candidates whose load is too
small to pay back a commercial install.

Methodology
-----------
The factor `kWh / €1000 of revenue` (energy intensity per euro of
revenue) is the most stable cross-sector heuristic in EU industrial
energy statistics — far more stable than `kWh / employee` for sectors
like e-commerce or trading where revenue ≫ headcount.

Source mix:
  * GSE Italian sector benchmarks 2023 (manufacturing tiers)
  * Eurostat NRG_BAL_C 2022 (services / commerce)
  * Internal calibration on 12 closed deals (2025) for retail/HoReCa

Numbers are conservative — better to under-estimate (and let a few
borderline candidates through) than to over-estimate and skip a real
opportunity. The orchestrator can apply a x1.2 safety multiplier when
the operator runs in "aggressive" targeting mode.

Integration
-----------
* Pure function, no IO, no DB calls — safe to call inside any worker.
* Returns a dataclass; orchestrator decides whether to log/reject.
* Tests in apps/api/tests/test_consumption_estimator.py (Phase A).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# ATECO → kWh-per-€1000-revenue lookup
# ---------------------------------------------------------------------------
#
# Keys are the 2-3 digit ATECO root (the ISTAT "divisione/gruppo"). The
# orchestrator should pass `ateco_code[:3]` (or `[:2]` if 3 isn't found)
# — see `_lookup_intensity` below for the cascade logic.
#
# Values are kWh consumed per €1 000 of yearly revenue. Multiply by
# `revenue_eur / 1000` to get yearly kWh.
#
# Buckets (rough rule of thumb, used for QA when adding new codes):
#   * 30+    very intensive (steel, glass, cement, chemicals)
#   * 15-30  manufacturing (mechanical, food, paper)
#   * 5-15   commerce, hospitality, large retail, cold chain
#   * 1-5    light services, offices, professional services
#   * <1     pure software / knowledge work
ATECO_KWH_PER_1000_EUR: dict[str, float] = {
    # Heavy industry — ATECO division 24 (metallurgy), 23 (cement/glass),
    # 20 (chemicals), 17 (paper/pulp).
    "24": 38.0,   # metallurgia
    "23": 34.0,   # cemento, vetro, ceramica
    "20": 30.0,   # chimica
    "17": 26.0,   # carta
    # Food processing — energy-hungry refrigeration + ovens.
    "10": 18.0,   # industria alimentare
    "11": 16.0,   # bevande
    # Other manufacturing.
    "13": 15.0,   # tessile
    "14": 12.0,   # abbigliamento
    "15": 12.0,   # pelletteria
    "16": 14.0,   # legno
    "22": 14.0,   # gomma e plastica
    "25": 16.0,   # metalli (non basici)
    "27": 14.0,   # apparecchi elettrici
    "28": 14.0,   # macchinari
    "29": 14.0,   # autoveicoli
    "31": 11.0,   # mobili
    "32": 10.0,   # altre manifatturiere
    # Cold chain / logistics — driven by refrigerated warehouses.
    "52": 9.0,    # magazzinaggio (incl. cold storage)
    "49": 5.0,    # trasporto terrestre (depots + offices)
    # Commerce + hospitality — driven by HVAC, lighting, cooking, fridges.
    "47": 8.0,    # commercio al dettaglio
    "46": 5.0,    # commercio all'ingrosso
    "55": 14.0,   # alberghi
    "56": 12.0,   # ristoranti, bar
    # Large public-facing services.
    "85": 7.0,    # istruzione (scuole)
    "86": 11.0,   # sanità (cliniche, RSA)
    "87": 9.0,    # assistenza sociale residenziale
    "93": 8.0,    # attività sportive (palestre, piscine)
    # Office-heavy services.
    "41": 5.0,    # costruzioni edili (uffici di cantiere)
    "42": 5.0,    # ingegneria civile
    "43": 4.0,    # installazioni
    "68": 2.5,    # immobiliare
    "69": 2.0,    # studi legali / contabili
    "70": 2.0,    # consulenza direzionale
    "71": 2.5,    # ingegneria, architettura
    "73": 2.5,    # marketing, advertising
    "74": 2.0,    # altre attività professionali
    # Light services / digital.
    "62": 1.5,    # software, IT
    "63": 1.5,    # servizi informatici
    "58": 1.8,    # editoria
    "82": 2.5,    # servizi alle imprese (call centre etc.)
    # Public sector / utilities (excluded by anti-uffici filter usually,
    # kept here for completeness).
    "35": 22.0,   # energia (own consumption)
    "36": 12.0,   # acqua
    "38": 10.0,   # rifiuti
}

# Fallback when the ATECO code has no entry — uses the median of the
# table above for "general SME". Avoid 0 so the estimator doesn't
# silently reject every novel ATECO; reject on revenue/employees instead.
DEFAULT_KWH_PER_1000_EUR: float = 6.0

# Italian rooftop PV typically yields ~1 200 kWh / kWp / year (north),
# ~1 400 kWh / kWp (centre), ~1 550 kWh / kWp (south). We use a
# nation-wide conservative average for sizing decisions.
KWH_PER_KWP_PER_YEAR_AVG: float = 1_300.0

# Aim to cover ~70 % of yearly load with own production (typical
# self-consumption optimum for B2B without batteries). Going higher
# hits diminishing returns; going lower under-sizes the install and
# leaves money on the table.
SELF_CONSUMPTION_TARGET_RATIO: float = 0.70

# Below this kWp recommendation a commercial install rarely pays back
# in <7 years given current Italian incentive landscape. The 9-phase
# orchestrator uses this as the gate.
MIN_QUALIFYING_KWP: float = 30.0

# Sanity caps — guard against absurd revenue values (Atoka sometimes
# reports a holding's consolidated revenue against a tiny operating
# subsidiary, which would over-estimate consumption 100x).
MAX_REASONABLE_YEARLY_KWH: float = 50_000_000.0  # 50 GWh — a small smelter
MIN_REASONABLE_YEARLY_KWH: float = 1_000.0       # below this we treat as "no data"


@dataclass(frozen=True)
class ConsumptionEstimate:
    """Result of `stima_potenza_FV` — fully self-describing."""

    estimated_yearly_kwh: float | None
    recommended_kwp_min: float | None
    qualifies_for_solar: bool
    # Provenance for the audit trail (lead_rejection_log, dashboard).
    intensity_used: float | None
    intensity_source: str  # 'ateco_exact', 'ateco_root2', 'fallback', 'no_revenue'
    notes: str = ""


def stima_potenza_FV(azienda: dict[str, Any]) -> ConsumptionEstimate:
    """Estimate yearly consumption + recommended PV size from offline data.

    `azienda` is a permissive dict — typically from Atoka / our `subjects`
    table — with at least:
      * ateco_code: str | None
      * yearly_revenue_cents: int | None  (Atoka native unit)
      * revenue_eur: float | None         (alternative; takes precedence
                                          if both supplied)
      * employees: int | None             (used as soft cross-check, not
                                          primary signal)

    Returns a ConsumptionEstimate. Never raises — defensively handles
    missing/malformed inputs and returns `qualifies_for_solar=False`
    with `intensity_source='no_revenue'`.
    """

    revenue_eur = _resolve_revenue(azienda)
    if revenue_eur is None or revenue_eur <= 0:
        return ConsumptionEstimate(
            estimated_yearly_kwh=None,
            recommended_kwp_min=None,
            qualifies_for_solar=False,
            intensity_used=None,
            intensity_source="no_revenue",
            notes="Revenue missing or zero — cannot estimate consumption.",
        )

    intensity, source = _lookup_intensity(azienda.get("ateco_code"))

    yearly_kwh = (revenue_eur / 1000.0) * intensity

    # Sanity clamp: aberrant revenues (Atoka holdings) shouldn't drive
    # a 5-GWh estimate for a corner shop. Clamp to a believable range.
    if yearly_kwh > MAX_REASONABLE_YEARLY_KWH:
        yearly_kwh = MAX_REASONABLE_YEARLY_KWH
    if yearly_kwh < MIN_REASONABLE_YEARLY_KWH:
        return ConsumptionEstimate(
            estimated_yearly_kwh=yearly_kwh,
            recommended_kwp_min=None,
            qualifies_for_solar=False,
            intensity_used=intensity,
            intensity_source=source,
            notes="Estimated load below minimum threshold for B2B PV.",
        )

    recommended_kwp = (
        yearly_kwh * SELF_CONSUMPTION_TARGET_RATIO / KWH_PER_KWP_PER_YEAR_AVG
    )
    qualifies = recommended_kwp >= MIN_QUALIFYING_KWP

    return ConsumptionEstimate(
        estimated_yearly_kwh=round(yearly_kwh, 1),
        recommended_kwp_min=round(recommended_kwp, 1),
        qualifies_for_solar=qualifies,
        intensity_used=intensity,
        intensity_source=source,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_revenue(azienda: dict[str, Any]) -> float | None:
    """Accept either `revenue_eur` or `yearly_revenue_cents` (Atoka)."""

    val = azienda.get("revenue_eur")
    if isinstance(val, (int, float)) and val > 0:
        return float(val)

    cents = azienda.get("yearly_revenue_cents")
    if isinstance(cents, (int, float)) and cents > 0:
        return float(cents) / 100.0

    return None


def _lookup_intensity(ateco_code: Any) -> tuple[float, str]:
    """Cascade lookup: full code → 3-digit root → 2-digit root → fallback.

    Returns `(intensity, source)` where `source` is one of:
      * 'ateco_exact'   — full code matched (rare; table is keyed by root)
      * 'ateco_root2'   — first two digits matched
      * 'fallback'      — neither matched, default median used
      * 'no_ateco'      — input had no ateco_code at all
    """

    if not isinstance(ateco_code, str) or not ateco_code.strip():
        return DEFAULT_KWH_PER_1000_EUR, "no_ateco"

    code = ateco_code.strip().replace(".", "")

    # Exact + 3-char roots first (table is mostly 2-char so this is a
    # forward-compat hook for future 3-char additions like "47.11").
    for length in (len(code), 3, 2):
        root = code[:length]
        if root and root in ATECO_KWH_PER_1000_EUR:
            label = "ateco_exact" if length == len(code) else f"ateco_root{length}"
            return ATECO_KWH_PER_1000_EUR[root], label

    return DEFAULT_KWH_PER_1000_EUR, "fallback"
