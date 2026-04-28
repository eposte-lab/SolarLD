"""Cluster A/B copy variant management API (Sprint 9 Fase B.6).

Provides operators with visibility and manual-override capability for
the cluster-level A/B engine that runs automatically via the nightly
``cluster_ab_evaluation_cron`` (03:30 UTC).

Routes
------
GET  /v1/cluster-ab/active
    List all (tenant_id, cluster_signature) pairs with active variants.
    Returns variant stats, daily metrics and a provisional Bayesian
    "probability A wins" score (stdlib Monte Carlo, no scipy).

GET  /v1/cluster-ab/{cluster_signature}
    Detailed view for one cluster: all rounds, current active variants,
    last 30 days of daily metrics.

POST /v1/cluster-ab/{variant_id}/promote
    Manually promote a specific variant to winner (operator override).
    Demotes the partner and generates the next round.

POST /v1/cluster-ab/{cluster_signature}/regenerate
    Discard the current active variants and generate a fresh A+B pair
    for this cluster from scratch (no previous winner baseline).

All routes require authentication; data is RLS-scoped to the tenant.
"""

from __future__ import annotations

import random as _random
from typing import Any

from fastapi import APIRouter, HTTPException, status

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..services.cluster_ab_evaluator_service import manually_promote_variant

log = get_logger(__name__)
router = APIRouter(prefix="/cluster-ab", tags=["cluster-ab"])

_BAYESIAN_N = 10_000  # Monte Carlo draws for "P(A wins)" estimate


# ── Helpers ──────────────────────────────────────────────────────────


def _prob_a_wins(replied_a: int, sent_a: int, replied_b: int, sent_b: int) -> float | None:
    """Beta-Binomial Monte Carlo — P(reply_rate_A > reply_rate_B).

    Uses Beta(α, β) posterior with uniform prior (α=replies+1, β=not_replied+1).
    Returns None when either variant has < 5 sends (too sparse to be meaningful).
    """
    if sent_a < 5 or sent_b < 5:
        return None
    alpha_a = replied_a + 1
    beta_a = (sent_a - replied_a) + 1
    alpha_b = replied_b + 1
    beta_b = (sent_b - replied_b) + 1
    wins = sum(
        1
        for _ in range(_BAYESIAN_N)
        if _random.betavariate(alpha_a, beta_a) > _random.betavariate(alpha_b, beta_b)
    )
    return round(wins / _BAYESIAN_N, 4)


def _variant_row_to_dict(v: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": v["id"],
        "variant_label": v["variant_label"],
        "round_number": v["round_number"],
        "status": v["status"],
        "copy_subject": v["copy_subject"],
        "copy_opening_line": v["copy_opening_line"],
        "copy_proposition_line": v["copy_proposition_line"],
        "cta_primary_label": v["cta_primary_label"],
        "generated_by": v["generated_by"],
        "sent_count": v.get("sent_count", 0),
        "replied_count": v.get("replied_count", 0),
        "reply_rate": (
            round(v["replied_count"] / v["sent_count"], 4)
            if v.get("sent_count", 0) > 0
            else None
        ),
    }


# ── Routes ───────────────────────────────────────────────────────────


@router.get("/active")
async def list_active_clusters(user: CurrentUser) -> dict[str, Any]:
    """List all cluster pairs with active A+B variants for this tenant."""
    tenant_id = require_tenant(user)
    sb = get_service_client()

    resp = await sb.table("cluster_copy_variants") \
        .select(
            "id, tenant_id, cluster_signature, round_number, variant_label, "
            "copy_subject, copy_opening_line, copy_proposition_line, cta_primary_label, "
            "status, generated_by, sent_count, replied_count, generated_at, promoted_at"
        ) \
        .eq("tenant_id", tenant_id) \
        .eq("status", "active") \
        .order("cluster_signature") \
        .order("variant_label") \
        .execute()

    rows = resp.data or []

    # Group by cluster_signature → round_number pair.
    clusters: dict[str, dict[str, Any]] = {}
    for row in rows:
        sig = row["cluster_signature"]
        if sig not in clusters:
            clusters[sig] = {
                "cluster_signature": sig,
                "round_number": row["round_number"],
                "variants": [],
            }
        clusters[sig]["variants"].append(_variant_row_to_dict(row))

    # Add Bayesian P(A wins) for each cluster.
    result = []
    for cluster in clusters.values():
        va = next((v for v in cluster["variants"] if v["variant_label"] == "A"), None)
        vb = next((v for v in cluster["variants"] if v["variant_label"] == "B"), None)
        if va and vb:
            cluster["prob_a_wins"] = _prob_a_wins(
                va["replied_count"], va["sent_count"],
                vb["replied_count"], vb["sent_count"],
            )
        result.append(cluster)

    return {"clusters": result, "total": len(result)}


@router.get("/{cluster_signature:path}")
async def get_cluster_detail(cluster_signature: str, user: CurrentUser) -> dict[str, Any]:
    """Detailed view for one cluster — all rounds + last 30 days metrics."""
    tenant_id = require_tenant(user)
    sb = get_service_client()

    variants_resp = await sb.table("cluster_copy_variants") \
        .select(
            "id, variant_label, round_number, status, generated_by, "
            "copy_subject, copy_opening_line, copy_proposition_line, cta_primary_label, "
            "sent_count, replied_count, generated_at, promoted_at"
        ) \
        .eq("tenant_id", tenant_id) \
        .eq("cluster_signature", cluster_signature) \
        .order("round_number", desc=True) \
        .order("variant_label") \
        .execute()

    variants = variants_resp.data or []
    if not variants:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No variants found for cluster '{cluster_signature}'",
        )

    # Last 30 days of daily metrics.
    metrics_resp = await sb.table("ab_test_metrics_daily") \
        .select("round_number, variant_label, date, sent_count, replied_count, reply_rate") \
        .eq("tenant_id", tenant_id) \
        .eq("cluster_signature", cluster_signature) \
        .order("date", desc=True) \
        .limit(60) \
        .execute()

    # Active variants for Bayesian estimate.
    active = [v for v in variants if v["status"] == "active"]
    va = next((v for v in active if v["variant_label"] == "A"), None)
    vb = next((v for v in active if v["variant_label"] == "B"), None)
    prob_a_wins = None
    if va and vb:
        prob_a_wins = _prob_a_wins(
            va["replied_count"], va["sent_count"],
            vb["replied_count"], vb["sent_count"],
        )

    return {
        "cluster_signature": cluster_signature,
        "variants": [_variant_row_to_dict(v) for v in variants],
        "active_round": max((v["round_number"] for v in active), default=None),
        "prob_a_wins": prob_a_wins,
        "daily_metrics": metrics_resp.data or [],
    }


@router.post("/{variant_id}/promote")
async def promote_variant(variant_id: str, user: CurrentUser) -> dict[str, Any]:
    """Manually promote a variant to winner (operator override).

    Promotes the variant, demotes its partner, and generates a new round
    using the promoted variant as the baseline.
    """
    tenant_id = require_tenant(user)
    sb = get_service_client()

    try:
        result = await manually_promote_variant(sb, tenant_id, variant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    log.info(
        "cluster_ab.manual_promote",
        tenant_id=tenant_id,
        variant_id=variant_id,
        **result,
    )
    return result


@router.post("/{cluster_signature:path}/regenerate")
async def regenerate_cluster(cluster_signature: str, user: CurrentUser) -> dict[str, Any]:
    """Discard current active variants and generate a fresh A+B round.

    Both active variants are archived and a new pair is generated from
    scratch (no previous_winner baseline — pure round 1 prompt).

    Use this when the cluster copy feels stale or you want to start a
    fresh exploration after manual promotions have biased the baseline.
    """
    tenant_id = require_tenant(user)
    sb = get_service_client()

    # Find current active variants.
    resp = await sb.table("cluster_copy_variants") \
        .select("id, round_number") \
        .eq("tenant_id", tenant_id) \
        .eq("cluster_signature", cluster_signature) \
        .eq("status", "active") \
        .execute()

    current = resp.data or []
    if not current:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active variants for cluster '{cluster_signature}'",
        )

    current_round = max(v["round_number"] for v in current)

    # Archive existing active variants.
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    ids = [v["id"] for v in current]
    for vid in ids:
        await sb.table("cluster_copy_variants") \
            .update({"status": "archived", "promoted_at": now_iso}) \
            .eq("id", vid) \
            .execute()

    # Generate new round from scratch (no baseline).
    from ..services.cluster_ab_evaluator_service import _generate_new_round
    new_round = current_round + 1
    await _generate_new_round(sb, tenant_id, cluster_signature, new_round, previous_winner=None)

    log.info(
        "cluster_ab.regenerated",
        tenant_id=tenant_id,
        cluster_signature=cluster_signature,
        new_round=new_round,
    )
    return {
        "cluster_signature": cluster_signature,
        "archived_count": len(ids),
        "new_round": new_round,
    }
