"""B2B funnel v2 — 4-level ATECO discovery pipeline.

See docs/ARCHITECTURE_V2.md for the design rationale.
"""

from .types import EnrichedCandidate, FunnelContext, ScoredCandidate

__all__ = ["EnrichedCandidate", "FunnelContext", "ScoredCandidate"]
