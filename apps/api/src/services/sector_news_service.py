"""Sector news catalogue lookup.

Powers the engagement-based follow-up copy by surfacing a
*sector-relevant* hook — never something derived from tracked behavior.

The copy rule (per design doc): the lead must NEVER feel observed. We
say "in the metalmeccanico sector, steel +18%" because that's a public
fact about the sector, not "you opened the email".

Lookup priority for `pick_news(supabase, tenant_id, ateco_code)`:
    1. Tenant-specific row matching ATECO 2-digit, status=active
    2. Global seed (tenant_id IS NULL), ATECO 2-digit, status=active
    3. None — caller falls back to a generic Jinja default

When multiple rows match the same priority bucket, the most recently
updated wins (rotation: operators bump `updated_at` to bring a row to
the top).
"""

from __future__ import annotations

from typing import Any, TypedDict


class SectorNews(TypedDict):
    id: str
    tenant_id: str | None
    ateco_2digit: str
    headline: str
    body: str
    source_url: str | None


async def pick_news(
    supabase: Any,
    *,
    tenant_id: str,
    ateco_code: str | None,
) -> SectorNews | None:
    """Return the best matching news row for this tenant + sector, or None.

    ``ateco_code`` may be the full code (e.g. "41.20.00") — only the
    leading 2 chars are used for the lookup. ``None`` falls through to
    no-news (template uses generic copy).
    """
    if not ateco_code:
        return None
    two_digit = str(ateco_code)[:2]
    if not two_digit.isdigit() or len(two_digit) != 2:
        return None

    # 1. Tenant-specific
    res = (
        supabase.table("sector_news")
        .select("id,tenant_id,ateco_2digit,headline,body,source_url,updated_at")
        .eq("tenant_id", tenant_id)
        .eq("ateco_2digit", two_digit)
        .eq("status", "active")
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if rows:
        return _to_dto(rows[0])

    # 2. Global seed (tenant_id IS NULL)
    res = (
        supabase.table("sector_news")
        .select("id,tenant_id,ateco_2digit,headline,body,source_url,updated_at")
        .is_("tenant_id", "null")
        .eq("ateco_2digit", two_digit)
        .eq("status", "active")
        .order("updated_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if rows:
        return _to_dto(rows[0])

    return None


def _to_dto(row: dict[str, Any]) -> SectorNews:
    return SectorNews(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]) if row.get("tenant_id") else None,
        ateco_2digit=str(row["ateco_2digit"]),
        headline=str(row["headline"]),
        body=str(row["body"]),
        source_url=row.get("source_url"),
    )


# ---------------------------------------------------------------------------
# Tenant CRUD helpers (used by the dashboard /settings/sector-news page)
# ---------------------------------------------------------------------------
async def list_for_tenant(supabase: Any, tenant_id: str) -> list[dict[str, Any]]:
    """All sector news visible to this tenant — own + global, active first."""
    res = (
        supabase.table("sector_news")
        .select("*")
        .or_(f"tenant_id.eq.{tenant_id},tenant_id.is.null")
        .order("status", desc=False)
        .order("ateco_2digit", desc=False)
        .order("updated_at", desc=True)
        .execute()
    )
    return list(res.data or [])


async def upsert_news(
    supabase: Any,
    *,
    tenant_id: str,
    news_id: str | None,
    ateco_2digit: str,
    headline: str,
    body: str,
    source_url: str | None,
    status: str = "active",
) -> dict[str, Any]:
    """Insert (when news_id is None) or update an existing tenant-owned row.

    Global rows (``tenant_id IS NULL``) are read-only here — operators
    create their own override row instead, which takes priority.
    """
    payload = {
        "tenant_id": tenant_id,
        "ateco_2digit": ateco_2digit[:2],
        "headline": headline,
        "body": body,
        "source_url": source_url,
        "status": status,
    }
    if news_id is None:
        res = supabase.table("sector_news").insert(payload).execute()
        return (res.data or [None])[0]

    res = (
        supabase.table("sector_news")
        .update(payload)
        .eq("id", news_id)
        .eq("tenant_id", tenant_id)  # safety: never let a tenant edit a global
        .execute()
    )
    return (res.data or [None])[0]


async def archive_news(
    supabase: Any, *, tenant_id: str, news_id: str
) -> bool:
    """Soft-archive a tenant-owned row."""
    res = (
        supabase.table("sector_news")
        .update({"status": "archived"})
        .eq("id", news_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    return bool(res.data)
