"""L3 — Quality filter stage (FLUSSO 1 v3).

Iterates over L2 scraped candidates, calls
``building_quality_filter.passes_filter_simple`` for each, persists the
score on ``scan_candidates.building_quality_score``, and returns only
the candidates that passed (score >= 3).

The drop is logged but doesn't delete the row — the persisted record
keeps the score so the dashboard can display "rejected_quality"
candidates with their reason.

Cost: zero.
"""

from __future__ import annotations

from typing import Any

from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from ...services.building_quality_filter import passes_filter_simple
from .types_v3 import (
    FunnelV3Context,
    QualifiedCandidate,
    ScrapedCandidate,
)

log = get_logger(__name__)


async def run_level3_quality(
    ctx: FunnelV3Context,
    candidates: list[ScrapedCandidate],
) -> list[QualifiedCandidate]:
    """Score every candidate, persist, return survivors."""
    if not candidates:
        return []

    sb = get_service_client()
    survivors: list[QualifiedCandidate] = []
    bulk_updates: list[dict[str, Any]] = []
    rejected = 0

    for sc in candidates:
        rec = sc.record
        check = passes_filter_simple(
            user_ratings_total=rec.user_ratings_total,
            website=rec.website,
            phone=rec.phone,
            business_status=rec.business_status,
        )
        bulk_updates.append(
            {
                "id": str(rec.candidate_id),
                "building_quality_score": check.score,
                "stage": 3 if check.passed else 3,  # always advance
                # Reasons piggyback on the existing score_flags column for now.
                "score_flags": (
                    list(check.reasons)
                    if check.passed
                    else [f"rejected_quality:{r}" for r in check.reasons]
                ),
            }
        )
        if check.passed:
            survivors.append(
                QualifiedCandidate(
                    record=rec,
                    scraped=sc.scraped,
                    contact=sc.contact,
                    building_quality_score=check.score,
                )
            )
        else:
            rejected += 1

    if bulk_updates:
        try:
            sb.table("scan_candidates").upsert(
                bulk_updates, on_conflict="id"
            ).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "level3_quality.persist_failed",
                err=type(exc).__name__,
                msg=str(exc)[:200],
            )

    log.info(
        "level3_quality.done",
        tenant_id=ctx.tenant_id,
        scanned=len(candidates),
        passed=len(survivors),
        rejected=rejected,
    )
    return survivors
