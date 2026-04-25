"""Immobiliare.it building-data matcher (Phase 2 enrichment).

What it returns
---------------
For a given Italian street address, returns a `BuildingMatch` describing:
  * Was the building found in our matched dataset? (matched / no_match
    / ambiguous / backend_error / backend_disabled)
  * Is it a multi-tenant property (condominio, palazzo uffici)?
  * Is it currently listed for sale / for rent?
  * Listing count + most-recent listing timestamp.

The orchestrator + offline filters use these signals to refine the
"proprietà" filter beyond what Atoka tells us.

Why a backend abstraction
-------------------------
The user has not yet decided WHICH source feeds this matcher:

  * **`null` backend** (Phase B default) — always returns
    `match_status='backend_disabled'`. The orchestrator treats this
    as "no enrichment available" and the proprietà filter falls back
    to its permissive PASS path. This is the SAFE default until a
    legal/commercial decision is taken.

  * **`partner_feed_v1`** (future) — official aggregator partnership
    (RemUe / Casa.it XML feed). Cost ~5 c€ / lookup. Lawful and stable.

  * **`scraper_v1`** (future, requires explicit ToS signoff) — direct
    scraping of immobiliare.it search results. We do NOT enable this
    autonomously. It must be a deliberate operator decision because
    immobiliare's ToS forbid bulk automation.

The matcher always reads/writes the `immobiliare_listings_cache` table
(migration 0058) regardless of backend. That guarantees:
  * one fetch per (address, 90d window) across the whole platform
  * deterministic re-runs
  * a clean rollback path if a backend gets disabled

Public API
----------
* `lookup(address: str, *, lat=None, lng=None) -> BuildingMatch`
* `set_backend(backend: BuildingMatchBackend)` — testing / DI seam

Caller is responsible for upstream throttling. The matcher does not
implement rate-limiting itself — that lives in the backend
implementation when it ships.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import structlog

from ..core.supabase_client import get_service_client

log = structlog.get_logger(__name__)

# Cache TTL must match the migration's `expires_at` default.
CACHE_TTL_DAYS = 90

# Backend identifier written to the cache row's `backend_name` column.
NULL_BACKEND_NAME = "null"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildingMatch:
    """Outcome of a matcher lookup. Always produced — even on errors."""

    match_status: str             # 'matched' | 'no_match' | 'ambiguous' | 'backend_error' | 'backend_disabled'
    building_type: str | None = None
    is_multi_tenant: bool | None = None
    is_for_sale: bool | None = None
    is_for_rent: bool | None = None
    listing_count: int | None = None
    last_listing_seen: datetime | None = None
    backend_name: str = NULL_BACKEND_NAME
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_match(self) -> bool:
        return self.match_status == "matched"


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


class BuildingMatchBackend(Protocol):
    """Pluggable backend.

    Implementations: NullBackend (default), PartnerFeedBackend (future),
    ScraperBackend (future, gated on ToS signoff).
    """

    name: str

    async def fetch(
        self,
        address_normalised: str,
        *,
        lat: float | None,
        lng: float | None,
    ) -> BuildingMatch: ...


class NullBackend:
    """Default Phase B backend.

    Returns `match_status='backend_disabled'` for every query. The
    orchestrator + filters interpret this as "no enrichment data
    available" and fall back to permissive defaults.
    """

    name = NULL_BACKEND_NAME

    async def fetch(
        self,
        address_normalised: str,
        *,
        lat: float | None,
        lng: float | None,
    ) -> BuildingMatch:
        # No IO. Pure stub. Logged once per call so it's visible in
        # observability when an operator wonders why the proprietà
        # filter never tightens.
        log.debug(
            "immobiliare_matcher.null_backend",
            address_normalised=address_normalised,
        )
        return BuildingMatch(
            match_status="backend_disabled",
            backend_name=NULL_BACKEND_NAME,
        )


# ---------------------------------------------------------------------------
# Module-level state (overridable for tests + future DI)
# ---------------------------------------------------------------------------

_backend: BuildingMatchBackend = NullBackend()


def set_backend(backend: BuildingMatchBackend) -> None:
    """Replace the active backend. Used by tests and by the future
    bootstrap when the operator opts into a real source."""

    global _backend
    _backend = backend


def get_backend() -> BuildingMatchBackend:
    return _backend


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def lookup(
    address: str,
    *,
    lat: float | None = None,
    lng: float | None = None,
) -> BuildingMatch:
    """Match an address against the immobiliare dataset.

    Behaviour:
      1. Normalise the address (strip CAP, city, punctuation, lower).
      2. Look up the cache; if a non-expired row exists, return it.
      3. Otherwise call the active backend, persist the result, return it.

    Never raises — backend errors are caught and surfaced as
    `match_status='backend_error'` so the offline filter loop stays
    side-effect free.
    """

    if not isinstance(address, str) or not address.strip():
        return BuildingMatch(
            match_status="no_match",
            backend_name=get_backend().name,
        )

    normalised = _normalise_address(address)
    addr_hash = _hash_address(normalised)

    cached = await _read_cache(addr_hash)
    if cached is not None:
        return cached

    try:
        result = await get_backend().fetch(normalised, lat=lat, lng=lng)
    except Exception as exc:  # noqa: BLE001 — defensively catch any backend bug
        log.warning(
            "immobiliare_matcher.backend_error",
            err=str(exc),
            backend=get_backend().name,
        )
        result = BuildingMatch(
            match_status="backend_error",
            backend_name=get_backend().name,
        )

    # Cache the result regardless of status — caching `no_match` and
    # `backend_error` prevents a re-query storm. The `backend_error`
    # row stays cached until expiry; an operator who fixes the backend
    # can call `invalidate(address)` to force a refresh.
    await _write_cache(
        address_normalised=normalised,
        address_hash=addr_hash,
        lat=lat,
        lng=lng,
        match=result,
    )
    return result


async def invalidate(address: str) -> None:
    """Remove the cached row for a given address.

    Useful after a backend swap or after a manual fix in the source
    dataset. Idempotent.
    """

    if not isinstance(address, str) or not address.strip():
        return
    addr_hash = _hash_address(_normalise_address(address))
    sb = get_service_client()
    try:
        await asyncio.to_thread(
            lambda: sb.table("immobiliare_listings_cache")
            .delete()
            .eq("address_hash", addr_hash)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("immobiliare_matcher.invalidate_failed", err=str(exc))


# ---------------------------------------------------------------------------
# Internals — normalisation, hashing, persistence
# ---------------------------------------------------------------------------


# Strip CAP (5 digits, optionally preceded by I- / IT-) and trailing
# province codes "(MI)", "(NA)", and excess whitespace. We keep the
# street + civic number and drop the rest — that's the right key
# granularity for "is this the same building".
_CAP_RE = re.compile(r"\b(?:IT-|I-)?\d{5}\b", re.IGNORECASE)
_PROVINCE_RE = re.compile(r"\(\s*[A-Za-z]{2}\s*\)")
# Strip punctuation AND hyphens — Italian addresses sometimes use a
# dash to separate civic number from CAP ("Via Roma 1 - 20121 Milano").
_PUNCT_RE = re.compile(r"[\.,;:\-]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalise_address(address: str) -> str:
    s = address.strip().lower()
    s = _CAP_RE.sub(" ", s)
    s = _PROVINCE_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    # If the address was passed as "via roma 1, milano" → after CAP/punct
    # strip we get "via roma 1 milano". We do NOT try to remove the city
    # because it's needed to disambiguate "via roma 1" across thousands
    # of Italian municipalities. Leaving the city in keeps the hash stable.
    return s


def _hash_address(normalised: str) -> str:
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


async def _read_cache(address_hash: str) -> BuildingMatch | None:
    sb = get_service_client()
    try:
        res = await asyncio.to_thread(
            lambda: sb.table("immobiliare_listings_cache")
            .select(
                "match_status, building_type, is_multi_tenant, is_for_sale, "
                "is_for_rent, listing_count, last_listing_seen, raw, "
                "backend_name, expires_at"
            )
            .eq("address_hash", address_hash)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("immobiliare_matcher.cache_read_failed", err=str(exc))
        return None

    rows = getattr(res, "data", None) or []
    if not rows:
        return None
    row = rows[0]

    # TTL check (defence-in-depth — we also have a partial index in SQL).
    expires_raw = row.get("expires_at")
    if expires_raw:
        try:
            expires_at = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(tz=timezone.utc):
                return None
        except ValueError:
            pass  # garbled — treat as miss

    last_seen_raw = row.get("last_listing_seen")
    last_seen: datetime | None = None
    if last_seen_raw:
        try:
            last_seen = datetime.fromisoformat(
                str(last_seen_raw).replace("Z", "+00:00")
            )
        except ValueError:
            last_seen = None

    return BuildingMatch(
        match_status=row["match_status"],
        building_type=row.get("building_type"),
        is_multi_tenant=row.get("is_multi_tenant"),
        is_for_sale=row.get("is_for_sale"),
        is_for_rent=row.get("is_for_rent"),
        listing_count=row.get("listing_count"),
        last_listing_seen=last_seen,
        backend_name=row.get("backend_name") or NULL_BACKEND_NAME,
        raw=row.get("raw") or {},
    )


async def _write_cache(
    *,
    address_normalised: str,
    address_hash: str,
    lat: float | None,
    lng: float | None,
    match: BuildingMatch,
) -> None:
    sb = get_service_client()
    payload: dict[str, Any] = {
        "address_normalised": address_normalised,
        "address_hash": address_hash,
        "lat": lat,
        "lng": lng,
        "match_status": match.match_status,
        "building_type": match.building_type,
        "is_multi_tenant": match.is_multi_tenant,
        "is_for_sale": match.is_for_sale,
        "is_for_rent": match.is_for_rent,
        "listing_count": match.listing_count,
        "last_listing_seen": (
            match.last_listing_seen.isoformat()
            if match.last_listing_seen is not None
            else None
        ),
        "raw": match.raw or {},
        "backend_name": match.backend_name,
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "expires_at": (
            datetime.now(tz=timezone.utc) + timedelta(days=CACHE_TTL_DAYS)
        ).isoformat(),
    }
    try:
        await asyncio.to_thread(
            lambda: sb.table("immobiliare_listings_cache")
            .upsert(payload, on_conflict="address_hash")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        # Cache write failure must not break the orchestrator. Log + continue.
        log.warning(
            "immobiliare_matcher.cache_write_failed",
            err=str(exc),
            address_hash=address_hash,
        )


# ---------------------------------------------------------------------------
# Convenience for tests / dashboards
# ---------------------------------------------------------------------------


def to_dict(match: BuildingMatch) -> dict[str, Any]:
    """Serialise a BuildingMatch (dataclass with a datetime field) to
    a JSON-safe dict. Used by the `/leads/[id]` inspector and by tests."""

    d = asdict(match)
    last = match.last_listing_seen
    d["last_listing_seen"] = last.isoformat() if last is not None else None
    return d
