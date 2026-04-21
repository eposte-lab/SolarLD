"""B2C audience materialisation — builds `b2c_audiences` rows from
`geo_income_stats` filtered by the tenant's Sorgente module.

Audience creation is the *only* scan-time artefact for
`scan_mode='b2c_residential'`. No Solar, no leads, no per-address
enrichment. The audience is the thing the tenant acts on (letters,
Meta ads, door-to-door export).

Income bucketing:
    basso     reddito_medio_eur <  25_000
    medio     25_000 ≤ reddito < 40_000
    alto      40_000 ≤ reddito < 60_000
    premium   reddito ≥ 60_000

Buckets are snapshots — stored on the row so future ISTAT refreshes
don't silently reshuffle historical audiences. Letter-template A/B
tests will segment on `reddito_bucket` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------


def income_bucket(reddito_eur: int | None) -> str:
    """Map ISTAT average income to a coarse affluence bucket.

    Rural CAPs frequently come in below €25k average even when they
    house sizeable single-family homes — we tag them `basso` but they
    still pass tenant filters with low `reddito_min_eur`. Bucketing is
    purely for segmentation, not filtering.
    """
    if reddito_eur is None:
        return "basso"
    if reddito_eur < 25_000:
        return "basso"
    if reddito_eur < 40_000:
        return "medio"
    if reddito_eur < 60_000:
        return "alto"
    return "premium"


# ---------------------------------------------------------------------------
# Territory → CAP expansion
# ---------------------------------------------------------------------------


def _geo_filters_from_territory(territory: dict[str, Any]) -> dict[str, Any]:
    """Lift territory selection into Supabase eq/in filters for
    `geo_income_stats`. Mirrors the B2B Level 1 helper but for CAP
    scope (we have CAP as PK here, not a free-text location field).
    """
    t_type = (territory.get("type") or "").lower()
    code = (territory.get("code") or "").strip()
    meta = territory.get("metadata") or {}
    if t_type == "cap" and code:
        return {"cap_in": [code]}
    if t_type == "provincia" and code:
        return {"provincia": code.upper()[:3]}
    if t_type == "regione" and code:
        return {"regione": code}
    # Fallbacks for composite territories with metadata
    if "provincia" in meta:
        return {"provincia": str(meta["provincia"]).upper()[:3]}
    return {}


@dataclass(slots=True, frozen=True)
class AudienceFilters:
    """Thin typed view of the subset of `sorgente` module config this
    service cares about. Let callers hand us a plain dict or this
    dataclass — we accept both."""

    reddito_min_eur: int = 35_000
    case_unifamiliari_pct_min: int = 40

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> "AudienceFilters":
        cfg = cfg or {}
        return cls(
            reddito_min_eur=int(cfg.get("reddito_min_eur") or 0),
            case_unifamiliari_pct_min=int(
                cfg.get("case_unifamiliari_pct_min") or 0
            ),
        )


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


async def select_caps(
    *,
    territory: dict[str, Any],
    filters: AudienceFilters,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return CAP rows matching the territory + tenant ICP.

    We cap the result to 500 CAPs by default — that's enough to
    produce a large multi-million-household audience without letting a
    misconfigured regione='Italia' scan load every single CAP in the
    country (~45k) into memory.
    """
    sb = get_service_client()
    geo = _geo_filters_from_territory(territory)

    q = sb.table("geo_income_stats").select(
        "cap, provincia, regione, comune, reddito_medio_eur, "
        "popolazione, case_unifamiliari_pct"
    )
    if "cap_in" in geo:
        q = q.in_("cap", geo["cap_in"])
    if "provincia" in geo:
        q = q.eq("provincia", geo["provincia"])
    if "regione" in geo:
        q = q.eq("regione", geo["regione"])
    if filters.reddito_min_eur > 0:
        q = q.gte("reddito_medio_eur", filters.reddito_min_eur)
    if filters.case_unifamiliari_pct_min > 0:
        q = q.gte("case_unifamiliari_pct", filters.case_unifamiliari_pct_min)

    # Affluent + detached-house CAPs first — these are the high-value
    # targets for solar door-to-door / letter campaigns.
    q = q.order("reddito_medio_eur", desc=True).limit(limit)
    res = q.execute()
    return list(getattr(res, "data", None) or [])


async def materialise_audiences(
    *,
    tenant_id: UUID | str,
    scan_id: UUID | str,
    territory_id: UUID | str | None,
    territory: dict[str, Any],
    filters: AudienceFilters,
    channels_active: list[str],
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Create/refresh `b2c_audiences` rows for one B2C scan.

    Idempotent on (tenant_id, scan_id, cap) — re-running the same scan
    updates channel availability + stima_contatti but preserves
    letters_sent/replies/qualified_roofs counters.
    """
    rows = await select_caps(
        territory=territory, filters=filters, limit=limit
    )
    if not rows:
        return []

    tid = str(tenant_id)
    sid = str(scan_id)
    terr_id = str(territory_id) if territory_id else None

    payload: list[dict[str, Any]] = []
    for r in rows:
        reddito = r.get("reddito_medio_eur")
        popolazione = int(r.get("popolazione") or 0)
        # Coarse contact estimate — assume average household size of
        # 2.3 (ISTAT), half single-family where `case_unifamiliari_pct`
        # absent, multiply by the CAP's detached-house share.
        unif_pct = float(
            r.get("case_unifamiliari_pct")
            or 50
        ) / 100.0
        stima = int(popolazione / 2.3 * unif_pct)

        payload.append(
            {
                "tenant_id": tid,
                "scan_id": sid,
                "territory_id": terr_id,
                "cap": r["cap"],
                "provincia": r["provincia"],
                "regione": r["regione"],
                "reddito_bucket": income_bucket(reddito),
                "stima_contatti": max(stima, 0),
                "canali_attivi": list(channels_active),
            }
        )

    sb = get_service_client()
    # We split on_conflict fields by comma — Supabase expects exactly
    # the composite UNIQUE constraint columns.
    sb.table("b2c_audiences").upsert(
        payload, on_conflict="tenant_id,scan_id,cap"
    ).execute()

    log.info(
        "b2c_audiences.materialised",
        extra={
            "tenant_id": tid,
            "scan_id": sid,
            "count": len(payload),
            "sample_cap": payload[0]["cap"] if payload else None,
        },
    )
    return payload


async def list_audiences_for_scan(
    tenant_id: UUID | str, scan_id: UUID | str
) -> list[dict[str, Any]]:
    """Read helper used by the B2C routes (export, trigger campaign)."""
    sb = get_service_client()
    res = (
        sb.table("b2c_audiences")
        .select("*")
        .eq("tenant_id", str(tenant_id))
        .eq("scan_id", str(scan_id))
        .execute()
    )
    return list(getattr(res, "data", None) or [])


async def get_audience(
    audience_id: UUID | str, tenant_id: UUID | str
) -> dict[str, Any] | None:
    """Fetch a single audience by id, scoped to tenant (defence in
    depth — routes already check auth, but this makes the constraint
    explicit at the data layer too)."""
    sb = get_service_client()
    res = (
        sb.table("b2c_audiences")
        .select("*")
        .eq("id", str(audience_id))
        .eq("tenant_id", str(tenant_id))
        .maybe_single()
        .execute()
    )
    return getattr(res, "data", None)
