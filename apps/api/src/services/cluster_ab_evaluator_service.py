"""Cluster A/B test daily evaluation — chi-square with auto-promotion.

Runs as an arq background task (registered in jobs/cluster_ab_cron.py).
For each (tenant, cluster_signature) pair that has active A+B variants:

  1. Aggregate sent_count / replied_count per variant from outreach_sends
     JOIN cluster_copy_variants (via cluster_variant_id) for the last
     WINDOW_DAYS days.

  2. Update the denormalised counters on cluster_copy_variants and
     append a snapshot row to ab_test_metrics_daily.

  3. Statistical test (chi-square 2×2, Pearson with Yates correction,
     no scipy — stdlib math.erfc only):
       • min(sent_a, sent_b) < MIN_SAMPLES → skip, too few data points
       • p < ALPHA  → promote winner, demote loser, generate new round
       • sent_total >= MAX_SAMPLES and p >= ALPHA → "no_difference",
         generate a new round with random variation

All writes are service-role scoped (no RLS restriction).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

from ..core.logging import get_logger

log = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────
WINDOW_DAYS: int = 14      # rolling window for aggregation
MIN_SAMPLES: int = 100     # minimum sends per variant before deciding
MAX_SAMPLES: int = 1000    # total sends after which no_difference is forced
ALPHA: float = 0.05        # significance level for chi-square


# ── Chi-square 2x2 (Pearson, Yates correction) — no scipy ─────────────

def chi_square_2x2(replied_a: int, sent_a: int, replied_b: int, sent_b: int) -> float:
    """Return the p-value for a 2x2 contingency table chi-square test.

    Uses Pearson chi-square with Yates continuity correction (df=1).
    p-value computed from the chi-square CDF via the complementary error
    function (math.erfc) — stdlib only, no scipy.

    Args:
        replied_a, sent_a: successes and total for variant A.
        replied_b, sent_b: successes and total for variant B.

    Returns:
        p-value in [0, 1].  Returns 1.0 on degenerate inputs.
    """
    # Not-replied counts
    not_a = sent_a - replied_a
    not_b = sent_b - replied_b

    n = sent_a + sent_b
    if n == 0:
        return 1.0

    # Row / column marginals
    r1 = replied_a + replied_b    # first row (replied)
    r2 = not_a + not_b            # second row (not replied)
    c1 = sent_a                   # first col (variant A)
    c2 = sent_b                   # second col (variant B)

    if r1 == 0 or r2 == 0 or c1 == 0 or c2 == 0:
        return 1.0

    # Expected values
    e11 = r1 * c1 / n
    e12 = r1 * c2 / n
    e21 = r2 * c1 / n
    e22 = r2 * c2 / n

    if min(e11, e12, e21, e22) < 1:
        # Cell counts too small even for the continuity-corrected test.
        return 1.0

    # Yates-corrected chi-square statistic
    chi2 = (
        (max(0, abs(replied_a - e11) - 0.5) ** 2) / e11
        + (max(0, abs(replied_b - e12) - 0.5) ** 2) / e12
        + (max(0, abs(not_a - e21) - 0.5) ** 2) / e21
        + (max(0, abs(not_b - e22) - 0.5) ** 2) / e22
    )

    # p-value from chi-square(df=1) CDF.
    # For df=1: chi2(df=1) = z² where z ~ N(0,1).
    # P(χ² > x) = P(|Z| > √x) = erfc(√x / √2)
    if chi2 <= 0:
        return 1.0
    p_value = math.erfc(math.sqrt(chi2 / 2))
    return float(p_value)


# ── Main evaluation function ──────────────────────────────────────────

async def evaluate_cluster_ab_tests(sb: Any) -> dict[str, Any]:
    """Evaluate all active cluster A/B tests for all tenants.

    Args:
        sb: Supabase service-role async client.

    Returns:
        Summary dict with counts for monitoring/alerting.
    """
    stats = {
        "clusters_evaluated": 0,
        "clusters_promoted": 0,
        "clusters_no_difference": 0,
        "clusters_skipped_min_samples": 0,
        "errors": 0,
    }
    today = date.today().isoformat()

    # Find all (tenant_id, cluster_signature) pairs with active variants.
    active_resp = await sb.table("cluster_copy_variants") \
        .select("tenant_id, cluster_signature") \
        .eq("status", "active") \
        .execute()

    if not active_resp.data:
        return stats

    # Deduplicate pairs.
    pairs: set[tuple[str, str]] = {
        (r["tenant_id"], r["cluster_signature"])
        for r in active_resp.data
    }

    for tenant_id, cluster_sig in pairs:
        try:
            await _evaluate_one_cluster(sb, tenant_id, cluster_sig, today, stats)
            stats["clusters_evaluated"] += 1
        except Exception as exc:  # noqa: BLE001
            log.error(
                "cluster_ab_evaluator.error",
                tenant=tenant_id,
                cluster=cluster_sig,
                error=str(exc),
            )
            stats["errors"] += 1

    log.info("cluster_ab_evaluator.done", **stats)
    return stats


async def _evaluate_one_cluster(
    sb: Any,
    tenant_id: str,
    cluster_sig: str,
    today: str,
    stats: dict,
) -> None:
    """Run evaluation for one (tenant, cluster) pair."""
    # Fetch the active A+B variants (highest round_number).
    variants_resp = await sb.table("cluster_copy_variants") \
        .select("id, variant_label, round_number, sent_count, replied_count") \
        .eq("tenant_id", tenant_id) \
        .eq("cluster_signature", cluster_sig) \
        .eq("status", "active") \
        .order("round_number", desc=True) \
        .limit(2) \
        .execute()

    variants = variants_resp.data or []
    if len(variants) < 2:
        return

    # Map by label.
    var_map = {v["variant_label"]: v for v in variants}
    va = var_map.get("A")
    vb = var_map.get("B")
    if not va or not vb:
        return

    round_number = va["round_number"]

    # Aggregate from outreach_sends for the rolling window.
    sent_a, replied_a = await _aggregate_variant(sb, va["id"])
    sent_b, replied_b = await _aggregate_variant(sb, vb["id"])

    # Update denormalised counters.
    await sb.table("cluster_copy_variants") \
        .update({"sent_count": sent_a, "replied_count": replied_a}) \
        .eq("id", va["id"]) \
        .execute()
    await sb.table("cluster_copy_variants") \
        .update({"sent_count": sent_b, "replied_count": replied_b}) \
        .eq("id", vb["id"]) \
        .execute()

    # Append daily snapshot (upsert).
    for label, sent, replied in (("A", sent_a, replied_a), ("B", sent_b, replied_b)):
        rate = round(replied / sent, 4) if sent > 0 else None
        await sb.table("ab_test_metrics_daily").upsert({
            "tenant_id": tenant_id,
            "cluster_signature": cluster_sig,
            "round_number": round_number,
            "variant_label": label,
            "date": today,
            "sent_count": sent,
            "replied_count": replied,
            "reply_rate": rate,
        }).execute()

    min_sent = min(sent_a, sent_b)
    total_sent = sent_a + sent_b

    if min_sent < MIN_SAMPLES:
        stats["clusters_skipped_min_samples"] += 1
        log.debug(
            "cluster_ab_evaluator.waiting_for_samples",
            cluster=cluster_sig,
            tenant=tenant_id,
            sent_a=sent_a,
            sent_b=sent_b,
            min_needed=MIN_SAMPLES,
        )
        return

    p = chi_square_2x2(replied_a, sent_a, replied_b, sent_b)

    if p < ALPHA:
        # Statistically significant — promote winner.
        rate_a = replied_a / sent_a if sent_a > 0 else 0.0
        rate_b = replied_b / sent_b if sent_b > 0 else 0.0
        winner_label = "A" if rate_a >= rate_b else "B"
        loser_label = "B" if winner_label == "A" else "A"
        winner_var = var_map[winner_label]
        loser_var = var_map[loser_label]

        now_iso = datetime.now(timezone.utc).isoformat()
        await sb.table("cluster_copy_variants") \
            .update({"status": "winner", "promoted_at": now_iso}) \
            .eq("id", winner_var["id"]) \
            .execute()
        await sb.table("cluster_copy_variants") \
            .update({"status": "loser", "promoted_at": now_iso}) \
            .eq("id", loser_var["id"]) \
            .execute()

        # Generate next round with winner as baseline.
        previous_winner = {
            "copy_subject": winner_var.get("copy_subject", ""),
            "copy_opening_line": winner_var.get("copy_opening_line", ""),
            "copy_proposition_line": winner_var.get("copy_proposition_line", ""),
            "cta_primary_label": winner_var.get("cta_primary_label", ""),
        }
        await _generate_new_round(
            sb, tenant_id, cluster_sig, round_number + 1, previous_winner
        )

        stats["clusters_promoted"] += 1
        log.info(
            "cluster_ab_evaluator.promoted",
            cluster=cluster_sig,
            tenant=tenant_id,
            winner=winner_label,
            p_value=p,
            reply_rate_a=rate_a,
            reply_rate_b=rate_b,
        )

    elif total_sent >= MAX_SAMPLES:
        # No significant difference after MAX_SAMPLES — call it no_difference.
        now_iso = datetime.now(timezone.utc).isoformat()
        for v in (va, vb):
            await sb.table("cluster_copy_variants") \
                .update({"status": "no_difference", "promoted_at": now_iso}) \
                .eq("id", v["id"]) \
                .execute()

        # Generate a new round from scratch (no previous winner).
        await _generate_new_round(
            sb, tenant_id, cluster_sig, round_number + 1, previous_winner=None
        )

        stats["clusters_no_difference"] += 1
        log.info(
            "cluster_ab_evaluator.no_difference",
            cluster=cluster_sig,
            tenant=tenant_id,
            p_value=p,
            total_sent=total_sent,
        )


async def _aggregate_variant(sb: Any, variant_id: str) -> tuple[int, int]:
    """Return (sent_count, replied_count) for a variant over WINDOW_DAYS.

    Aggregates from outreach_sends.  "replied" is detected by
    outreach_sends.pipeline_status = 'engaged' or by the presence of
    a replied_at timestamp — whichever the schema exposes.
    """
    from datetime import timedelta

    since = (
        datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    ).isoformat()

    resp = await sb.table("outreach_sends") \
        .select("id, pipeline_status") \
        .eq("cluster_variant_id", variant_id) \
        .gte("sent_at", since) \
        .execute()

    rows = resp.data or []
    sent = len(rows)
    replied = sum(
        1 for r in rows
        if r.get("pipeline_status") in {"engaged", "appointment", "closed_won"}
    )
    return sent, replied


async def _generate_new_round(
    sb: Any,
    tenant_id: str,
    cluster_sig: str,
    new_round: int,
    previous_winner: dict[str, str] | None,
) -> None:
    """Fetch tenant name and generate a new A+B pair for the next round."""
    tenant_resp = await sb.table("tenants") \
        .select("business_name") \
        .eq("id", tenant_id) \
        .single() \
        .execute()
    tenant_name = (
        (tenant_resp.data or {}).get("business_name") or "SolarLead"
    )

    from .variant_generator_service import generate_variant_pair, persist_variant_pair

    va, vb = await generate_variant_pair(
        tenant_name=tenant_name,
        cluster_signature=cluster_sig,
        round_number=new_round,
        previous_winner=previous_winner,
    )
    await persist_variant_pair(
        sb, tenant_id, cluster_sig, new_round, va, vb
    )


# ── Manual promotion helper ───────────────────────────────────────────

async def manually_promote_variant(
    sb: Any,
    tenant_id: str,
    winner_variant_id: str,
) -> dict[str, Any]:
    """Manually promote a variant to winner (operator override).

    Promotes the chosen variant, demotes its partner, and generates
    a new round.  Returns a summary dict.
    """
    winner_resp = await sb.table("cluster_copy_variants") \
        .select("*") \
        .eq("id", winner_variant_id) \
        .eq("tenant_id", tenant_id) \
        .single() \
        .execute()
    winner = winner_resp.data
    if not winner:
        raise ValueError(f"Variant {winner_variant_id} not found for tenant {tenant_id}")

    cluster_sig = winner["cluster_signature"]
    round_number = winner["round_number"]
    loser_label = "B" if winner["variant_label"] == "A" else "A"

    now_iso = datetime.now(timezone.utc).isoformat()

    # Promote winner.
    await sb.table("cluster_copy_variants") \
        .update({"status": "winner", "promoted_at": now_iso}) \
        .eq("id", winner_variant_id) \
        .execute()

    # Demote partner.
    await sb.table("cluster_copy_variants") \
        .update({"status": "loser", "promoted_at": now_iso}) \
        .eq("tenant_id", tenant_id) \
        .eq("cluster_signature", cluster_sig) \
        .eq("round_number", round_number) \
        .eq("variant_label", loser_label) \
        .execute()

    # Generate next round.
    previous_winner = {k: winner[k] for k in (
        "copy_subject", "copy_opening_line",
        "copy_proposition_line", "cta_primary_label"
    ) if k in winner}
    await _generate_new_round(
        sb, tenant_id, cluster_sig, round_number + 1, previous_winner
    )

    return {
        "promoted": winner_variant_id,
        "cluster_signature": cluster_sig,
        "new_round": round_number + 1,
    }
