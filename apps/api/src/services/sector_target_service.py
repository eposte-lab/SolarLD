"""Sector-aware target service — bridges `tenant.target_wizard_groups`
to the per-sector palette stored in `ateco_google_types`.

The hunter funnel uses this service to:

  * Derive the set of ATECO prefixes the tenant cares about from a
    list of wizard_group palettes (when `ateco_codes` is left empty
    in the Sorgente module — the tenant relies on a sector palette).
  * Predict which wizard_group a single candidate belongs to (L1 →
    `scan_candidates.predicted_sector`).
  * Load the full `SectorAreaMapping` (places_keywords,
    site_signal_keywords, osm_landuse_hints, ...) for one
    wizard_group, used by L2 enrichment, L3 prompt rendering, and
    BIC stage-4 voting.

All lookups are cached in-process via `lru_cache` keyed by
`wizard_group` — the seed table changes only via migration so a long
TTL is fine. The cache is keyed on the wizard_group string, not on
the Supabase client (which isn't hashable), so callers can reuse it
across requests.

Migration coupling: this module assumes the columns added by
``packages/db/migrations/0097_ateco_google_types_sector_extend.sql``
exist. It never creates rows — operators (or the seed migration 0098)
populate the table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class OsmTagHint:
    """One OSM-tag-with-weight signal. Used in BIC stage 4 voting."""

    tag_key: str          # 'landuse' or 'building' or 'amenity', etc.
    tag_value: str        # 'industrial', 'warehouse', 'hotel', etc.
    weight: float         # 0..1 — additive bonus to the building's vote


@dataclass(slots=True, frozen=True)
class SectorAreaMapping:
    """Snapshot of one sector palette from ``ateco_google_types``.

    A wizard_group typically has multiple ATECO codes; this object
    aggregates the palette across them (UNION on lists, max on
    numeric ranges). Constructed by ``get_sector_config_by_wizard_group``.
    """

    wizard_group: str
    ateco_codes: list[str] = field(default_factory=list)
    osm_landuse_hints: list[OsmTagHint] = field(default_factory=list)
    osm_additional_tags: list[OsmTagHint] = field(default_factory=list)
    places_keywords: list[str] = field(default_factory=list)
    places_excluded_types: list[str] = field(default_factory=list)
    site_signal_keywords: list[str] = field(default_factory=list)
    min_zone_area_m2: int | None = None
    search_radius_m: int = 1500
    typical_kwp_range_min: int | None = None
    typical_kwp_range_max: int | None = None


# ---------------------------------------------------------------------------
# Internal cache: wizard_group -> SectorAreaMapping
# ---------------------------------------------------------------------------
#
# We cache one dict at a time (not per row) because callers always need
# the merged palette across a wizard_group's rows. The cache is async-
# safe by virtue of being read-only after warm-up.
_PALETTE_CACHE: dict[str, SectorAreaMapping] = {}
_ATECO_TO_GROUP_CACHE: dict[str, str] = {}
_CACHE_WARM = False


def _reset_cache_for_tests() -> None:
    """Test helper. Clears the in-process cache so the next call refreshes."""
    global _CACHE_WARM
    _PALETTE_CACHE.clear()
    _ATECO_TO_GROUP_CACHE.clear()
    _CACHE_WARM = False


async def _warm_cache(supabase: Any) -> None:
    """Load all rows from ``ateco_google_types`` and group by wizard_group.

    Fire-once. Subsequent calls are no-ops. The function is idempotent
    so concurrent callers get the same warm cache (the first one wins;
    others see _CACHE_WARM=True on retry).
    """
    global _CACHE_WARM
    if _CACHE_WARM:
        return

    res = (
        supabase.table("ateco_google_types")
        .select(
            "ateco_code, wizard_group, "
            "osm_landuse_hints, osm_additional_tags, "
            "places_keywords, places_excluded_types, "
            "site_signal_keywords, min_zone_area_m2, search_radius_m, "
            "typical_kwp_range_min, typical_kwp_range_max"
        )
        .execute()
    )
    rows = res.data or []

    # Aggregate per wizard_group.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        wg = row.get("wizard_group")
        if not wg:
            continue
        grouped.setdefault(wg, []).append(row)
        ateco = row.get("ateco_code")
        if ateco:
            # Last writer wins — fine because each ateco_code maps to
            # exactly one wizard_group (PK constraint upstream).
            _ATECO_TO_GROUP_CACHE[ateco] = wg

    for wg, entries in grouped.items():
        _PALETTE_CACHE[wg] = _build_mapping_from_rows(wg, entries)

    _CACHE_WARM = True
    log.info(
        "sector_target.cache_warmed",
        wizard_groups=len(_PALETTE_CACHE),
        ateco_codes=len(_ATECO_TO_GROUP_CACHE),
    )


def _build_mapping_from_rows(
    wizard_group: str, rows: list[dict[str, Any]]
) -> SectorAreaMapping:
    """Merge multiple ATECO rows of the same wizard_group into one palette."""
    ateco_codes: list[str] = []
    landuse: list[OsmTagHint] = []
    additional: list[OsmTagHint] = []
    places_kw: list[str] = []
    places_excl: list[str] = []
    site_kw: list[str] = []
    min_area: int | None = None
    radius_m = 1500
    kwp_min: int | None = None
    kwp_max: int | None = None

    seen_landuse: set[tuple[str, str]] = set()
    seen_additional: set[tuple[str, str]] = set()
    seen_places_kw: set[str] = set()
    seen_places_excl: set[str] = set()
    seen_site_kw: set[str] = set()
    seen_ateco: set[str] = set()

    for row in rows:
        code = row.get("ateco_code")
        if code and code not in seen_ateco:
            seen_ateco.add(code)
            ateco_codes.append(code)

        for hint in _parse_osm_hints(row.get("osm_landuse_hints"), default_key="landuse"):
            key = (hint.tag_key, hint.tag_value)
            if key not in seen_landuse:
                seen_landuse.add(key)
                landuse.append(hint)

        for hint in _parse_osm_hints(row.get("osm_additional_tags"), default_key=None):
            key = (hint.tag_key, hint.tag_value)
            if key not in seen_additional:
                seen_additional.add(key)
                additional.append(hint)

        for kw in row.get("places_keywords") or []:
            kws = str(kw).strip().lower()
            if kws and kws not in seen_places_kw:
                seen_places_kw.add(kws)
                places_kw.append(kws)

        for kw in row.get("places_excluded_types") or []:
            kws = str(kw).strip().lower()
            if kws and kws not in seen_places_excl:
                seen_places_excl.add(kws)
                places_excl.append(kws)

        for kw in row.get("site_signal_keywords") or []:
            kws = str(kw).strip().lower()
            if kws and kws not in seen_site_kw:
                seen_site_kw.add(kws)
                site_kw.append(kws)

        # Take the smallest min_zone_area_m2 and largest search_radius_m
        # across rows of the same group — most permissive wins so the
        # palette covers the union.
        row_min = row.get("min_zone_area_m2")
        if row_min is not None:
            row_min_int = int(row_min)
            if min_area is None or row_min_int < min_area:
                min_area = row_min_int

        row_radius = row.get("search_radius_m")
        if row_radius is not None:
            row_radius_int = int(row_radius)
            if row_radius_int > radius_m:
                radius_m = row_radius_int

        row_kwp_min = row.get("typical_kwp_range_min")
        if row_kwp_min is not None:
            row_kwp_min_int = int(row_kwp_min)
            if kwp_min is None or row_kwp_min_int < kwp_min:
                kwp_min = row_kwp_min_int

        row_kwp_max = row.get("typical_kwp_range_max")
        if row_kwp_max is not None:
            row_kwp_max_int = int(row_kwp_max)
            if kwp_max is None or row_kwp_max_int > kwp_max:
                kwp_max = row_kwp_max_int

    return SectorAreaMapping(
        wizard_group=wizard_group,
        ateco_codes=ateco_codes,
        osm_landuse_hints=landuse,
        osm_additional_tags=additional,
        places_keywords=places_kw,
        places_excluded_types=places_excl,
        site_signal_keywords=site_kw,
        min_zone_area_m2=min_area,
        search_radius_m=radius_m,
        typical_kwp_range_min=kwp_min,
        typical_kwp_range_max=kwp_max,
    )


def _parse_osm_hints(
    raw: Any, *, default_key: str | None
) -> list[OsmTagHint]:
    """Parse a JSONB list like [{"landuse":"industrial","weight":1.0}, ...]
    into a list of `OsmTagHint`. Tolerant: skips malformed entries.
    The ``default_key`` parameter is used for compact ``osm_landuse_hints``
    rows where each dict has the landuse-key implied (currently the seed
    is verbose so this is mostly a guard)."""
    if not raw:
        return []
    if not isinstance(raw, list):
        return []
    out: list[OsmTagHint] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        weight_raw = entry.get("weight")
        try:
            weight = float(weight_raw) if weight_raw is not None else 1.0
        except (TypeError, ValueError):
            weight = 1.0
        # Pick the first non-weight key as tag_key/value.
        for k, v in entry.items():
            if k == "weight":
                continue
            if not isinstance(v, str):
                continue
            out.append(OsmTagHint(tag_key=str(k), tag_value=v, weight=weight))
            break
        else:
            # No usable tag — skip.
            continue
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_sector_config_by_wizard_group(
    supabase: Any, *, wizard_group: str
) -> SectorAreaMapping | None:
    """Load the palette for one wizard_group. Cached.

    Returns ``None`` when the wizard_group doesn't exist in
    `ateco_google_types`. Callers should fall back to legacy mode in
    that case (or surface a config error).
    """
    await _warm_cache(supabase)
    return _PALETTE_CACHE.get(wizard_group)


async def derive_ateco_whitelist(
    supabase: Any, *, wizard_groups: list[str] | tuple[str, ...]
) -> list[str]:
    """Compute the union of ATECO codes for the given wizard_groups.

    Used by L1 when the tenant left ``ateco_codes`` empty and relies on
    `target_wizard_groups` instead. Order is preserved (first group
    first), duplicates deduped. Empty input → empty list.
    """
    if not wizard_groups:
        return []
    await _warm_cache(supabase)
    seen: set[str] = set()
    out: list[str] = []
    for wg in wizard_groups:
        mapping = _PALETTE_CACHE.get(wg)
        if mapping is None:
            log.warning(
                "sector_target.wizard_group_unknown",
                wizard_group=wg,
            )
            continue
        for code in mapping.ateco_codes:
            if code not in seen:
                seen.add(code)
                out.append(code)
    return out


async def predict_sector_for_candidate(
    supabase: Any,
    *,
    ateco_code: str | None,
    business_name: str | None,
    enabled_wizard_groups: list[str] | tuple[str, ...],
) -> tuple[str, float] | None:
    """Best-effort prediction of which wizard_group a candidate belongs to.

    Strategy (cheap, deterministic, no LLM):

      1. Exact ATECO → wizard_group lookup (highest confidence: 1.0).
      2. ATECO 2-digit prefix match against any code in any enabled group.
         Multiple matches → pick the one with the most ATECO codes in
         that group (most "natural fit"). Confidence: 0.7.
      3. Fuzzy site_signal_keyword hit on business_name. Confidence: 0.4.
      4. None of the above → return None.

    Restricted to ``enabled_wizard_groups`` so the prediction never
    suggests a group the tenant hasn't opted into.
    """
    if not enabled_wizard_groups:
        return None
    await _warm_cache(supabase)

    enabled = set(enabled_wizard_groups)

    # Path 1: exact ATECO.
    if ateco_code:
        wg = _ATECO_TO_GROUP_CACHE.get(ateco_code)
        if wg and wg in enabled:
            return (wg, 1.0)

        # Path 2: 2-digit prefix
        prefix = ateco_code.split(".")[0] if "." in ateco_code else ateco_code[:2]
        if prefix:
            best: tuple[str, int] | None = None
            for wg in enabled:
                mapping = _PALETTE_CACHE.get(wg)
                if mapping is None:
                    continue
                matches = sum(
                    1
                    for c in mapping.ateco_codes
                    if c.split(".")[0] == prefix or c.startswith(prefix)
                )
                if matches > 0 and (best is None or matches > best[1]):
                    best = (wg, matches)
            if best:
                return (best[0], 0.7)

    # Path 3: fuzzy site_signal_keyword on business name.
    if business_name:
        name_low = business_name.lower()
        best_kw: tuple[str, int] | None = None
        for wg in enabled:
            mapping = _PALETTE_CACHE.get(wg)
            if mapping is None:
                continue
            hits = sum(1 for kw in mapping.site_signal_keywords if kw and kw in name_low)
            if hits > 0 and (best_kw is None or hits > best_kw[1]):
                best_kw = (wg, hits)
        if best_kw:
            return (best_kw[0], 0.4)

    return None


async def get_wizard_group_for_ateco(
    supabase: Any, *, ateco_code: str
) -> str | None:
    """Direct lookup: which wizard_group owns this ATECO code? Cached."""
    if not ateco_code:
        return None
    await _warm_cache(supabase)
    return _ATECO_TO_GROUP_CACHE.get(ateco_code)


async def union_site_signal_keywords(
    supabase: Any, *, wizard_groups: list[str] | tuple[str, ...]
) -> list[str]:
    """Union of site_signal_keywords across the given wizard_groups,
    deduped. Used by L2 enrichment when the candidate has no
    `predicted_sector` yet — we scan the HTML against everything the
    tenant cares about."""
    if not wizard_groups:
        return []
    await _warm_cache(supabase)
    seen: set[str] = set()
    out: list[str] = []
    for wg in wizard_groups:
        mapping = _PALETTE_CACHE.get(wg)
        if mapping is None:
            continue
        for kw in mapping.site_signal_keywords:
            if kw not in seen:
                seen.add(kw)
                out.append(kw)
    return out


@lru_cache(maxsize=1)
def known_wizard_groups_seed() -> tuple[str, ...]:
    """Hardcoded enumeration of the wizard_groups present after migration
    0098. Used for Pydantic validation paths that can't reach the DB at
    boot time. Keep this list in sync with the seed migration."""
    return (
        "industry_light",
        "industry_heavy",
        "food_production",
        "logistics",
        "retail_gdo",
        "horeca",
        "hospitality_large",
        "hospitality_food_service",
        "healthcare",
        "healthcare_private",  # alias for forward-compat with addendum
        "agricultural_intensive",
        "automotive",
        "education",
        "personal_services",
        "professional_offices",
    )
