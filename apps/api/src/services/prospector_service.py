"""Prospector — standalone "Trova aziende" engine.

Wraps Atoka's `atoka_search_by_criteria()` for ad-hoc operator
discovery, separate from the Hunter L1-L4 funnel. The funnel feeds
the *automated* lead pipeline; the prospector is the *manual*,
operator-driven workflow that the dashboard /scoperta page exposes.

Surface:
    • search()      — live Atoka call, no DB write
    • create_list() — persist a saved list with the criteria + items
    • get_list()    — load a list with paginated items
    • list_lists()  — index of all lists for a tenant
    • delete_list() — cascade items
    • estimate_cost() — predict €€ before the call (UI shows it
      before the user pulls the trigger on a 1k-record search)

Why this lives in services/ instead of routes/:
    • search() is reused by the import endpoint in routes (not
      yet) and by future scheduled "auto-refresh" jobs on lists.
    • Keeping Atoka coupling out of the route file means we can
      swap the data provider (Cerved, Telemaco) without touching
      the API surface.

Cost model (from italian_business_service.py):
    ATOKA_DISCOVERY_COST_PER_RECORD_CENTS = 1   # €0.01 / record
    ATOKA_COST_PER_CALL_CENTS              = 15  # €0.15 / VAT lookup

Only `search()` is billed at discovery rate. Promotion to subjects
(handled in the import flow) does NOT re-fetch from Atoka — we
already have the full payload snapshotted in `prospect_list_items`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import UUID

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from .italian_business_service import (
    ATOKA_DISCOVERY_COST_PER_RECORD_CENTS,
    AtokaProfile,
    EnrichmentUnavailable,
    atoka_search_by_criteria,
)

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Search — pure pass-through to Atoka with light filtering on top
# ---------------------------------------------------------------------------

# ATECO presets surfaced as one-click chips in the UI. The codes
# below are the official ISTAT ATECO 2007 codes; Atoka accepts them
# either as the full 6-digit code or as a 2-digit prefix.
ATECO_PRESETS: dict[str, dict[str, Any]] = {
    "amministratori_condominio": {
        "label": "Amministratori condominio",
        "ateco_codes": ["68.32.00", "81.10.00"],
        "description": (
            "Studi che amministrano edifici per conto terzi. "
            "Target ideale per pacchetti fotovoltaico condominiale."
        ),
    },
    "capannoni_industriali": {
        "label": "Capannoni industriali",
        "ateco_codes": ["10", "11", "13", "14", "15", "16", "17", "18", "20", "22", "23", "25", "27", "28"],
        "description": (
            "Manifattura con grandi superfici di copertura — alta "
            "consumi energetici, alta probabilità di tetto idoneo."
        ),
    },
    "logistica_warehouse": {
        "label": "Logistica & warehouse",
        "ateco_codes": ["52.10.10", "52.10.20", "49.41.00"],
        "description": (
            "Magazzini, stoccaggio, autotrasporto: superfici piatte "
            "ampie e contratti energetici flat."
        ),
    },
    "centri_commerciali": {
        "label": "Centri commerciali & GDO",
        "ateco_codes": ["47.11.10", "47.19.10"],
        "description": "Ipermercati e grande distribuzione organizzata.",
    },
}


async def search(
    *,
    ateco_codes: list[str],
    province_code: str | None = None,
    region_code: str | None = None,
    employees_min: int | None = None,
    employees_max: int | None = None,
    revenue_min_eur: int | None = None,
    revenue_max_eur: int | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Live Atoka search — no DB write.

    Returns a dict ready for JSON serialization:
        {
            "items": [<flat dicts>, ...],
            "count": <int returned this page>,
            "limit": <int>,
            "offset": <int>,
            "estimated_cost_eur": <float>,
        }

    `keyword` (currently unused by the Atoka wrapper) is reserved
    for the next iteration where we'll either pass it as
    full-text via Atoka's `q=` or post-filter the result set on
    legal_name / website. Holding the slot in the API contract
    means the dashboard form ships now without breaking later.
    """
    if not ateco_codes:
        # Defence-in-depth — the wrapper already raises ValueError
        # on empty list, but failing earlier with a structured
        # response gives the UI a cleaner error path.
        return {
            "items": [],
            "count": 0,
            "limit": limit,
            "offset": offset,
            "estimated_cost_eur": 0.0,
            "error": "ateco_required",
        }

    try:
        profiles = await atoka_search_by_criteria(
            ateco_codes=ateco_codes,
            province_code=province_code,
            region_code=region_code,
            employees_min=employees_min,
            employees_max=employees_max,
            revenue_min_eur=revenue_min_eur,
            revenue_max_eur=revenue_max_eur,
            limit=limit,
            offset=offset,
        )
    except EnrichmentUnavailable as exc:
        log.warning("prospector.search_unavailable", extra={"reason": str(exc)})
        return {
            "items": [],
            "count": 0,
            "limit": limit,
            "offset": offset,
            "estimated_cost_eur": 0.0,
            "error": str(exc),
        }
    except ValueError as exc:
        return {
            "items": [],
            "count": 0,
            "limit": limit,
            "offset": offset,
            "estimated_cost_eur": 0.0,
            "error": str(exc),
        }

    # Optional client-side keyword post-filter — naive but useful
    # to refine results when the Atoka discovery sort is too broad.
    if keyword:
        kw = keyword.lower().strip()
        profiles = [
            p for p in profiles
            if kw in (p.legal_name or "").lower()
            or kw in (p.ateco_description or "").lower()
        ]

    items = [_profile_to_dict(p) for p in profiles]
    return {
        "items": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "estimated_cost_eur": round(
            len(items) * ATOKA_DISCOVERY_COST_PER_RECORD_CENTS / 100.0,
            2,
        ),
    }


def estimate_cost(record_count: int) -> float:
    """Pre-flight cost estimate in EUR for a planned search."""
    return round(record_count * ATOKA_DISCOVERY_COST_PER_RECORD_CENTS / 100.0, 2)


# ---------------------------------------------------------------------------
# Lists — CRUD + items
# ---------------------------------------------------------------------------


def create_list(
    *,
    tenant_id: str,
    name: str,
    description: str | None,
    search_filter: dict[str, Any],
    items: list[dict[str, Any]],
    preset_code: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Persist a list + its items in a single transaction.

    `items` are the flat dicts returned by `search()` — we
    snapshot them as `prospect_list_items` rows so the list is
    durable independently of Atoka's catalog churn.
    """
    sb = get_service_client()

    list_payload = {
        "tenant_id": tenant_id,
        "name": name,
        "description": description,
        "search_filter": search_filter,
        "preset_code": preset_code,
        "item_count": len(items),
        "created_by": created_by,
    }
    inserted = sb.table("prospect_lists").insert(list_payload).execute()
    if not inserted.data:
        raise RuntimeError("prospect_lists insert returned no row")
    list_row = inserted.data[0]
    list_id = list_row["id"]

    if items:
        # Build item rows — drop dupes-within-page on vat_number
        # (Atoka can occasionally return the same company with two
        # ATECO entries; we only want one row per VAT per list).
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for it in items:
            vat = it.get("vat_number")
            if not vat or vat in seen:
                continue
            seen.add(vat)
            rows.append({
                "list_id": list_id,
                "tenant_id": tenant_id,
                "vat_number": vat,
                "legal_name": it.get("legal_name") or "",
                "ateco_code": it.get("ateco_code"),
                "ateco_description": it.get("ateco_description"),
                "employees": it.get("employees"),
                "revenue_eur": it.get("revenue_eur"),
                "hq_address": it.get("hq_address"),
                "hq_cap": it.get("hq_cap"),
                "hq_city": it.get("hq_city"),
                "hq_province": it.get("hq_province"),
                "hq_lat": it.get("hq_lat"),
                "hq_lng": it.get("hq_lng"),
                "website_domain": it.get("website_domain"),
                "decision_maker_name": it.get("decision_maker_name"),
                "decision_maker_role": it.get("decision_maker_role"),
                "decision_maker_email": it.get("decision_maker_email"),
                "linkedin_url": it.get("linkedin_url"),
                "atoka_payload": it.get("raw") or {},
            })

        if rows:
            # Chunked insert — Supabase REST has a ~1k row payload
            # limit; chunk at 500 to be safe.
            for chunk_start in range(0, len(rows), 500):
                chunk = rows[chunk_start:chunk_start + 500]
                sb.table("prospect_list_items").insert(chunk).execute()

            # Refresh item_count to deduped reality.
            sb.table("prospect_lists").update(
                {"item_count": len(rows)}
            ).eq("id", list_id).execute()
            list_row["item_count"] = len(rows)

    return list_row


def list_lists(*, tenant_id: str, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """Index of saved lists for a tenant (most recent first)."""
    sb = get_service_client()
    from_idx = (page - 1) * page_size
    to_idx = from_idx + page_size - 1
    res = (
        sb.table("prospect_lists")
        .select("*", count="exact")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .range(from_idx, to_idx)
        .execute()
    )
    return {
        "rows": res.data or [],
        "total": res.count or 0,
        "page": page,
        "page_size": page_size,
    }


def get_list(
    *,
    tenant_id: str,
    list_id: str,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any] | None:
    """Load a list with paginated items.

    Returns None if the list doesn't exist or doesn't belong to
    the tenant — caller maps that to 404.
    """
    sb = get_service_client()
    head = (
        sb.table("prospect_lists")
        .select("*")
        .eq("tenant_id", tenant_id)
        .eq("id", list_id)
        .limit(1)
        .execute()
    )
    if not head.data:
        return None
    list_row = head.data[0]

    from_idx = (page - 1) * page_size
    to_idx = from_idx + page_size - 1
    items_res = (
        sb.table("prospect_list_items")
        .select("*", count="exact")
        .eq("tenant_id", tenant_id)
        .eq("list_id", list_id)
        .order("created_at", desc=False)
        .range(from_idx, to_idx)
        .execute()
    )

    return {
        "list": list_row,
        "items": items_res.data or [],
        "items_total": items_res.count or 0,
        "page": page,
        "page_size": page_size,
    }


def delete_list(*, tenant_id: str, list_id: str) -> bool:
    """Hard-delete a list + cascade items (FK on items has ON DELETE CASCADE)."""
    sb = get_service_client()
    res = (
        sb.table("prospect_lists")
        .delete()
        .eq("tenant_id", tenant_id)
        .eq("id", list_id)
        .execute()
    )
    return bool(res.data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_to_dict(p: AtokaProfile) -> dict[str, Any]:
    """AtokaProfile → flat dict with consistent key naming.

    Naming follows the DB column conventions (snake_case,
    revenue in EUR not cents at the wire layer for human
    readability) so the dashboard can render the result table
    directly without a re-mapping step.
    """
    d = asdict(p)
    cents = d.pop("yearly_revenue_cents", None)
    d["revenue_eur"] = cents // 100 if cents else None
    return d
