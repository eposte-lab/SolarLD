"""V3 funnel dataclasses — no-Atoka, geocentric.

Each level produces a richer variant of the previous one (same pattern
as v2's ``types.py``) but the v3 chain is:

  L1 PlaceCandidateRecord   → just discovered via Places Nearby
  L2 ScrapedCandidate       → + scraped_data (sito, Pagine Bianche, OpenCorporates)
  L3 QualifiedCandidate     → + building_quality_score (euristica)
  L4 SolarQualified         → + roof + solar_verdict
  L5 ScoredV3Candidate      → + Haiku proxy score + recommended_for_rendering

Once the v2 → v3 demolition lands (Sprint 1.1 + 1.3 of the plan),
this file moves to ``types.py`` and the old Atoka-coupled types are
deleted. Until then the two files coexist and v3 agents import from
this module explicitly to avoid Atoka coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ...services.scan_cost_tracker import ScanCostAccumulator
from ...services.tenant_config_service import TenantConfig


@dataclass(slots=True)
class FunnelV3Context:
    """Shared context threaded through all v3 levels.

    The v3 funnel is **tenant-scoped** (not territory-scoped like v2) —
    L0 zone mapping happens once in onboarding, then L1-L6 cycle daily
    without re-doing the territory partitioning. ``tenant_id`` is the
    only routing key; territory_id is no longer carried.
    """

    tenant_id: str
    scan_id: str
    config: TenantConfig
    costs: ScanCostAccumulator

    # Hard cap to avoid runaway Places spend on first deploy. With ~100
    # zones × 1 Nearby call/zone × 20 results/call = 2000 candidates max.
    max_l1_candidates: int = 2000

    # L4 gate — fraction of L3-quality candidates that progress to Solar.
    # Cheap quality filter is upstream so this can be permissive.
    solar_gate_pct: float = 0.60

    # Hard floor so tiny scans still produce leads.
    solar_gate_min_candidates: int = 20

    # L5 score threshold to mark a candidate `recommended_for_rendering`.
    rendering_score_threshold: int = 60


# ---------------------------------------------------------------------------
# L1 — Places discovery output
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PlaceCandidateRecord:
    """Persisted snapshot after L1.

    Replaces v2's ``L1Candidate``. The anchor is now ``google_place_id``
    (always non-null) — VAT may show up later via L2 OpenCorporates
    lookup but it's no longer the candidate's identity.
    """

    candidate_id: UUID  # PK in scan_candidates
    google_place_id: str
    display_name: str | None
    formatted_address: str | None
    lat: float
    lng: float
    types: list[str] = field(default_factory=list)
    business_status: str | None = None
    user_ratings_total: int | None = None
    rating: float | None = None
    website: str | None = None
    phone: str | None = None
    google_maps_uri: str | None = None
    # Discovery context
    zone_id: UUID | None = None  # FK to tenant_target_areas.id
    predicted_sector: str | None = None
    sector_confidence: float | None = None
    discovery_keyword: str | None = None


# ---------------------------------------------------------------------------
# L2 — Scraping output
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScrapedSignals:
    """What L2 writes into `scan_candidates.scraped_data` JSONB."""

    # From candidate's own website (sito → /contatti, /chi-siamo, footer)
    website_emails: list[str] = field(default_factory=list)
    website_phone: str | None = None
    website_pec: str | None = None
    website_address: str | None = None
    website_decision_maker: str | None = None  # CEO/AD if cited on /chi-siamo

    # From Pagine Bianche
    pagine_bianche_phone: str | None = None
    pagine_bianche_address: str | None = None
    pagine_bianche_category: str | None = None

    # From OpenCorporates
    opencorporates_vat: str | None = None
    opencorporates_legal_name: str | None = None
    opencorporates_founding_date: str | None = None
    opencorporates_status: str | None = None
    opencorporates_legal_form: str | None = None

    # Site signal flags (sector keywords like "capannone", "stabilimento"...)
    site_signals: list[str] = field(default_factory=list)

    # Audit metadata for GDPR
    sources_consulted: list[str] = field(default_factory=list)
    scrape_ok: bool = False
    scrape_errors: list[str] = field(default_factory=list)

    def to_jsonb(self) -> dict[str, Any]:
        return {
            "website": {
                "emails": self.website_emails,
                "phone": self.website_phone,
                "pec": self.website_pec,
                "address": self.website_address,
                "decision_maker": self.website_decision_maker,
            },
            "pagine_bianche": {
                "phone": self.pagine_bianche_phone,
                "address": self.pagine_bianche_address,
                "category": self.pagine_bianche_category,
            },
            "opencorporates": {
                "vat": self.opencorporates_vat,
                "legal_name": self.opencorporates_legal_name,
                "founding_date": self.opencorporates_founding_date,
                "status": self.opencorporates_status,
                "legal_form": self.opencorporates_legal_form,
            },
            "site_signals": self.site_signals,
            "sources_consulted": self.sources_consulted,
            "scrape_ok": self.scrape_ok,
            "scrape_errors": self.scrape_errors,
        }


@dataclass(slots=True)
class ContactExtraction:
    """The single best contact picked by `extract_best_email` for L5."""

    best_email: str | None = None
    best_email_confidence: str | None = None  # "alta" | "media" | None
    best_email_type: str | None = None  # "named_role" | "generic"
    best_phone: str | None = None
    pec: str | None = None
    decision_maker_name: str | None = None

    def to_jsonb(self) -> dict[str, Any]:
        return {
            "best_email": self.best_email,
            "best_email_confidence": self.best_email_confidence,
            "best_email_type": self.best_email_type,
            "best_phone": self.best_phone,
            "pec": self.pec,
            "decision_maker_name": self.decision_maker_name,
        }


@dataclass(slots=True)
class ScrapedCandidate:
    """L1 + L2."""

    record: PlaceCandidateRecord
    scraped: ScrapedSignals
    contact: ContactExtraction


# ---------------------------------------------------------------------------
# L3 — Building quality output
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class QualifiedCandidate:
    """L1 + L2 + L3 building quality euristica."""

    record: PlaceCandidateRecord
    scraped: ScrapedSignals
    contact: ContactExtraction
    building_quality_score: int  # 0-5 from heuristics; higher = better


# ---------------------------------------------------------------------------
# L4 — Solar qualification output
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SolarQualified:
    """L1..L3 + Solar buildingInsights verdict.

    `roof_id` is null when Solar said "no_solar_data" or the area / kWp /
    sunshine thresholds rejected the candidate. The orchestrator drops
    those before L5.
    """

    record: PlaceCandidateRecord
    scraped: ScrapedSignals
    contact: ContactExtraction
    building_quality_score: int
    roof_id: UUID | None
    solar_verdict: str  # 'accepted' | 'rejected_tech' | 'no_solar_data' | 'api_error'
    solar_area_m2: float | None = None
    solar_kw_installable: float | None = None
    solar_panels_count: int | None = None
    solar_sunshine_hours: float | None = None


# ---------------------------------------------------------------------------
# L5 — Proxy score output
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScoredV3Candidate:
    """All previous stages + L5 LLM proxy score."""

    record: PlaceCandidateRecord
    scraped: ScrapedSignals
    contact: ContactExtraction
    building_quality_score: int
    roof_id: UUID | None
    solar_verdict: str
    solar_area_m2: float | None
    solar_kw_installable: float | None
    solar_panels_count: int | None
    solar_sunshine_hours: float | None
    # Score breakdown
    icp_fit_score: int
    solar_potential_score: int
    contact_completeness_score: int
    overall_score: int
    predicted_size_category: str | None = None  # micro|small|medium|large
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    recommended_for_rendering: bool = False
    predicted_ateco_codes: list[str] = field(default_factory=list)
