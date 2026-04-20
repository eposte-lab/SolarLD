"""Template A/B experiments API (Part B.4 — tier=enterprise).

CRUD + stats for ``template_experiments``.  The OutreachAgent consults
``load_active_experiment`` (exported from this module) to pick a variant at
send-time.  Stats are computed server-side with a Bayesian Beta-Binomial
model using the Python standard-library ``random.betavariate`` — no scipy
needed.

Routes:
    GET  /v1/experiments          — list (newest first)
    POST /v1/experiments          — create
    GET  /v1/experiments/{id}     — detail
    PATCH /v1/experiments/{id}    — end / declare winner
    GET  /v1/experiments/{id}/stats — per-variant stats + Bayesian verdict
    DELETE /v1/experiments/{id}   — hard-delete (only if no campaigns linked)
"""

from __future__ import annotations

import random as _random
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.security import CurrentUser, require_tenant
from ..core.supabase_client import get_service_client
from ..core.tier import Capability, TierGateError, require_capability
from ..services.audit_service import log_action as audit_log

log = get_logger(__name__)
router = APIRouter()

MIN_SAMPLE_FOR_VERDICT = 20   # sends per variant before declaring a winner
BAYESIAN_N_SAMPLES = 12_000   # Monte Carlo draws — accurate to ~1%
WINNER_THRESHOLD = 0.95       # P(A > B) must exceed this to auto-declare


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    variant_a_subject: str = Field(min_length=1, max_length=300)
    variant_b_subject: str = Field(min_length=1, max_length=300)
    split_pct: int = Field(default=50, ge=1, le=99)


class ExperimentPatch(BaseModel):
    ended_at: str | None = None           # ISO timestamp or null
    winner: Literal["a", "b"] | None = None


class VariantStats(BaseModel):
    sends: int
    opens: int
    clicks: int
    open_rate: float
    click_rate: float


class ExperimentStats(BaseModel):
    experiment_id: str
    a: VariantStats
    b: VariantStats
    prob_a_wins_open: float
    prob_a_wins_click: float
    verdict_open: Literal["a_wins", "b_wins", "in_corso", "no_data"]
    verdict_click: Literal["a_wins", "b_wins", "in_corso", "no_data"]
    min_sample_met: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_experiments(
    ctx: CurrentUser,
) -> list[dict[str, Any]]:
    """List all experiments for the current tenant, newest first."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    res = (
        sb.table("template_experiments")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("started_at", desc=True)
        .limit(100)
        .execute()
    )
    return res.data or []


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_experiment(
    body: ExperimentCreate,
    ctx: CurrentUser,
) -> dict[str, Any]:
    """Create a new A/B experiment (enterprise tier only).

    Enforces max-one-active-experiment per tenant: if there is already
    a running experiment (ended_at IS NULL) for this tenant we return
    409 with a clear message. The operator must end it first.
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()

    # Tier gate
    tenant_res = (
        sb.table("tenants")
        .select("id, tier, settings")
        .eq("id", tenant_id)
        .single()
        .execute()
    )
    tenant_row = tenant_res.data or {}
    try:
        require_capability(tenant_row, Capability.AB_TESTING_TEMPLATES)
    except TierGateError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Piano insufficiente: {exc}",
        ) from exc

    # One active at a time
    running = (
        sb.table("template_experiments")
        .select("id, name")
        .eq("tenant_id", tenant_id)
        .is_("ended_at", "null")
        .limit(1)
        .execute()
    )
    if running.data:
        existing = running.data[0]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Esperimento attivo già presente: «{existing['name']}» "
                f"(id: {existing['id']}). Terminalo prima di crearne uno nuovo."
            ),
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    insert_res = (
        sb.table("template_experiments")
        .insert(
            {
                "tenant_id": tenant_id,
                "name": body.name,
                "variant_a_subject": body.variant_a_subject,
                "variant_b_subject": body.variant_b_subject,
                "split_pct": body.split_pct,
                "started_at": now_iso,
            }
        )
        .execute()
    )
    row = (insert_res.data or [{}])[0]

    await audit_log(
        tenant_id,
        "experiment.created",
        actor_user_id=ctx.sub,
        target_table="template_experiments",
        target_id=str(row.get("id") or ""),
        diff={"name": body.name, "split_pct": body.split_pct},
    )
    return row


@router.get("/{experiment_id}")
async def get_experiment(
    experiment_id: str,
    ctx: CurrentUser,
) -> dict[str, Any]:
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    return _load_experiment(sb, experiment_id, tenant_id)


@router.patch("/{experiment_id}")
async def patch_experiment(
    experiment_id: str,
    body: ExperimentPatch,
    ctx: CurrentUser,
) -> dict[str, Any]:
    """End an experiment and/or declare a winner."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    _load_experiment(sb, experiment_id, tenant_id)  # ownership check

    now_iso = datetime.now(timezone.utc).isoformat()
    update: dict[str, Any] = {}

    if body.ended_at is not None:
        update["ended_at"] = body.ended_at or now_iso

    if body.winner is not None:
        update["winner"] = body.winner
        update["winner_declared_at"] = now_iso
        if "ended_at" not in update:
            update["ended_at"] = now_iso

    if not update:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nessun campo da aggiornare",
        )

    res = (
        sb.table("template_experiments")
        .update(update)
        .eq("id", experiment_id)
        .execute()
    )
    row = (res.data or [{}])[0]

    await audit_log(
        tenant_id,
        "experiment.updated",
        actor_user_id=ctx.sub,
        target_table="template_experiments",
        target_id=experiment_id,
        diff=update,
    )
    return row


@router.delete("/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_experiment(
    experiment_id: str,
    ctx: CurrentUser,
) -> Response:
    """Hard-delete an experiment (only if no campaigns are linked to it)."""
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    _load_experiment(sb, experiment_id, tenant_id)

    linked = (
        sb.table("campaigns")
        .select("id")
        .eq("experiment_id", experiment_id)
        .limit(1)
        .execute()
    )
    if linked.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Impossibile eliminare: l'esperimento ha campagne collegate. "
                "Terminalo (PATCH ended_at) invece di eliminarlo, così i dati "
                "storici rimangono intatti."
            ),
        )

    sb.table("template_experiments").delete().eq("id", experiment_id).execute()

    await audit_log(
        tenant_id,
        "experiment.deleted",
        actor_user_id=ctx.sub,
        target_table="template_experiments",
        target_id=experiment_id,
        diff={},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{experiment_id}/stats", response_model=ExperimentStats)
async def experiment_stats(
    experiment_id: str,
    ctx: CurrentUser,
) -> ExperimentStats:
    """Return per-variant metrics + Bayesian winner probability.

    Open rate and click rate are computed by joining campaigns
    (experiment_id + variant) with leads (outreach_opened/clicked_at).
    The Bayesian P(A > B) uses Beta(1 + conversions, 1 + non-conversions)
    priors with Monte Carlo sampling (12 000 draws, no external libs needed).
    """
    tenant_id = require_tenant(ctx)
    sb = get_service_client()
    _load_experiment(sb, experiment_id, tenant_id)

    # ------------------------------------------------------------------
    # 1) Pull all campaigns for this experiment in one query
    # ------------------------------------------------------------------
    camp_res = (
        sb.table("campaigns")
        .select("id, lead_id, experiment_variant, status")
        .eq("experiment_id", experiment_id)
        .in_("experiment_variant", ["a", "b"])
        .execute()
    )
    campaigns = camp_res.data or []

    if not campaigns:
        empty = VariantStats(sends=0, opens=0, clicks=0, open_rate=0.0, click_rate=0.0)
        return ExperimentStats(
            experiment_id=experiment_id,
            a=empty,
            b=empty,
            prob_a_wins_open=0.5,
            prob_a_wins_click=0.5,
            verdict_open="no_data",
            verdict_click="no_data",
            min_sample_met=False,
        )

    variant_leads: dict[str, set[str]] = {"a": set(), "b": set()}
    for c in campaigns:
        v = c.get("experiment_variant")
        lid = c.get("lead_id")
        if v in variant_leads and lid:
            variant_leads[v].add(lid)

    # ------------------------------------------------------------------
    # 2) Fetch open/click signals from the leads rows
    # ------------------------------------------------------------------
    all_lead_ids = list(variant_leads["a"] | variant_leads["b"])
    if all_lead_ids:
        leads_res = (
            sb.table("leads")
            .select("id, outreach_opened_at, outreach_clicked_at")
            .in_("id", all_lead_ids)
            .execute()
        )
        lead_signals: dict[str, dict[str, Any]] = {
            r["id"]: r for r in (leads_res.data or [])
        }
    else:
        lead_signals = {}

    def _count(lead_ids: set[str], field: str) -> int:
        return sum(1 for lid in lead_ids if lead_signals.get(lid, {}).get(field))

    stats_map: dict[str, VariantStats] = {}
    for v, lids in variant_leads.items():
        sends = len(lids)
        opens = _count(lids, "outreach_opened_at")
        clicks = _count(lids, "outreach_clicked_at")
        stats_map[v] = VariantStats(
            sends=sends,
            opens=opens,
            clicks=clicks,
            open_rate=round(opens / sends, 4) if sends else 0.0,
            click_rate=round(clicks / sends, 4) if sends else 0.0,
        )

    a = stats_map.get("a", VariantStats(sends=0, opens=0, clicks=0, open_rate=0.0, click_rate=0.0))
    b = stats_map.get("b", VariantStats(sends=0, opens=0, clicks=0, open_rate=0.0, click_rate=0.0))
    min_sample = a.sends >= MIN_SAMPLE_FOR_VERDICT and b.sends >= MIN_SAMPLE_FOR_VERDICT

    prob_open = _bayesian_prob_a_wins(a.opens, a.sends, b.opens, b.sends)
    prob_click = _bayesian_prob_a_wins(a.clicks, a.sends, b.clicks, b.sends)

    return ExperimentStats(
        experiment_id=experiment_id,
        a=a,
        b=b,
        prob_a_wins_open=round(prob_open, 3),
        prob_a_wins_click=round(prob_click, 3),
        verdict_open=_verdict(prob_open, min_sample),
        verdict_click=_verdict(prob_click, min_sample),
        min_sample_met=min_sample,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_experiment(sb: Any, experiment_id: str, tenant_id: str) -> dict[str, Any]:
    """Load experiment row or raise 404. Also enforces tenant ownership."""
    res = (
        sb.table("template_experiments")
        .select("*")
        .eq("id", experiment_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esperimento non trovato",
        )
    return rows[0]


def load_active_experiment(tenant_id: str) -> dict[str, Any] | None:
    """Return the running experiment for a tenant, or None.

    Called by the OutreachAgent at send-time. Returns None if:
      - no experiment exists
      - the experiment has ended (ended_at IS NOT NULL)
    """
    sb = get_service_client()
    try:
        res = (
            sb.table("template_experiments")
            .select("id, variant_a_subject, variant_b_subject, split_pct")
            .eq("tenant_id", tenant_id)
            .is_("ended_at", "null")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as exc:  # noqa: BLE001
        log.warning("experiments.load_active_failed", tenant_id=tenant_id, err=str(exc))
        return None


def _bayesian_prob_a_wins(
    conv_a: int, sends_a: int, conv_b: int, sends_b: int
) -> float:
    """P(rate_A > rate_B) via Monte Carlo with Beta(1+conv, 1+non-conv) priors.

    Uses only Python's standard-library ``random.betavariate``. At 12 000
    samples the standard error is ≈ 0.5%, sufficient for a binary
    winner-declaration threshold.
    """
    if sends_a == 0 or sends_b == 0:
        return 0.5

    non_a = max(0, sends_a - conv_a)
    non_b = max(0, sends_b - conv_b)
    wins = 0
    for _ in range(BAYESIAN_N_SAMPLES):
        rate_a = _random.betavariate(1 + conv_a, 1 + non_a)
        rate_b = _random.betavariate(1 + conv_b, 1 + non_b)
        if rate_a > rate_b:
            wins += 1
    return wins / BAYESIAN_N_SAMPLES


def _verdict(
    prob: float, min_sample: bool
) -> Literal["a_wins", "b_wins", "in_corso", "no_data"]:
    if not min_sample:
        return "no_data"
    if prob >= WINNER_THRESHOLD:
        return "a_wins"
    if prob <= (1 - WINNER_THRESHOLD):
        return "b_wins"
    return "in_corso"
