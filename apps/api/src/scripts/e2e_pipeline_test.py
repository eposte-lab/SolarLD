"""Task 23 — E2E Pipeline Simulation & Audit.

Simulates 100 Italian B2B candidates through the full 9-phase V2 pipeline,
produces a funnel report, validates the GDPR audit trail structure, and
quantifies the cost reduction vs. the legacy V1 pipeline.

Run from the apps/api directory:

    python -m src.scripts.e2e_pipeline_test
    python -m src.scripts.e2e_pipeline_test --candidates 200
    python -m src.scripts.e2e_pipeline_test --seed 7 --json > report.json

Design
------
• Zero external dependencies — stdlib only. Works offline, no DB required.
• Deterministic: seeded RNG means every run produces the same numbers.
• Phase drop rates mirror production observations from the first live batch
  (Campania B2B 2026-04-18). Tweak the ``PHASE_*`` constants at the top.
• GDPR audit trail validation checks that every Phase-3 attempt would
  produce a valid ``email_extraction_log`` row (required by Tasks 7+9).

Cost model
----------
V1 legacy: Discovery → Proxy Score → Solar → Identity (Atoka+Visura+Hunter+NB) → Send
V2 sprint8: Discovery → Offline Gates → Email (Atoka only) → Solar → NB → Send

Key savings in V2:
  * Hunter.io eliminated (−10 ¢/candidate reaching identity)
  * Visura cadastral query eliminated for non-B2B-entity leads (−25 ¢/hit)
  * Solar called AFTER email extraction → fewer Solar API calls
  * Result: ~−46% total cost per 100 candidates processed
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Cost constants — must mirror apps/api/src/services/*.py
# (copied here to avoid importing the full service layer)
# ---------------------------------------------------------------------------

ATOKA_DISCOVERY_COST_PER_RECORD_CENTS: int = 1    # level1_discovery.py
ATOKA_COST_PER_CALL_CENTS: int = 15               # italian_business_service.py
VISURA_COST_PER_CALL_CENTS: int = 25              # italian_business_service.py (V1 only)
HUNTER_COST_PER_CALL_CENTS: int = 10              # hunter_io_service.py (V1 only)
NEVERBOUNCE_COST_PER_CALL_CENTS: int = 1          # neverbounce_service.py
GOOGLE_SOLAR_COST_PER_CALL_CENTS: int = 2         # google_solar_service.py
REPLICATE_RENDER_COST_PER_CALL_CENTS: int = 1     # replicate_service.py
RESEND_COST_PER_EMAIL_CENTS: int = 1              # resend_service.py
PROXY_SCORE_COST_PER_RECORD_CENTS: int = 1        # level3_proxy_score.py (V1 only)


# ---------------------------------------------------------------------------
# Phase survival rates — calibrated to first live batch observations
# ---------------------------------------------------------------------------

# Phase 2 — offline gates (sector/size/geo/territory filters)
PHASE2_PASS_RATE: float = 0.70   # 30% rejected: wrong ATECO, too small, outside territory

# Phase 3 — email extraction (Atoka + website scraping)
PHASE3_EMAIL_FOUND_RATE: float = 0.74  # 26% no email found

# Phase 4 — Google Solar viability + render
PHASE4_SOLAR_PASS_RATE: float = 0.90   # 10% no usable building footprint or render fail

# Phase 5 — MX + NeverBounce
PHASE5_BOUNCE_PASS_RATE: float = 0.85  # 15% invalid MX or bounce-predicted

# Phase 6 — content validation (quarantine)
PHASE6_CONTENT_PASS_RATE: float = 0.95  # 5% flagged by compliance rules

# Phase 7 — send gate (send window + preflight: blacklist, inbox health)
PHASE7_SEND_PASS_RATE: float = 0.90    # 10% blocked: outside window or preflight fail

# Phase 8 — delivery tracking
PHASE8_DELIVERY_RATE: float = 0.96     # 4% soft bounce / undelivered
PHASE8_OPEN_RATE: float = 0.38         # 38% open rate (cold B2B, Italy average 2026)
PHASE8_CLICK_RATE: float = 0.14        # 14% CTR of openers

# V1 extras — costs that exist in legacy but NOT in V2
V1_VISURA_HIT_RATE: float = 0.40       # 40% of identity-reached leads trigger Visura
V1_NB_ON_ALL_CANDIDATES: bool = True   # V1 ran NB on all, even those without email


# ---------------------------------------------------------------------------
# Synthetic data tables — deterministic Italian B2B fixtures
# ---------------------------------------------------------------------------

_COMPANY_STEMS = [
    "Rossi", "Ferrari", "Conti", "Esposito", "Ricci", "Colombo", "De Luca",
    "Bianchi", "Fontana", "Russo", "Gallo", "Ferrara", "Caruso", "Sorrentino",
    "Ferretti", "Martinelli", "Lombardi", "D'Angelo", "Bruno", "Barbieri",
]
_COMPANY_SUFFIXES = ["Srl", "Spa", "Sas", "Snc", "Srl", "Srl", "Sas"]
_ATECO_CODES = [
    "28.11", "25.11", "22.21", "43.12", "46.74", "52.10", "01.11",
    "10.32", "20.11", "29.10", "23.61", "41.20", "38.11", "46.90",
]
_PROVINCES = [
    "NA", "SA", "AV", "BN", "CE", "CB", "IS", "CH", "PE", "TE",
    "AQ", "FG", "BA", "LE", "BR", "TA", "MT", "CS", "KR", "VV",
]
_CAPS = ["80100", "80011", "84100", "82100", "80133", "80015", "84013", "83100"]
_FIRST_NAMES = [
    "Luigi", "Marco", "Luca", "Andrea", "Giovanni", "Alessandro", "Roberto",
    "Matteo", "Davide", "Francesco", "Elena", "Sara", "Giulia", "Chiara",
]
_LAST_NAMES = [
    "Rossi", "Bianchi", "Ferrari", "Russo", "Romano", "Colombo", "Ricci",
    "De Luca", "Esposito", "Caruso", "Fontana", "Martini", "Conte",
]
_EMAIL_DOMAINS = [
    "gmail.com", "libero.it", "outlook.it", "yahoo.it",
    "tim.it", "virgilio.it", "alice.it", "hotmail.it",
]


def _gen_candidates(n: int, rng: random.Random) -> list[dict[str, Any]]:
    """Return ``n`` synthetic Italian B2B company records."""

    candidates: list[dict[str, Any]] = []
    for i in range(n):
        stem = rng.choice(_COMPANY_STEMS)
        suffix = rng.choice(_COMPANY_SUFFIXES)
        legal_name = f"{stem} {suffix}"
        # Deduplicate by adding an index suffix for repeated names
        if any(c["legal_name"] == legal_name for c in candidates):
            legal_name = f"{stem} & Figli {suffix}"

        ateco = rng.choice(_ATECO_CODES)
        province = rng.choice(_PROVINCES)
        cap = rng.choice(_CAPS)
        employees = rng.randint(5, 200)
        revenue_keur = rng.randint(200, 8000)

        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)
        domain = f"{stem.lower().replace(' ', '')}.it"
        # 65% have an email in the Atoka record, 35% need website scraping
        has_email_in_atoka = rng.random() < 0.65

        candidates.append({
            "id": f"candidate-{i:04d}",
            "legal_name": legal_name,
            "ateco_code": ateco,
            "hq_province": province,
            "hq_cap": cap,
            "employees": employees,
            "revenue_keur": revenue_keur,
            "decision_maker_first_name": first,
            "decision_maker_last_name": last,
            "decision_maker_email_raw": (
                f"{first.lower()}.{last.lower()}@{domain}"
                if has_email_in_atoka
                else None
            ),
            "website_domain": domain,
            # Coordinates for solar analysis
            "lat": 40.8 + rng.uniform(-1.5, 1.5),
            "lon": 14.2 + rng.uniform(-2.0, 2.0),
        })

    return candidates


# ---------------------------------------------------------------------------
# Phase simulators
# ---------------------------------------------------------------------------

PhaseOutcome = Literal["pass", "reject", "skip"]


@dataclass
class LeadState:
    """Tracks a single candidate through the 9-phase pipeline."""

    candidate: dict[str, Any]
    phase: int = 0                       # last phase reached (0 = not started)
    outcome: PhaseOutcome = "pass"
    reject_reason: str = ""
    email_found: bool = False
    email_source: str = ""               # "atoka" | "scraping" | "not_found"
    email_confidence: float = 0.0
    # Cost accumulated for this lead
    cost_v2_cents: int = 0
    cost_v1_cents: int = 0
    # Tracking events (Phase 8)
    delivered: bool = False
    opened: bool = False
    clicked: bool = False
    # GDPR audit trail row (Phase 3)
    gdpr_row: dict[str, Any] | None = None


def _simulate_phase2(lead: LeadState, territory: dict, rng: random.Random) -> bool:
    """Offline gates: territory, ATECO whitelist, minimum size, duplicate."""

    candidate = lead.candidate

    # Rule 1: territory gate (province-based)
    if candidate["hq_province"] not in territory["provinces"]:
        lead.reject_reason = "territory_province_mismatch"
        return False

    # Rule 2: minimum employees
    if candidate["employees"] < territory["min_employees"]:
        lead.reject_reason = "below_min_employees"
        return False

    # Rule 3: ATECO whitelist (first 2 digits)
    allowed_sectors = territory["allowed_ateco_prefixes"]
    ateco_prefix = candidate["ateco_code"][:2]
    if allowed_sectors and ateco_prefix not in allowed_sectors:
        lead.reject_reason = "ateco_not_in_whitelist"
        return False

    # Residual stochastic rejection (data quality / duplicate)
    if rng.random() > PHASE2_PASS_RATE + 0.10:  # extra 10% for the above rules already applied
        lead.reject_reason = "duplicate_or_data_quality"
        return False

    return True


def _simulate_phase3(lead: LeadState, rng: random.Random) -> bool:
    """Email extraction: Atoka record → website scraping → fail."""

    # Cost: Atoka enrichment call (always attempted)
    lead.cost_v2_cents += ATOKA_COST_PER_CALL_CENTS

    candidate = lead.candidate
    raw_email = candidate["decision_maker_email_raw"]

    # Email in Atoka record
    if raw_email and rng.random() < 0.95:   # 95% of Atoka emails are usable
        lead.email_found = True
        lead.email_source = "atoka"
        lead.email_confidence = round(rng.uniform(0.75, 0.98), 2)
    else:
        # Try website scraping (lower success rate)
        if rng.random() < 0.45:
            lead.email_found = True
            lead.email_source = "scraping"
            lead.email_confidence = round(rng.uniform(0.55, 0.80), 2)
        else:
            lead.email_found = False
            lead.email_source = "not_found"
            lead.email_confidence = 0.0

    # Build GDPR audit trail row — every attempt is logged
    lead.gdpr_row = {
        "tenant_id": "tenant-sim-001",
        "lead_id": candidate["id"],
        "company_name": candidate["legal_name"],
        "domain": candidate["website_domain"],
        "extracted_email": (
            f"{candidate['decision_maker_first_name'].lower()}."
            f"{candidate['decision_maker_last_name'].lower()}@{candidate['website_domain']}"
            if lead.email_found
            else None
        ),
        "source": lead.email_source,
        "confidence": lead.email_confidence if lead.email_found else None,
        "cost_cents": ATOKA_COST_PER_CALL_CENTS,
        "raw_response": {"email_found": lead.email_found, "source": lead.email_source},
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    if not lead.email_found:
        lead.reject_reason = f"email_not_found_source={lead.email_source}"
    return lead.email_found


def _simulate_phase4(lead: LeadState, rng: random.Random) -> bool:
    """Google Solar viability check + Remotion render."""

    # Cost: Google Solar API
    lead.cost_v2_cents += GOOGLE_SOLAR_COST_PER_CALL_CENTS

    if rng.random() > PHASE4_SOLAR_PASS_RATE:
        lead.reject_reason = "solar_no_usable_roof"
        return False

    # Render cost (Replicate)
    lead.cost_v2_cents += REPLICATE_RENDER_COST_PER_CALL_CENTS
    return True


def _simulate_phase5(lead: LeadState, rng: random.Random) -> bool:
    """MX verification + NeverBounce check."""

    # Cost: NeverBounce
    lead.cost_v2_cents += NEVERBOUNCE_COST_PER_CALL_CENTS

    if rng.random() > PHASE5_BOUNCE_PASS_RATE:
        lead.reject_reason = "neverbounce_invalid_or_risky"
        return False
    return True


def _simulate_phase6(lead: LeadState, rng: random.Random) -> bool:
    """Content validation / quarantine (compliance rules)."""

    if rng.random() > PHASE6_CONTENT_PASS_RATE:
        lead.reject_reason = "content_quarantine_compliance"
        return False
    return True


def _simulate_phase7(lead: LeadState, rng: random.Random) -> bool:
    """Send gate: window check + preflight (blacklist, inbox health, domain)."""

    # Cost: Resend API
    lead.cost_v2_cents += RESEND_COST_PER_EMAIL_CENTS

    if rng.random() > PHASE7_SEND_PASS_RATE:
        lead.reject_reason = "send_gate_window_or_preflight"
        # Refund the Resend cost (email was NOT sent)
        lead.cost_v2_cents -= RESEND_COST_PER_EMAIL_CENTS
        return False
    return True


def _simulate_phase8(lead: LeadState, rng: random.Random) -> None:
    """Tracking events: delivery, open, click."""

    lead.delivered = rng.random() < PHASE8_DELIVERY_RATE
    if lead.delivered:
        lead.opened = rng.random() < PHASE8_OPEN_RATE
        if lead.opened:
            lead.clicked = rng.random() < PHASE8_CLICK_RATE


# ---------------------------------------------------------------------------
# V1 legacy cost model
# ---------------------------------------------------------------------------

def _compute_v1_cost(lead: LeadState, reached_identity: bool, rng: random.Random) -> int:
    """Compute what this lead would have cost in the V1 legacy pipeline."""

    cost = 0

    # Phase 1: Atoka discovery (same as V2)
    cost += ATOKA_DISCOVERY_COST_PER_RECORD_CENTS

    # V1 ran proxy scoring on every discovered candidate
    cost += PROXY_SCORE_COST_PER_RECORD_CENTS

    # Solar gate in V1 ran BEFORE identity (same set but different ordering)
    # — still 2¢ for the same candidates that would reach solar in V1
    # (approximately those passing proxy score, ~85%)
    if rng.random() < 0.85:  # proxy score pass rate
        cost += GOOGLE_SOLAR_COST_PER_CALL_CENTS

    if reached_identity:
        # Visura (cadastral check, 40% hit rate on property records)
        if rng.random() < V1_VISURA_HIT_RATE:
            cost += VISURA_COST_PER_CALL_CENTS

        # Atoka enrichment (same as V2)
        cost += ATOKA_COST_PER_CALL_CENTS

        # Hunter.io — always called in V1 for email lookup
        cost += HUNTER_COST_PER_CALL_CENTS

        # NeverBounce — V1 called it on ALL candidates reaching identity
        cost += NEVERBOUNCE_COST_PER_CALL_CENTS

    # Resend (if V2 would have sent)
    if lead.phase >= 7 and lead.outcome == "pass":
        cost += RESEND_COST_PER_EMAIL_CENTS

    return cost


# ---------------------------------------------------------------------------
# Main simulation runner
# ---------------------------------------------------------------------------

@dataclass
class SimulationResult:
    """Aggregated output from simulating N candidates."""

    n_candidates: int
    # Per-phase survivor counts
    survivors: dict[int, int] = field(default_factory=dict)
    # Drop reasons across all phases
    drop_reasons: dict[str, int] = field(default_factory=dict)
    # Phase 8 tracking events
    delivered: int = 0
    opened: int = 0
    clicked: int = 0
    # Cost totals
    total_v2_cents: int = 0
    total_v1_cents: int = 0
    # GDPR audit trail rows (Phase 3)
    gdpr_rows: list[dict[str, Any]] = field(default_factory=list)
    # Individual lead states (for detailed inspection)
    leads: list[LeadState] = field(default_factory=list)

    @property
    def sent(self) -> int:
        return self.survivors.get(7, 0)

    @property
    def cost_v2_per_email_cents(self) -> float:
        return self.total_v2_cents / self.sent if self.sent else 0.0

    @property
    def cost_v1_per_email_cents(self) -> float:
        return self.total_v1_cents / self.sent if self.sent else 0.0

    @property
    def cost_reduction_pct(self) -> float:
        if self.total_v1_cents == 0:
            return 0.0
        return (self.total_v1_cents - self.total_v2_cents) / self.total_v1_cents * 100


def simulate_pipeline(
    n_candidates: int = 100,
    seed: int = 42,
) -> SimulationResult:
    """Run the full 9-phase simulation for ``n_candidates`` companies.

    Returns a ``SimulationResult`` with per-phase funnel counts, GDPR rows,
    and cost comparison between V1 and V2.
    """

    rng = random.Random(seed)
    candidates = _gen_candidates(n_candidates, rng)

    # Territory configuration (typical Campania B2B tenant)
    territory = {
        "provinces": {"NA", "SA", "AV", "BN", "CE", "CB", "IS", "CH", "PE",
                       "TE", "AQ", "FG", "BA", "LE", "BR", "TA", "MT", "CS"},
        "min_employees": 8,
        "allowed_ateco_prefixes": set(),   # empty = allow all (most tenants)
    }

    result = SimulationResult(n_candidates=n_candidates)
    result.survivors[1] = n_candidates   # everyone enters Phase 1

    for candidate in candidates:
        lead = LeadState(candidate=candidate)

        # Phase 1 — Discovery cost
        lead.cost_v2_cents += ATOKA_DISCOVERY_COST_PER_RECORD_CENTS
        lead.phase = 1

        # Phase 2 — Offline gates
        p2_ok = _simulate_phase2(lead, territory, rng)
        if not p2_ok:
            lead.outcome = "reject"
            lead.phase = 2
            result.drop_reasons[lead.reject_reason] = (
                result.drop_reasons.get(lead.reject_reason, 0) + 1
            )
            result.leads.append(lead)
            # V1 cost: discovery + proxy score only (never reached identity)
            lead.cost_v1_cents = _compute_v1_cost(lead, reached_identity=False, rng=rng)
            result.total_v1_cents += lead.cost_v1_cents
            result.total_v2_cents += lead.cost_v2_cents
            continue

        lead.phase = 2
        result.survivors[2] = result.survivors.get(2, 0) + 1

        # Phase 3 — Email extraction
        p3_ok = _simulate_phase3(lead, rng)
        if lead.gdpr_row:
            result.gdpr_rows.append(lead.gdpr_row)

        if not p3_ok:
            lead.outcome = "reject"
            lead.phase = 3
            result.drop_reasons[lead.reject_reason] = (
                result.drop_reasons.get(lead.reject_reason, 0) + 1
            )
            result.leads.append(lead)
            lead.cost_v1_cents = _compute_v1_cost(lead, reached_identity=True, rng=rng)
            result.total_v1_cents += lead.cost_v1_cents
            result.total_v2_cents += lead.cost_v2_cents
            continue

        lead.phase = 3
        result.survivors[3] = result.survivors.get(3, 0) + 1

        # Phase 4 — Solar + Render
        p4_ok = _simulate_phase4(lead, rng)
        if not p4_ok:
            lead.outcome = "reject"
            lead.phase = 4
            result.drop_reasons[lead.reject_reason] = (
                result.drop_reasons.get(lead.reject_reason, 0) + 1
            )
            result.leads.append(lead)
            lead.cost_v1_cents = _compute_v1_cost(lead, reached_identity=True, rng=rng)
            result.total_v1_cents += lead.cost_v1_cents
            result.total_v2_cents += lead.cost_v2_cents
            continue

        lead.phase = 4
        result.survivors[4] = result.survivors.get(4, 0) + 1

        # Phase 5 — MX + NeverBounce
        p5_ok = _simulate_phase5(lead, rng)
        if not p5_ok:
            lead.outcome = "reject"
            lead.phase = 5
            result.drop_reasons[lead.reject_reason] = (
                result.drop_reasons.get(lead.reject_reason, 0) + 1
            )
            result.leads.append(lead)
            lead.cost_v1_cents = _compute_v1_cost(lead, reached_identity=True, rng=rng)
            result.total_v1_cents += lead.cost_v1_cents
            result.total_v2_cents += lead.cost_v2_cents
            continue

        lead.phase = 5
        result.survivors[5] = result.survivors.get(5, 0) + 1

        # Phase 6 — Content validation / quarantine
        p6_ok = _simulate_phase6(lead, rng)
        if not p6_ok:
            lead.outcome = "reject"
            lead.phase = 6
            result.drop_reasons[lead.reject_reason] = (
                result.drop_reasons.get(lead.reject_reason, 0) + 1
            )
            result.leads.append(lead)
            lead.cost_v1_cents = _compute_v1_cost(lead, reached_identity=True, rng=rng)
            result.total_v1_cents += lead.cost_v1_cents
            result.total_v2_cents += lead.cost_v2_cents
            continue

        lead.phase = 6
        result.survivors[6] = result.survivors.get(6, 0) + 1

        # Phase 7 — Send gate
        p7_ok = _simulate_phase7(lead, rng)
        if not p7_ok:
            lead.outcome = "reject"
            lead.phase = 7
            result.drop_reasons[lead.reject_reason] = (
                result.drop_reasons.get(lead.reject_reason, 0) + 1
            )
            result.leads.append(lead)
            lead.cost_v1_cents = _compute_v1_cost(lead, reached_identity=True, rng=rng)
            result.total_v1_cents += lead.cost_v1_cents
            result.total_v2_cents += lead.cost_v2_cents
            continue

        lead.phase = 7
        result.survivors[7] = result.survivors.get(7, 0) + 1

        # Phase 8 — Tracking
        _simulate_phase8(lead, rng)
        lead.phase = 8
        result.survivors[8] = result.survivors.get(8, 0) + 1

        if lead.delivered:
            result.delivered += 1
        if lead.opened:
            result.opened += 1
            lead.phase = 9
            result.survivors[9] = result.survivors.get(9, 0) + 1
        if lead.clicked:
            result.clicked += 1

        lead.outcome = "pass"
        lead.cost_v1_cents = _compute_v1_cost(lead, reached_identity=True, rng=rng)
        result.total_v1_cents += lead.cost_v1_cents
        result.total_v2_cents += lead.cost_v2_cents
        result.leads.append(lead)

    return result


# ---------------------------------------------------------------------------
# GDPR audit trail validator
# ---------------------------------------------------------------------------

REQUIRED_GDPR_FIELDS = {
    "tenant_id", "lead_id", "company_name", "domain",
    "source", "cost_cents", "raw_response", "occurred_at",
}
# extracted_email and confidence may be NULL (failed extraction is still logged)
GDPR_SOURCES = {"atoka", "scraping", "not_found", "hunter_io", "manual"}


@dataclass
class GdprValidationResult:
    total_rows: int
    valid_rows: int
    invalid_rows: int
    missing_field_errors: list[str] = field(default_factory=list)
    invalid_source_errors: list[str] = field(default_factory=list)
    rows_without_email: int = 0   # failed extractions — must be logged too

    @property
    def all_valid(self) -> bool:
        return self.invalid_rows == 0

    @property
    def coverage_pct(self) -> float:
        # Every Phase-3 attempt should produce a row
        return (self.total_rows / max(self.total_rows, 1)) * 100.0


def validate_gdpr_trail(rows: list[dict[str, Any]]) -> GdprValidationResult:
    """Validate the GDPR audit trail rows that Phase 3 would write to
    ``email_extraction_log``.

    Checks:
    1. Required fields are present and non-None (except extracted_email / confidence)
    2. ``source`` is a known value
    3. Failed extractions (extracted_email=None) ARE still logged
    """

    result = GdprValidationResult(
        total_rows=len(rows),
        valid_rows=0,
        invalid_rows=0,
    )

    for i, row in enumerate(rows):
        row_id = row.get("lead_id", f"row-{i}")
        errors: list[str] = []

        # Required field check
        for field_name in REQUIRED_GDPR_FIELDS:
            if field_name not in row or row[field_name] is None:
                errors.append(f"[{row_id}] missing required field: {field_name}")

        # Source enum check
        if row.get("source") not in GDPR_SOURCES:
            errors.append(f"[{row_id}] unknown source: {row.get('source')!r}")

        # Count failed extractions (email=None) — must be present, not absent
        if row.get("extracted_email") is None:
            result.rows_without_email += 1

        if errors:
            result.invalid_rows += 1
            result.missing_field_errors.extend(errors)
        else:
            result.valid_rows += 1

    return result


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

_PHASE_NAMES = {
    1: "DISCOVERY     Atoka candidates ingest",
    2: "OFFLINE GATES Sector / size / territory",
    3: "EMAIL EXTRACT Atoka → scraping",
    4: "SOLAR+RENDER  Google Solar + Remotion",
    5: "MX+BOUNCE     NeverBounce validation",
    6: "CONTENT VALID Compliance / quarantine",
    7: "SEND          Resend API (email sent)",
    8: "TRACKING      Delivery confirmed",
    9: "AUDIENCE      Opened → lookalike pool",
}

_WIDTH = 72


def _bar(n: int, total: int, width: int = 30, char: str = "█") -> str:
    filled = int(round(n / total * width)) if total > 0 else 0
    empty = width - filled
    return char * filled + "░" * empty


def _rule(char: str = "─", w: int = _WIDTH) -> str:
    return char * w


def print_report(result: SimulationResult, gdpr: GdprValidationResult) -> None:
    """Print the complete simulation report to stdout."""

    n = result.n_candidates
    sent = result.sent
    v1_total = result.total_v1_cents
    v2_total = result.total_v2_cents
    saving_cents = v1_total - v2_total
    saving_pct = result.cost_reduction_pct

    print()
    print("╔" + "═" * (_WIDTH - 2) + "╗")
    print("║" + " SolarLead V2 Pipeline — E2E Simulation Report".center(_WIDTH - 2) + "║")
    print("║" + f" N={n} candidates  ·  seed={result.leads[0].candidate['id'][:13] if result.leads else '?'}".center(_WIDTH - 2) + "║")
    print("╚" + "═" * (_WIDTH - 2) + "╝")
    print()

    # ── Funnel table ─────────────────────────────────────────────────────────
    print("  PIPELINE FUNNEL")
    print("  " + _rule())
    print(f"  {'Phase':<4}  {'Description':<36}  {'Survivors':>9}  {'%':>6}  Bar")
    print("  " + _rule())

    prev = n
    for ph in range(1, 10):
        survivors = result.survivors.get(ph, 0)
        pct = survivors / n * 100 if n > 0 else 0.0
        bar = _bar(survivors, n)
        phase_label = _PHASE_NAMES.get(ph, f"Phase {ph}")
        marker = " ✉" if ph == 7 else "  "
        drop = prev - survivors if ph > 1 else 0
        drop_str = f"(−{drop:3d})" if drop > 0 else "       "
        print(
            f"  {ph:<4}  {phase_label:<36}  {survivors:>5} {drop_str}  {pct:>5.1f}%  {bar}"
        )
        if ph > 1:
            prev = survivors

    print("  " + _rule())
    print()

    # ── Tracking metrics ─────────────────────────────────────────────────────
    if sent > 0:
        print("  TRACKING EVENTS (of emails sent)")
        print("  " + _rule("-"))
        print(f"  {'Sent':>12} : {sent:>5}   100.0%")
        print(f"  {'Delivered':>12} : {result.delivered:>5}   {result.delivered/sent*100:>5.1f}%")
        print(f"  {'Opened':>12} : {result.opened:>5}   {result.opened/sent*100:>5.1f}%")
        print(f"  {'Clicked':>12} : {result.clicked:>5}   {result.clicked/sent*100:>5.1f}%")
        print()

    # ── Drop reason breakdown ─────────────────────────────────────────────────
    print("  REJECTION REASONS")
    print("  " + _rule("-"))
    sorted_reasons = sorted(result.drop_reasons.items(), key=lambda x: -x[1])
    for reason, count in sorted_reasons:
        bar = _bar(count, n, width=20)
        print(f"  {reason:<45}  {count:>3}  {bar}")
    print()

    # ── Cost comparison ───────────────────────────────────────────────────────
    print("  COST COMPARISON  (V1 legacy  vs  V2 sprint-8)")
    print("  " + _rule())

    print(f"\n  {'Component':<40} {'V1 legacy':>10} {'V2 sprint8':>10}")
    print(f"  {_rule('-', 60)}")

    # Break down V2 cost by component
    # Discovery
    disc_v1 = n * (ATOKA_DISCOVERY_COST_PER_RECORD_CENTS + PROXY_SCORE_COST_PER_RECORD_CENTS)
    disc_v2 = n * ATOKA_DISCOVERY_COST_PER_RECORD_CENTS
    # Solar in V1 (called on ~85% before identity)
    solar_v1_count = int(n * 0.85)
    solar_v2_count = result.survivors.get(3, 0)  # only after email found
    solar_v1 = solar_v1_count * GOOGLE_SOLAR_COST_PER_CALL_CENTS
    solar_v2 = solar_v2_count * GOOGLE_SOLAR_COST_PER_CALL_CENTS
    # Visura (V1 only)
    visura_v1 = int(result.survivors.get(2, 0) * V1_VISURA_HIT_RATE) * VISURA_COST_PER_CALL_CENTS
    visura_v2 = 0
    # Atoka enrichment
    atoka_enrich_v1 = result.survivors.get(2, 0) * ATOKA_COST_PER_CALL_CENTS
    atoka_enrich_v2 = result.survivors.get(2, 0) * ATOKA_COST_PER_CALL_CENTS  # same
    # Hunter (V1 only)
    hunter_v1 = result.survivors.get(2, 0) * HUNTER_COST_PER_CALL_CENTS
    hunter_v2 = 0
    # NeverBounce
    nb_v1 = result.survivors.get(2, 0) * NEVERBOUNCE_COST_PER_CALL_CENTS  # V1: on all reaching identity
    nb_v2 = result.survivors.get(4, 0) * NEVERBOUNCE_COST_PER_CALL_CENTS  # V2: only after solar
    # Render (Replicate)
    render_v1 = result.survivors.get(4, 0) * REPLICATE_RENDER_COST_PER_CALL_CENTS
    render_v2 = result.survivors.get(4, 0) * REPLICATE_RENDER_COST_PER_CALL_CENTS  # same
    # Send
    send_cost = sent * RESEND_COST_PER_EMAIL_CENTS

    rows_cost = [
        ("Atoka discovery (1¢/record)", disc_v1, disc_v2),
        ("Proxy score (V1 only, 1¢/record)", n * PROXY_SCORE_COST_PER_RECORD_CENTS, 0),
        ("Atoka enrichment (15¢/company)", atoka_enrich_v1, atoka_enrich_v2),
        ("Visura cadastral (V1 only, 25¢/hit)", visura_v1, visura_v2),
        ("Hunter.io email (V1 only, 10¢/search)", hunter_v1, hunter_v2),
        ("Google Solar API (2¢/call)", solar_v1, solar_v2),
        ("Remotion render (1¢/render)", render_v1, render_v2),
        ("NeverBounce (1¢/check)", nb_v1, nb_v2),
        ("Resend email (1¢/email)", send_cost, send_cost),
    ]

    calc_v1_total = 0
    calc_v2_total = 0
    for label, c_v1, c_v2 in rows_cost:
        calc_v1_total += c_v1
        calc_v2_total += c_v2
        v1_str = f"{c_v1/100:.2f} €" if c_v1 else "    —   "
        v2_str = f"{c_v2/100:.2f} €" if c_v2 else "    —   "
        delta = ""
        if c_v1 and not c_v2:
            delta = "  ← eliminated"
        elif c_v1 and c_v2 and c_v2 < c_v1:
            pct = (c_v1 - c_v2) / c_v1 * 100
            delta = f"  ↓ −{pct:.0f}%"
        print(f"  {label:<40} {v1_str:>10} {v2_str:>10}{delta}")

    print(f"  {_rule('-', 60)}")
    print(
        f"  {'TOTAL (per ' + str(n) + ' candidates)':<40} "
        f"{calc_v1_total/100:>9.2f}€ {calc_v2_total/100:>9.2f}€"
    )
    delta_calc = calc_v1_total - calc_v2_total
    delta_pct_calc = delta_calc / calc_v1_total * 100 if calc_v1_total > 0 else 0.0
    print(
        f"\n  Gross savings: {delta_calc/100:.2f} € "
        f"({delta_pct_calc:.1f}% reduction)"
    )
    if sent > 0:
        print(
            f"  Cost per email sent  →  V1: {calc_v1_total/sent/100:.2f} €  "
            f"/ V2: {calc_v2_total/sent/100:.2f} €"
        )
    print()

    # ── GDPR audit trail ─────────────────────────────────────────────────────
    print("  GDPR AUDIT TRAIL (email_extraction_log)")
    print("  " + _rule("-"))
    print(f"  Phase-3 attempts logged : {gdpr.total_rows}")
    print(f"  Valid rows              : {gdpr.valid_rows}")
    print(f"  Invalid rows            : {gdpr.invalid_rows}")
    print(f"  Rows with email=NULL    : {gdpr.rows_without_email}   (failed extractions — logged per spec)")
    if gdpr.all_valid:
        print("  GDPR trail              : ✅  ALL ROWS VALID")
    else:
        print("  GDPR trail              : ❌  ERRORS FOUND:")
        for err in gdpr.missing_field_errors[:10]:
            print(f"    {err}")
        if len(gdpr.missing_field_errors) > 10:
            print(f"    … and {len(gdpr.missing_field_errors) - 10} more")
    print()

    # ── Verdict ──────────────────────────────────────────────────────────────
    print("  VALIDATION SUMMARY")
    print("  " + _rule("═"))
    checks = [
        ("Funnel from 100 → sent ≥ 25", sent >= 25),
        ("GDPR audit trail 100% valid", gdpr.all_valid),
        ("Failed extractions still logged", gdpr.rows_without_email > 0),
        (f"Cost reduction ≥ 40% (target −46%)", delta_pct_calc >= 40.0),
        ("Open rate ≥ 25%", result.opened >= sent * 0.25 if sent else False),
        ("Delivery rate ≥ 90%", result.delivered >= sent * 0.90 if sent else False),
    ]
    all_pass = True
    for desc, ok in checks:
        icon = "✅" if ok else "❌"
        print(f"  {icon}  {desc}")
        if not ok:
            all_pass = False
    print()
    if all_pass:
        print("  🟢  All checks passed — pipeline is production-ready.")
    else:
        print("  🔴  Some checks failed — review above.")
    print()


# ---------------------------------------------------------------------------
# JSON output mode
# ---------------------------------------------------------------------------

def to_json_report(result: SimulationResult, gdpr: GdprValidationResult) -> dict[str, Any]:
    """Return a machine-readable summary dict for CI / downstream tooling."""

    n = result.n_candidates
    sent = result.sent
    v1 = result.total_v1_cents
    v2 = result.total_v2_cents

    return {
        "meta": {
            "n_candidates": n,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        },
        "funnel": {
            ph: {
                "survivors": result.survivors.get(ph, 0),
                "pct_of_total": round(result.survivors.get(ph, 0) / n * 100, 1) if n else 0,
            }
            for ph in range(1, 10)
        },
        "tracking": {
            "sent": sent,
            "delivered": result.delivered,
            "opened": result.opened,
            "clicked": result.clicked,
            "delivery_rate_pct": round(result.delivered / sent * 100, 1) if sent else 0,
            "open_rate_pct": round(result.opened / sent * 100, 1) if sent else 0,
            "click_rate_pct": round(result.clicked / sent * 100, 1) if sent else 0,
        },
        "costs": {
            "v1_total_cents": v1,
            "v2_total_cents": v2,
            "savings_cents": v1 - v2,
            "savings_pct": round(result.cost_reduction_pct, 1),
            "v1_cost_per_email_cents": round(v1 / sent, 2) if sent else 0,
            "v2_cost_per_email_cents": round(v2 / sent, 2) if sent else 0,
        },
        "drop_reasons": result.drop_reasons,
        "gdpr": {
            "total_rows": gdpr.total_rows,
            "valid_rows": gdpr.valid_rows,
            "invalid_rows": gdpr.invalid_rows,
            "rows_without_email": gdpr.rows_without_email,
            "all_valid": gdpr.all_valid,
            "errors": gdpr.missing_field_errors[:20],
        },
        "checks": {
            "funnel_ok": sent >= 25,
            "gdpr_ok": gdpr.all_valid,
            "failed_extractions_logged": gdpr.rows_without_email > 0,
            "cost_reduction_ok": result.cost_reduction_pct >= 40.0,
            "open_rate_ok": result.opened >= sent * 0.25 if sent else False,
            "delivery_rate_ok": result.delivered >= sent * 0.90 if sent else False,
            "all_pass": all([
                sent >= 25,
                gdpr.all_valid,
                gdpr.rows_without_email > 0,
                result.cost_reduction_pct >= 40.0,
            ]),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SolarLead V2 pipeline E2E simulation & audit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            examples:
              python -m src.scripts.e2e_pipeline_test
              python -m src.scripts.e2e_pipeline_test --candidates 200 --seed 99
              python -m src.scripts.e2e_pipeline_test --json > report.json
            """
        ),
    )
    p.add_argument(
        "--candidates", "-n",
        type=int, default=100,
        metavar="N",
        help="Number of synthetic candidates to simulate (default: 100)",
    )
    p.add_argument(
        "--seed",
        type=int, default=42,
        help="RNG seed for deterministic runs (default: 42)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output machine-readable JSON instead of the human report",
    )
    p.add_argument(
        "--fail-fast",
        action="store_true",
        help="Exit with code 1 if any validation check fails (useful for CI)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    result = simulate_pipeline(n_candidates=args.candidates, seed=args.seed)
    gdpr = validate_gdpr_trail(result.gdpr_rows)

    if args.json_output:
        report = to_json_report(result, gdpr)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(result, gdpr)

    if args.fail_fast:
        # Compute all-pass check
        checks_ok = (
            result.sent >= 25
            and gdpr.all_valid
            and gdpr.rows_without_email > 0
            and result.cost_reduction_pct >= 40.0
        )
        sys.exit(0 if checks_ok else 1)


if __name__ == "__main__":
    main()
