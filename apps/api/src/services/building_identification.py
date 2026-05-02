"""Building Identification Cascade (BIC) — multi-signal + vision.

The legacy `operating_site_resolver` resolves a (lat, lng) pair from a
4-tier cascade (Atoka → website scrape → Google Places → Mapbox HQ).
That works for companies whose registered address is precise to a
civic number, but breaks systematically in industrial zones: the
address resolves to the centroid of the whole "Z.I.", Solar API picks
*any* building in the area, and the AI render paints panels on the
wrong capannone.

This module is the next-generation resolver: instead of returning the
first signal that matches, we **collect candidates from every signal
in parallel**, weight them, and let a final voting + vision step pick
the building with quantified confidence.

Stages
------
0. **Cache lookup** by VAT — short-circuit if we've already resolved
   this company in any tenant before.
1. **Atoka civic precision** (existing, reused).
2. **Google Places multi-query** — fire 4-6 differently-formatted
   variants of the company name through Places, dedupe by `place_id`.
3. **Website scrape + JSON-LD geo + Maps iframe** (existing
   `email_extractor` extended).
4. **OSM Overpass** in a zone bbox with fuzzy `name=*` / `operator=*`
   matching against the company name.
5. **Vision-on-aerial** — when stages 1-4 don't converge with high
   confidence, fetch the aerial of the zone and ask Claude Vision to
   identify which of the OSM building candidates has the company
   name visibly displayed.
6. **Voting + decision** — cluster all candidates by geographic
   proximity, sum weights, pick the winning cluster, classify
   confidence as high / medium / low / none.
7. **Cache** the winning building keyed by VAT for next time.

The output `BuildingMatch` is downward-compatible with the legacy
`OperatingSite` so existing call sites (creative.py, level4_solar_gate)
don't have to change behaviour — `operating_site_resolver` is now a
thin wrapper that calls `identify_building` and projects.

Cost
----
A worst-case run that traverses every stage costs ~€0.20 (Atoka €0.05,
Places ×6 €0.10, Solar ×2 €0.02, Vision ~€0.005, Overpass free).
Cache hits are free. Stages 5 and 6 are gated on stage 1-4 confidence
so well-known companies short-circuit at stage 1 in <2s.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..services.italian_business_service import AtokaProfile

log = get_logger(__name__)


# Confidence buckets — match the legacy `operating_site_resolver`
# vocabulary so callers (subjects.sede_operativa_confidence column,
# CreativeAgent hard gate, dashboard badge) don't need changes.
ConfidenceBucket = Literal["high", "medium", "low", "none", "user_confirmed"]


# Geographic-clustering tolerance: candidates within this many metres
# of each other are pooled into the same cluster for voting. 50 m is
# tight enough to separate adjacent capannoni (typical industrial-zone
# parcel ~30-50 m wide) yet loose enough to absorb the intrinsic
# geocode jitter between Mapbox / Google Places / Atoka coords for
# the same physical building.
CLUSTER_RADIUS_M = 50.0

# Score thresholds. The voter sums per-candidate weights inside a
# cluster; the winning cluster's score determines the confidence
# bucket. Tuned so that:
#   * a single deterministic signal (Atoka civic, Places hit at >0.8,
#     iframe coords) → already gets us to ≥ 1.0 → medium minimum.
#   * two corroborating signals → ≥ 1.5 → high.
#   * vision-only with no text corroboration → 0.6-1.0 → low/medium.
SCORE_THRESHOLD_HIGH = 1.5
SCORE_THRESHOLD_MEDIUM = 0.9
SCORE_THRESHOLD_LOW = 0.4

# Stage 5 (vision) is invoked only when the best automatic candidate
# has weight below this threshold. Above it we already have enough
# evidence (e.g. Atoka civic + Places agreement) and don't need to
# pay the Vision API cost or the latency.
VISION_INVOCATION_THRESHOLD = 0.85

# Soft cap on candidates passed to Vision. We crop and stitch up to
# this many tiles into a single multi-image prompt; more than this
# blows the input token budget without improving the signal (Claude's
# attention degrades past ~6 simultaneous image regions in our tests).
MAX_VISION_CANDIDATES = 5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BuildingCandidate:
    """One signal-emitted hypothesis for "this is the company's building".

    Multiple stages can each emit several candidates; the voter pools
    them by geographic proximity and sums weights. ``source`` is a
    short string identifying which stage produced this candidate so
    we can audit "why did we pick this building?" on the dashboard.
    """

    lat: float
    lng: float
    weight: float                       # 0..1 typical; can exceed 1 for very strong deterministic signals
    source: str                         # "atoka" | "places_q1_first_token" | ... | "vision" | "user_pick"
    polygon_geojson: dict | None = None  # Building footprint when known (OSM, vision)
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class BuildingMatch:
    """Winning building from the cascade.

    Maps to ``subjects.sede_operativa_*`` columns:
      lat/lng → sede_operativa_lat/lng
      address → sede_operativa_address (from the source candidate's metadata)
      confidence → sede_operativa_confidence
      source → sede_operativa_source (kept legacy-compatible for the dashboard
        badge: 'atoka' | 'website_scrape' | 'google_places' | 'mapbox_hq' |
        'osm_snap' | 'vision' | 'user_confirmed' | 'unresolved')

    ``source_chain`` is the JSON-serialisable record persisted in
    ``known_company_buildings.source_chain`` for debugging.
    """

    lat: float | None
    lng: float | None
    address: str | None
    cap: str | None
    city: str | None
    province: str | None
    polygon_geojson: dict | None
    confidence: ConfidenceBucket
    source: str
    source_chain: list[dict]
    needs_user_confirmation: bool

    @classmethod
    def empty(cls) -> "BuildingMatch":
        return cls(
            lat=None,
            lng=None,
            address=None,
            cap=None,
            city=None,
            province=None,
            polygon_geojson=None,
            confidence="none",
            source="unresolved",
            source_chain=[],
            needs_user_confirmation=True,
        )

    @property
    def has_coords(self) -> bool:
        return self.lat is not None and self.lng is not None


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine distance in metres. Sufficiently accurate for < 1 km."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Cache (Stage 0)
# ---------------------------------------------------------------------------


async def lookup_cached_building(vat_number: str) -> BuildingMatch | None:
    """Return the cached BIC entry for this VAT, or None on miss.

    The cache is global by VAT (not tenant-scoped) — a building doesn't
    change owner because two tenants happen to target the same company.
    """
    if not vat_number or not vat_number.strip():
        return None
    sb = get_service_client()
    try:
        res = await asyncio.to_thread(
            lambda: sb.table("known_company_buildings")
            .select(
                "vat_number, lat, lng, polygon_geojson, confidence, "
                "source_chain, confirmed_at, resolved_at"
            )
            .eq("vat_number", vat_number.strip())
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — cache miss must never crash the resolver
        log.warning(
            "bic.cache_lookup_failed",
            vat_number=vat_number,
            err=str(exc)[:120],
        )
        return None

    rows = res.data or []
    if not rows:
        return None
    row = rows[0]
    confidence = row.get("confidence") or "none"
    source = "user_confirmed" if row.get("confirmed_at") else "cache"
    return BuildingMatch(
        lat=float(row["lat"]),
        lng=float(row["lng"]),
        address=None,
        cap=None,
        city=None,
        province=None,
        polygon_geojson=row.get("polygon_geojson"),
        confidence=confidence,
        source=source,
        source_chain=row.get("source_chain") or [],
        needs_user_confirmation=False,
    )


async def cache_building_match(
    *,
    vat_number: str,
    tenant_id: str | None,
    match: BuildingMatch,
    cost_cents: int = 0,
    user_id: str | None = None,
) -> None:
    """Upsert the resolved building into ``known_company_buildings``.

    User confirmations (``user_id`` set) overwrite any prior automated
    entry; automated entries never overwrite a ``user_confirmed`` row.
    """
    if not match.has_coords or not vat_number:
        return
    sb = get_service_client()
    payload: dict[str, Any] = {
        "vat_number": vat_number.strip(),
        "tenant_id": tenant_id,
        "lat": match.lat,
        "lng": match.lng,
        "polygon_geojson": match.polygon_geojson,
        "confidence": (
            "user_confirmed" if user_id else match.confidence
        ),
        "source_chain": match.source_chain,
        "cost_cents": cost_cents,
    }
    if user_id:
        payload["confirmed_by_user_id"] = user_id
        payload["confirmed_at"] = "now()"

    try:
        # Manual conflict handling so user_confirmed entries are never
        # silently downgraded. Read-then-write is racy in theory but
        # demo writes happen at human cadence — collisions are
        # vanishingly rare and the worst case is a redundant write.
        existing = await asyncio.to_thread(
            lambda: sb.table("known_company_buildings")
            .select("confidence")
            .eq("vat_number", vat_number.strip())
            .limit(1)
            .execute()
        )
        existing_conf = (
            (existing.data or [{}])[0].get("confidence") if existing.data else None
        )
        if existing_conf == "user_confirmed" and not user_id:
            log.info(
                "bic.cache_skip_user_confirmed",
                vat_number=vat_number,
                attempted_overwrite_with=match.confidence,
            )
            return

        await asyncio.to_thread(
            lambda: sb.table("known_company_buildings")
            .upsert(payload, on_conflict="vat_number")
            .execute()
        )
        log.info(
            "bic.cache_write",
            vat_number=vat_number,
            confidence=payload["confidence"],
            user_confirmed=bool(user_id),
        )
    except Exception as exc:  # noqa: BLE001 — never compound a primary failure
        log.warning(
            "bic.cache_write_failed",
            vat_number=vat_number,
            err=str(exc)[:200],
        )


# ---------------------------------------------------------------------------
# Voting (Stage 6)
# ---------------------------------------------------------------------------


def _cluster_candidates(
    candidates: list[BuildingCandidate],
) -> list[list[BuildingCandidate]]:
    """Greedy single-link clustering on lat/lng with CLUSTER_RADIUS_M.

    For each unassigned candidate, build a cluster by absorbing every
    other candidate within the radius. Sufficient for our typical
    sample size (≤ 20 candidates per resolver invocation) and avoids
    pulling in scikit-learn for one DBSCAN call.
    """
    clusters: list[list[BuildingCandidate]] = []
    assigned: set[int] = set()
    for i, c in enumerate(candidates):
        if i in assigned:
            continue
        cluster = [c]
        assigned.add(i)
        for j, other in enumerate(candidates):
            if j in assigned:
                continue
            if _haversine_m(c.lat, c.lng, other.lat, other.lng) <= CLUSTER_RADIUS_M:
                cluster.append(other)
                assigned.add(j)
        clusters.append(cluster)
    return clusters


def _classify_confidence(
    cluster: list[BuildingCandidate], score: float
) -> ConfidenceBucket:
    """Map (cluster, score) → confidence bucket.

    The score thresholds are augmented with a "deterministic signal
    present?" check: a cluster scoring ≥ 1.5 but composed entirely of
    weak/inferential signals (e.g. multiple OSM nearby buildings + a
    vision guess with low confidence) only gets ``medium`` because
    we don't actually have a hard pin on the building.
    """
    deterministic_sources = {
        "atoka_civic",
        "places_iframe",
        "website_jsonld_geo",
        "user_pick",
        "user_confirmed",
    }
    has_deterministic = any(c.source in deterministic_sources for c in cluster)

    if score >= SCORE_THRESHOLD_HIGH and has_deterministic:
        return "high"
    if score >= SCORE_THRESHOLD_MEDIUM:
        return "medium"
    if score >= SCORE_THRESHOLD_LOW:
        return "low"
    return "none"


def vote_on_candidates(candidates: list[BuildingCandidate]) -> BuildingMatch:
    """Cluster candidates by proximity and pick the highest-scoring cluster.

    This is the "decision" stage of the cascade. Pure function — no
    network calls — so it's trivially testable in isolation.
    """
    if not candidates:
        return BuildingMatch.empty()

    clusters = _cluster_candidates(candidates)
    # Sort by descending total weight; tie-breaker is the cluster's
    # max single-candidate weight (a strong-signal cluster outranks a
    # crowd of weak signals at the same total).
    clusters.sort(
        key=lambda cl: (sum(c.weight for c in cl), max(c.weight for c in cl)),
        reverse=True,
    )
    winner = clusters[0]
    score = sum(c.weight for c in winner)

    # Weighted centroid of the winning cluster.
    total_w = sum(c.weight for c in winner)
    if total_w <= 0:
        # All candidates have zero weight (shouldn't happen — the voter
        # only receives positive-weight candidates) — degrade gracefully.
        win_lat = winner[0].lat
        win_lng = winner[0].lng
    else:
        win_lat = sum(c.lat * c.weight for c in winner) / total_w
        win_lng = sum(c.lng * c.weight for c in winner) / total_w

    # Pull metadata from the heaviest candidate so we keep its address /
    # polygon / source label rather than averaging strings.
    leader = max(winner, key=lambda c: c.weight)
    confidence = _classify_confidence(winner, score)

    source_chain = [
        {
            "stage": c.source,
            "weight": round(c.weight, 3),
            "lat": c.lat,
            "lng": c.lng,
            **c.metadata,
        }
        for c in winner
    ]

    return BuildingMatch(
        lat=win_lat,
        lng=win_lng,
        address=leader.metadata.get("address"),
        cap=leader.metadata.get("cap"),
        city=leader.metadata.get("city"),
        province=leader.metadata.get("province"),
        polygon_geojson=leader.polygon_geojson,
        confidence=confidence,
        source=leader.source,
        source_chain=source_chain,
        needs_user_confirmation=confidence in ("low", "none"),
    )


# ---------------------------------------------------------------------------
# Cascade orchestrator
# ---------------------------------------------------------------------------


async def identify_building(
    *,
    vat_number: str,
    legal_name: str,
    profile: AtokaProfile | None = None,
    website_domain: str | None = None,
    hq_address: str | None = None,
    hq_city: str | None = None,
    hq_province: str | None = None,
    ateco_code: str | None = None,
    ateco_description: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    enable_vision: bool = True,
    skip_cache: bool = False,
) -> BuildingMatch:
    """Run the full Building Identification Cascade.

    Always returns a ``BuildingMatch``; on total failure the match has
    ``has_coords=False`` and ``confidence='none'`` so callers (e.g.
    creative agent) can decide to skip the render and force user
    confirmation. Never raises — every stage is wrapped to fail open.
    """

    # Stage 0 — cache lookup
    if not skip_cache:
        cached = await lookup_cached_building(vat_number)
        if cached is not None and cached.has_coords:
            log.info(
                "bic.cache_hit",
                vat_number=vat_number,
                confidence=cached.confidence,
            )
            return cached

    # Lazy import — these modules pull in heavy deps (Anthropic SDK,
    # PIL) and we don't want to slow down `routes/demo.py` import time
    # for callers that don't reach the cascade.
    from . import google_places_service
    from .operating_site_resolver import resolve_operating_site

    candidates: list[BuildingCandidate] = []

    # ── Stage 1+3 — reuse the legacy resolver ────────────────────────
    # The 4-tier resolver still does Atoka + website + Places + Mapbox
    # for us. We project its single-result output into one candidate;
    # subsequent stages add corroborating signals to the same cluster.
    try:
        legacy = await resolve_operating_site(
            profile=profile,
            legal_name=legal_name,
            website_domain=website_domain,
            hq_address=hq_address,
            hq_city=hq_city,
            hq_province=hq_province,
            http_client=http_client,
            validate_with_solar=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "bic.legacy_resolver_failed",
            vat_number=vat_number,
            err=str(exc)[:200],
        )
        legacy = None

    if legacy is not None and legacy.has_coords:
        # Confidence → weight mapping. The legacy resolver's "high" is
        # Atoka civic match (+ Solar validation), which is a deterministic
        # pin. Medium is website / Places — strong but not pin-precise.
        weight_map = {"high": 1.0, "medium": 0.7, "low": 0.4}
        weight = weight_map.get(legacy.confidence, 0.3)
        # Tag the source so the voter's deterministic-signal check
        # works (atoka_civic) — only Atoka with high confidence is
        # truly deterministic.
        canonical_source = legacy.source
        if legacy.source == "atoka" and legacy.confidence == "high":
            canonical_source = "atoka_civic"
        candidates.append(
            BuildingCandidate(
                lat=legacy.lat,  # type: ignore[arg-type]
                lng=legacy.lng,  # type: ignore[arg-type]
                weight=weight,
                source=canonical_source,
                metadata={
                    "address": legacy.address,
                    "cap": legacy.cap,
                    "city": legacy.city,
                    "province": legacy.province,
                    "legacy_confidence": legacy.confidence,
                },
            )
        )

    # ── Stage 2 — Google Places multi-query ──────────────────────────
    # Fan out 4-6 differently-formatted queries against Places. Each
    # unique place_id contributes a low-weight candidate so a building
    # that surfaces under multiple name formulations naturally ends
    # up in a heavier vote cluster.
    try:
        owns_client = http_client is None
        if http_client is None:
            http_client = httpx.AsyncClient(timeout=8.0)
        try:
            place_candidates = await google_places_service.search_text_multi_query(
                legal_name=legal_name,
                city=hq_city,
                province=hq_province,
                ateco_code=ateco_code,
                ateco_description=ateco_description,
                http_client=http_client,
                location_bias_centre=(legacy.lat, legacy.lng)
                if legacy and legacy.has_coords
                else None,
            )
        finally:
            if owns_client:
                await http_client.aclose()
                http_client = None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "bic.places_multi_query_failed",
            vat_number=vat_number,
            err=str(exc)[:200],
        )
        place_candidates = []

    candidates.extend(place_candidates)

    # ── Stage 4 — OSM Overpass with name fuzzy match ─────────────────
    # Need a zone centre to query around. Prefer the legacy match's
    # coordinates (already on the right industrial zone) and fall
    # back to silently skipping this stage when we have no anchor.
    zone_anchor: tuple[float, float] | None = None
    if legacy is not None and legacy.has_coords:
        zone_anchor = (legacy.lat, legacy.lng)  # type: ignore[assignment]

    osm_buildings: list[BuildingCandidate] = []
    if zone_anchor is not None:
        try:
            from . import osm_building_service

            owns_client = http_client is None
            if http_client is None:
                http_client = httpx.AsyncClient(timeout=15.0)
            try:
                osm_buildings = await osm_building_service.find_buildings_in_zone(
                    lat=zone_anchor[0],
                    lng=zone_anchor[1],
                    target_name=legal_name,
                    client=http_client,
                )
            finally:
                if owns_client:
                    await http_client.aclose()
                    http_client = None
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "bic.osm_zone_failed",
                vat_number=vat_number,
                err=str(exc)[:200],
            )
            osm_buildings = []

    # OSM buildings whose name matches the company become weighted
    # candidates; the rest are kept around as zero-weight "vision
    # candidates" — they don't influence the vote on their own but
    # Stage 5 picks one of them as Vision's pick if it fires.
    candidates.extend(c for c in osm_buildings if c.weight > 0)

    # ── Stage 5 — Vision on aerial ───────────────────────────────────
    best_so_far = max((c.weight for c in candidates), default=0.0)
    vision_eligible_buildings = [c for c in osm_buildings if c.polygon_geojson is not None]

    # Limit the number of buildings we screenshot to keep the Vision
    # input manageable. Pick the closest N to the zone anchor.
    if zone_anchor is not None:
        vision_eligible_buildings.sort(
            key=lambda c: _haversine_m(
                zone_anchor[0], zone_anchor[1], c.lat, c.lng
            )
        )
    vision_eligible_buildings = vision_eligible_buildings[:MAX_VISION_CANDIDATES]

    if (
        enable_vision
        and best_so_far < VISION_INVOCATION_THRESHOLD
        and len(vision_eligible_buildings) >= 2
        and zone_anchor is not None
    ):
        try:
            from . import aerial_vision_service

            vision_pick = await aerial_vision_service.identify_company_building_in_zone(
                legal_name=legal_name,
                vat_number=vat_number,
                ateco_description=ateco_description,
                city=hq_city,
                candidate_buildings=vision_eligible_buildings,
                zone_anchor=zone_anchor,
            )
            if vision_pick is not None:
                candidates.append(vision_pick)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "bic.vision_failed",
                vat_number=vat_number,
                err=str(exc)[:200],
            )
    else:
        log.debug(
            "bic.vision_skipped",
            vat_number=vat_number,
            best_so_far=round(best_so_far, 2),
            n_vision_buildings=len(vision_eligible_buildings),
            reason=(
                "high_confidence_already" if best_so_far >= VISION_INVOCATION_THRESHOLD
                else "insufficient_candidates" if len(vision_eligible_buildings) < 2
                else "no_zone_anchor"
            ),
        )

    # ── Stage 6 — Voting ─────────────────────────────────────────────
    match = vote_on_candidates(candidates)

    log.info(
        "bic.cascade_decision",
        vat_number=vat_number,
        confidence=match.confidence,
        winning_source=match.source,
        n_candidates=len(candidates),
        n_clusters_evaluated=(
            len(_cluster_candidates(candidates)) if candidates else 0
        ),
    )

    # Stage 7 — Cache (only when we actually have coords)
    if match.has_coords and match.confidence != "none":
        await cache_building_match(
            vat_number=vat_number,
            tenant_id=None,  # caller knows the tenant; cache is global
            match=match,
        )

    return match


# ---------------------------------------------------------------------------
# Backwards compatibility — project BuildingMatch → legacy OperatingSite
# ---------------------------------------------------------------------------


def match_to_operating_site(match: "BuildingMatch") -> Any:
    """Project a BuildingMatch onto the legacy ``OperatingSite`` dataclass.

    Production call sites (level4_solar_gate, hunter cron, etc.) still
    expect the old ``OperatingSite`` shape because that's what the
    Postgres ``subjects.sede_operativa_*`` writers and the dashboard
    badge consume. Once the BIC is universally adopted we can flatten
    these into a single record; until then this helper lets us flip
    callers over one at a time without a big-bang migration.

    The mapping preserves source labels where possible:
      * BIC ``user_confirmed`` / ``user_pick`` → ``OperatingSite.source='user_confirmed'``
      * BIC ``vision`` → keep ``vision`` (new value, dashboard badge will need a row)
      * BIC ``cache`` → preserve underlying source from the cache row
        (we don't have it here cleanly — fall back to ``cache`` literal)
      * Other legacy sources (atoka, atoka_civic, places_*, website_*,
        osm_*, mapbox_hq) pass through; the dashboard already knows
        the legacy ones and treats unknown values as "unspecified".

    Confidence buckets are 1:1 (high/medium/low/none/user_confirmed)
    so we don't have to remap them.
    """
    # Local import to avoid a top-level circular: operating_site_resolver
    # imports building_identification (in callers that wrap the BIC),
    # which would re-import operating_site_resolver here.
    from .operating_site_resolver import OperatingSite

    if not match.has_coords:
        return OperatingSite.empty()

    # Normalise the source label. The BIC's voter copies ``leader.source``
    # which can be e.g. ``places_x3``, ``osm_name``, ``vision``,
    # ``atoka_civic``. The legacy enum ('atoka' | 'website_scrape' |
    # 'google_places' | 'mapbox_hq' | 'unresolved') is what the
    # dashboard badge currently switches on. We project onto the
    # closest match so the existing rendering keeps working; the new
    # values (``vision``, ``user_confirmed``, ``places_xN``) leak
    # through as-is so the dashboard can be extended later.
    src = match.source
    if src.startswith("places"):
        src = "google_places"
    elif src in ("osm_name", "osm_zone"):
        src = "osm_snap"
    elif src == "atoka_civic":
        src = "atoka"
    elif src == "user_pick":
        src = "user_confirmed"

    # Confidence: 'user_confirmed' from BIC stays as-is so the dashboard
    # can later add a green "operatore" badge; for now the legacy
    # OperatingSite confidence field accepts any string so we don't
    # have to coerce it.
    return OperatingSite(
        lat=match.lat,
        lng=match.lng,
        address=match.address,
        cap=match.cap,
        city=match.city,
        province=match.province,
        source=src,
        confidence=str(match.confidence),
    )
