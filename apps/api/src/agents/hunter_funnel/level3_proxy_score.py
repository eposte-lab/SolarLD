"""Level 3 — Claude Haiku proxy scoring (no Solar).

Ranks enriched candidates 0–100 on "probability of having a commercially
viable roof" using only the desk-research signals collected in L1/L2:
ATECO sector, headcount, revenue, HQ location, website heuristics.

Why a cheap model on this rung: Haiku at ~€0.001/candidate is ~30× cheaper
than Solar (~€0.03) and ~15× cheaper than Sonnet. We batch up to 10
candidates per API call so the fixed prompt cost amortises over many
ranks, keeping total L3 cost <€5 on a 5000-candidate scan.

The prompt lives in `apps/api/src/prompts/proxy_score.md` so non-engineers
can tune it. The JSON schema is hard-coded here so a prompt edit that
breaks the output contract is caught by the parser, not in production.

Output: updates the `score`, `score_reasons`, `score_flags`,
`predicted_ateco_codes` columns and advances `stage` to 3. Returns
``ScoredCandidate``s sorted by descending score — L4 consumes this
ordering directly for the top-N cutoff.

Sector-aware (Sprint B.4):
  * When the tenant has ``target_wizard_groups``, each batch's prompt
    includes a ``target_sector`` block telling Haiku which settori the
    user cares about and which is the predicted match for each candidate.
  * The output JSON now carries ``sector_match_score`` and
    ``predicted_ateco_codes``. We post-validate the latter against
    ``ateco_google_types.ateco_code`` to reject LLM hallucinations.
  * Rule-based safety net: any candidate with sector_match_score < 30
    receives an automatic ``wrong_sector`` flag in addition to whatever
    Haiku reported.
"""

from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...core.config import settings
from ...core.logging import get_logger
from ...core.supabase_client import get_service_client
from ...services.claude_service import get_client
from .types import EnrichedCandidate, FunnelContext, ScoredCandidate

log = get_logger(__name__)

# Batch size = #candidates per Haiku call. Higher batches win on cached
# prompt amortisation but push against the max_tokens budget. 10 is a
# sweet spot: ~5K tokens in, ~1K tokens out, comfortably under the Haiku
# 200K context window and cheap to retry on partial failure.
_BATCH_SIZE = 10

# Concurrency: how many batches fly in parallel. Haiku's rate limit is
# generous (~50 RPM on paid tier) but we're conservative so a big scan
# doesn't starve other tenants' Claude calls (creative, replies, etc).
_BATCH_CONCURRENCY = 4

# Per-candidate cost estimate (cents). ~500 input tokens + ~100 output
# at Haiku 4.5 pricing (€0.001 per candidate). Conservative so we don't
# under-report budget consumption.
_COST_PER_CANDIDATE_CENTS = 1  # rounded up; true value ~0.1c


_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "proxy_score.md"


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    """Prompt is static per-process; read once from disk."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


async def run_level3(
    ctx: FunnelContext, candidates: list[EnrichedCandidate]
) -> list[ScoredCandidate]:
    """Score all enriched candidates, persist, return sorted by score desc."""
    if not candidates:
        return []

    batches = [
        candidates[i : i + _BATCH_SIZE]
        for i in range(0, len(candidates), _BATCH_SIZE)
    ]

    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def run_batch(batch: list[EnrichedCandidate]) -> list[ScoredCandidate]:
        async with sem:
            return await _score_batch(batch, ctx=ctx)

    batch_results = await asyncio.gather(*(run_batch(b) for b in batches))
    scored = [s for batch in batch_results for s in batch]

    # Tracker: Haiku billing + L3 candidate counter
    ctx.costs.add_claude(
        scored=len(scored),
        cost_cents=len(scored) * _COST_PER_CANDIDATE_CENTS,
    )

    _bulk_persist_l3(scored)

    # Sort desc — L4 assumes this ordering.
    scored.sort(key=lambda s: s.score, reverse=True)

    log.info(
        "funnel_l3_complete",
        extra={
            "tenant_id": ctx.tenant_id,
            "scan_id": ctx.scan_id,
            "scored": len(scored),
            "score_avg": (
                sum(s.score for s in scored) / len(scored) if scored else 0
            ),
            "score_p80": scored[len(scored) // 5].score if scored else 0,
        },
    )
    return scored


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


async def _score_batch(
    batch: list[EnrichedCandidate], *, ctx: FunnelContext
) -> list[ScoredCandidate]:
    """Score one batch with Haiku. Degrades to a fallback heuristic on
    parse failure so L3 output is always dense (every L2 candidate gets
    *some* score, L4 never receives `None`s it has to filter out).
    """
    sector_mode_active = bool(ctx.config.target_wizard_groups)
    prompt = _build_batch_prompt(
        batch,
        target_wizard_groups=ctx.config.target_wizard_groups,
    )
    system_prompt = _load_prompt()

    client = get_client()
    try:
        msg = await client.messages.create(
            model=settings.anthropic_haiku_model,
            max_tokens=2048,
            temperature=0.0,  # deterministic ranking
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                text = block.text  # type: ignore[attr-defined]
                break
    except Exception as exc:  # noqa: BLE001
        # Haiku call failed entirely → fallback on every candidate.
        log.warning(
            "l3_haiku_call_failed",
            extra={"tenant_id": ctx.tenant_id, "err": str(exc)},
        )
        return [_fallback_score(c) for c in batch]

    parsed = _parse_batch_response(text, expected_len=len(batch))
    if parsed is None:
        log.warning(
            "l3_parse_failed",
            extra={"tenant_id": ctx.tenant_id, "raw": text[:400]},
        )
        return [_fallback_score(c) for c in batch]

    # Sprint B.4 — validate predicted_ateco_codes from Haiku against the
    # seed table (rejects hallucinated ATECO codes). One async lookup per
    # batch, cached after the first call.
    valid_atecos = await _known_ateco_codes()

    out: list[ScoredCandidate] = []
    for cand, item in zip(batch, parsed, strict=True):
        flags = _str_list(item.get("flags"))
        sector_match = _maybe_clamp_score(item.get("sector_match_score"))

        # Rule-based safety net: when sector mode is active and Haiku
        # didn't already flag the candidate, force `wrong_sector` if
        # sector_match_score is decisively low.
        if (
            sector_mode_active
            and sector_match is not None
            and sector_match < 30
            and "wrong_sector" not in flags
        ):
            flags.append("wrong_sector")

        predicted_codes = _validate_ateco_codes(
            item.get("predicted_ateco_codes"), valid=valid_atecos
        )

        out.append(
            ScoredCandidate(
                candidate_id=cand.candidate_id,
                profile=cand.profile,
                enrichment=cand.enrichment,
                score=_clamp_score(item.get("score")),
                reasons=_str_list(item.get("reasons")),
                flags=flags,
                predicted_sector=cand.predicted_sector,
                sector_confidence=cand.sector_confidence,
                sector_match_score=sector_match,
                predicted_ateco_codes=predicted_codes,
            )
        )
    return out


def _build_batch_prompt(
    batch: list[EnrichedCandidate],
    *,
    target_wizard_groups: tuple[str, ...] | list[str] = (),
) -> str:
    """Serialise a batch of candidates into the compact JSON the model
    expects. Order matters — the response array is position-keyed.

    When ``target_wizard_groups`` is non-empty (sector-aware mode), we
    prepend a ``target_sector`` context block listing the tenant's
    enabled palettes and the per-candidate predicted_sector. The system
    prompt teaches Haiku how to weigh ``sector_match_score`` from this.
    """
    items: list[dict[str, Any]] = []
    for i, c in enumerate(batch):
        p = c.profile
        item: dict[str, Any] = {
            "idx": i,
            "name": p.legal_name,
            "ateco": p.ateco_code,
            "ateco_desc": p.ateco_description,
            "employees": p.employees,
            "revenue_eur": (
                p.yearly_revenue_cents // 100
                if p.yearly_revenue_cents
                else None
            ),
            "province": p.hq_province,
            "city": p.hq_city,
            "site_signals": c.enrichment.site_signals,
            "website": c.enrichment.website,
        }
        if target_wizard_groups:
            item["predicted_sector"] = c.predicted_sector
            item["sector_confidence"] = c.sector_confidence
        items.append(item)

    target_sector_block = ""
    if target_wizard_groups:
        target_sector_block = (
            "target_sector context (sector-aware mode active):\n"
            f"  enabled_wizard_groups: {list(target_wizard_groups)}\n"
            "  When evaluating each candidate, set sector_match_score "
            "0-100 based on alignment with their predicted_sector and "
            "the enabled_wizard_groups list. Add flag wrong_sector "
            "when the candidate is clearly fuori target.\n\n"
        )

    return (
        "Score each company below. Return a JSON object with a single "
        "key `results` whose value is an array of length "
        f"{len(batch)}, in the same order, each element shaped per "
        "the system prompt schema (score, sector_match_score, reasons, "
        "flags, predicted_ateco_codes).\n\n"
        f"{target_sector_block}"
        f"Companies (JSON):\n{json.dumps(items, ensure_ascii=False)}"
    )


# ---------------------------------------------------------------------------
# Sprint B.4 helpers — predicted_ateco validation, sector_match clamping
# ---------------------------------------------------------------------------


_KNOWN_ATECO_CACHE: set[str] | None = None


async def _known_ateco_codes() -> set[str]:
    """Lazy-load all ATECO codes that exist in ateco_google_types.

    Used to filter Haiku's ``predicted_ateco_codes`` so a hallucinated
    "99.99" never makes it into ``scan_candidates.predicted_ateco_codes``.
    """
    global _KNOWN_ATECO_CACHE
    if _KNOWN_ATECO_CACHE is not None:
        return _KNOWN_ATECO_CACHE

    sb = get_service_client()
    try:
        res = sb.table("ateco_google_types").select("ateco_code").execute()
        rows = res.data or []
        _KNOWN_ATECO_CACHE = {r["ateco_code"] for r in rows if r.get("ateco_code")}
    except Exception as exc:  # noqa: BLE001
        log.warning("l3_known_ateco_load_failed", extra={"err": str(exc)})
        _KNOWN_ATECO_CACHE = set()
    return _KNOWN_ATECO_CACHE


def _validate_ateco_codes(raw: Any, *, valid: set[str]) -> list[str]:
    """Cross-check Haiku's predicted ATECO codes against the seed table.

    Accepts both exact codes ("10.51") and 2-digit prefixes ("10").
    A prefix is considered valid when ANY full code in ``valid`` starts
    with it. Output is deduped and capped at 6 entries.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        e = entry.strip()
        if not e or e in seen:
            continue
        if e in valid:
            seen.add(e)
            out.append(e)
            continue
        # Treat short entry as prefix.
        if len(e) <= 2 and any(code.startswith(e) for code in valid):
            seen.add(e)
            out.append(e)
        if len(out) >= 6:
            break
    return out


def _maybe_clamp_score(value: Any) -> int | None:
    """Like ``_clamp_score`` but preserves None for nullable fields
    (``sector_match_score`` is null in legacy mode)."""
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, n))


def _parse_batch_response(
    text: str, *, expected_len: int
) -> list[dict[str, Any]] | None:
    """Parse the Haiku JSON. Tolerant to leading/trailing whitespace and
    accidental markdown fences the model sometimes emits despite the
    system prompt.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip ```json ... ``` fence
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    results = obj.get("results") if isinstance(obj, dict) else None
    if not isinstance(results, list) or len(results) != expected_len:
        return None
    return results


# ---------------------------------------------------------------------------
# Fallback + normalization
# ---------------------------------------------------------------------------


def _fallback_score(c: EnrichedCandidate) -> ScoredCandidate:
    """Rule-based score for when Haiku is unavailable. Coarse but keeps
    the funnel flowing: we still produce a reasonable ranking, just with
    less granularity than the model would.

    When ``predicted_sector`` is set we use it as a coarse
    ``sector_match_score`` proxy: 70 if the sector exists (predicted)
    and the confidence is exact, 50 if it was a fuzzy match, None
    otherwise.
    """
    p = c.profile
    score = 40  # neutral baseline

    # Size sweet spot
    if p.employees:
        if 20 <= p.employees <= 250:
            score += 20
        elif p.employees < 5 or p.employees > 500:
            score -= 15

    # Revenue sanity
    if p.yearly_revenue_cents:
        rev_eur = p.yearly_revenue_cents // 100
        if 2_000_000 <= rev_eur <= 50_000_000:
            score += 10

    # Industrial ATECO prefixes (manifattura, logistica, alimentare)
    ateco = (p.ateco_code or "")[:2]
    if ateco in {"10", "11", "13", "14", "15", "16", "17", "18", "19", "20",
                 "21", "22", "23", "24", "25", "26", "27", "28", "29", "30",
                 "31", "32", "33", "52"}:
        score += 15

    # Website signal match
    if c.enrichment.site_signals:
        score += min(10, len(c.enrichment.site_signals) * 3)

    sector_match: int | None = None
    if c.predicted_sector:
        if c.sector_confidence is not None:
            if c.sector_confidence >= 0.9:
                sector_match = 75
            elif c.sector_confidence >= 0.6:
                sector_match = 55
            else:
                sector_match = 40

    return ScoredCandidate(
        candidate_id=c.candidate_id,
        profile=p,
        enrichment=c.enrichment,
        score=_clamp_score(score),
        reasons=["fallback_heuristic"],
        flags=["haiku_unavailable"],
        predicted_sector=c.predicted_sector,
        sector_confidence=c.sector_confidence,
        sector_match_score=sector_match,
        predicted_ateco_codes=[],
    )


def _clamp_score(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if x is not None][:6]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _bulk_persist_l3(scored: list[ScoredCandidate]) -> None:
    if not scored:
        return
    sb = get_service_client()
    for s in scored:
        update: dict[str, Any] = {
            "score": s.score,
            "score_reasons": s.reasons,
            "score_flags": s.flags,
            "stage": 3,
            # Sprint B.4 — persist Haiku's validated predicted_ateco_codes
            # (empty list is fine; the column has NOT NULL DEFAULT '{}').
            "predicted_ateco_codes": list(s.predicted_ateco_codes or []),
        }
        try:
            sb.table("scan_candidates").update(update).eq(
                "id", str(s.candidate_id)
            ).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "l3_persist_failed",
                extra={"candidate_id": str(s.candidate_id), "err": str(exc)},
            )
