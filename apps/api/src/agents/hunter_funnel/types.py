"""Shared dataclasses passed between funnel levels.

Each level produces a richer variant of the previous one — we keep them as
separate types (rather than one big mutable candidate) so the Python type
checker catches pipeline mis-wiring (e.g. calling L4 with an L1 candidate
that hasn't been scored yet).

The types are in-memory views; persistence goes through `scan_candidates`
(one row per VAT, stage column tracks progress).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from ...services.italian_business_service import AtokaProfile
from ...services.scan_cost_tracker import ScanCostAccumulator
from ...services.tenant_config_service import TenantConfig


@dataclass(slots=True)
class FunnelContext:
    """Shared context threaded through all four levels.

    Holds tenant config, scan identity, and the cost accumulator. Individual
    levels read config fields they care about (L1 reads `ateco_whitelist`,
    L4 reads `technical_b2b`) and accumulate costs here.
    """

    tenant_id: str
    scan_id: str
    territory_id: str
    territory: dict[str, Any]
    config: TenantConfig
    costs: ScanCostAccumulator

    # Budget cap for L1. Enforced *before* the Atoka call because once we
    # page through 5000 records Atoka charges us whether we use them or
    # not.
    max_l1_candidates: int = 1000

    # L4 gate — fraction of L3-scored candidates that pass to Solar.
    # 0.20 = top 20% by score. Lower = stricter / cheaper / fewer leads.
    solar_gate_pct: float = 0.20

    # Hard floor on L4 input so tiny scans still produce leads even when
    # 20% rounds to zero.
    solar_gate_min_candidates: int = 20


@dataclass(slots=True)
class L1Candidate:
    """Output of Level 1 — pure Atoka anagrafica, no enrichment yet.

    Sprint B.2: ``predicted_sector`` + ``sector_confidence`` carry the
    sector-aware tag stamped at L1 INSERT, so L2/L3 don't need to
    re-query ``scan_candidates`` to know which palette of keywords /
    prompt context to use. Both are ``None`` for legacy tenants
    without ``target_wizard_groups``.
    """

    candidate_id: UUID  # PK in scan_candidates
    profile: AtokaProfile
    predicted_sector: str | None = None
    sector_confidence: float | None = None


@dataclass(slots=True)
class EnrichmentSignals:
    """What Level 2 writes into the `enrichment` JSONB column."""

    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    user_ratings_total: int | None = None
    photos_count: int | None = None
    place_types: list[str] = field(default_factory=list)
    # Heuristic flags extracted from the business website ("capannone",
    # "stabilimento", "fabbrica" → strong positive signal for L3 scoring).
    site_signals: list[str] = field(default_factory=list)
    # Did we have to spend a Places call to get this (vs. Atoka had it)?
    places_spent_call: bool = False

    def to_jsonb(self) -> dict[str, Any]:
        return {
            "phone": self.phone,
            "website": self.website,
            "rating": self.rating,
            "user_ratings_total": self.user_ratings_total,
            "photos_count": self.photos_count,
            "place_types": self.place_types,
            "site_signals": self.site_signals,
            "places_spent_call": self.places_spent_call,
        }


@dataclass(slots=True)
class EnrichedCandidate:
    """L1 + L2 — ready for AI proxy scoring."""

    candidate_id: UUID
    profile: AtokaProfile
    enrichment: EnrichmentSignals
    predicted_sector: str | None = None
    sector_confidence: float | None = None


@dataclass(slots=True)
class ScoredCandidate:
    """L1 + L2 + L3 — ready for Solar gate."""

    candidate_id: UUID
    profile: AtokaProfile
    enrichment: EnrichmentSignals
    score: int
    reasons: list[str]
    flags: list[str]
    predicted_sector: str | None = None
    sector_confidence: float | None = None
    sector_match_score: int | None = None  # Sprint B.4 — L3 sub-score
    # Sprint B.4 — Haiku's best-guess ATECO codes for this candidate,
    # validated against ateco_google_types before persistence. Empty
    # when Haiku didn't populate it or all entries were rejected.
    predicted_ateco_codes: list[str] = field(default_factory=list)
