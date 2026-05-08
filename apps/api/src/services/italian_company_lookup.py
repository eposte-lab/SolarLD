"""Italian company registry lookup via OpenAPI.it.

OpenAPI.it (https://console.openapi.com) is a pay-as-you-go gateway to
the Camera di Commercio (Registro Imprese) — Italian official corporate
registry. Unlike Atoka it requires no contract: you create an OAuth
token in the self-service console, top up credit, and call REST
endpoints. The /advance endpoint accepts ATECO + provincia + revenue/
employee filters and returns active companies that match.

We use this for sectors where Google Places returns the wrong category
because Google has no dedicated type for them in Italy:

  - amministratori condominio (ATECO 68.32 + 81.10) — Google's
    `real_estate_agency` matches Tecnocasa, Gabetti, Regus, etc.
    The registry instead exposes the actual administrators by
    economic activity code, so we get the real cohort.

The service maps OpenAPI.it results into the same `ProspectorPlace`
shape the dashboard table already renders, so the UI stays unchanged.

Auth model
----------
A single Bearer token (set in `OPENAPI_IT_TOKEN` env var) is shared
across all tenants — OpenAPI.it bills the account, not per-end-user.
Empty token disables the integration: callers fall back to Google
Places for every sector. The token has read-only scope on the
`imprese` API and an explicit expiry the operator sets in the console.

Cost model
----------
Each /advance call costs €0.10 above 100/day (free tier). The caller
should cap `limit` to the number actually needed and reuse results
when possible (the dashboard /scoperta page already saves results as
a `prospect_lists` row before re-querying).

API spec source: https://console.openapi.com/apis/imprese/documentation
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..core.config import settings
from ..core.logging import get_logger
from .places_prospector_service import ProspectorPlace

log = get_logger(__name__)

PRODUCTION_BASE_URL = "https://imprese.openapi.it"
SANDBOX_BASE_URL = "https://test.imprese.openapi.it"

# OpenAPI.it /advance returns up to 100 records per call. Cap our own
# `limit` argument here so the caller can't accidentally request more
# and silently get truncated.
MAX_RECORDS_PER_CALL = 100

# Connect timeout 5s + total 15s — registry lookups are slower than
# Google Places (the upstream is the Camera di Commercio backend).
_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)


@dataclass(slots=True)
class CompanyLookupResult:
    """`total` is the raw count returned by /advance before our own
    post-filtering / mapping. `items` may be shorter when some rows
    fail to map (missing P.IVA, missing coordinates, etc.)."""

    items: list[ProspectorPlace]
    total: int


def _base_url() -> str:
    return SANDBOX_BASE_URL if settings.openapi_it_use_sandbox else PRODUCTION_BASE_URL


def _normalise_ateco(code: str) -> str:
    """OpenAPI.it accepts ATECO codes without dots (e.g. 68.32.00 → 683200).

    The registry tolerates partial codes too — `6832` matches every
    sub-class under 68.32. We strip the dots and let the upstream do the
    matching.
    """
    return code.replace(".", "").strip()


def _build_formatted_address(payload: dict[str, Any]) -> str | None:
    """Compose an Italian-style address line from the registry fields.

    OpenAPI.it splits the address across `indirizzo`, `cap`, `comune`,
    `provincia`. Glue them back into the one-line shape our UI expects.
    """
    parts: list[str] = []
    indirizzo = payload.get("indirizzo")
    if isinstance(indirizzo, str) and indirizzo.strip():
        parts.append(indirizzo.strip())
    city_block: list[str] = []
    cap = payload.get("cap")
    if isinstance(cap, str) and cap.strip():
        city_block.append(cap.strip())
    comune = payload.get("comune")
    if isinstance(comune, str) and comune.strip():
        city_block.append(comune.strip())
    provincia = payload.get("provincia")
    if isinstance(provincia, str) and provincia.strip():
        city_block.append(provincia.strip().upper())
    if city_block:
        parts.append(" ".join(city_block))
    if not parts:
        return None
    return ", ".join(parts) + ", IT"


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _parse_company(payload: dict[str, Any]) -> ProspectorPlace | None:
    """Map one /advance hit into the table-friendly `ProspectorPlace`.

    Required: a unique identifier (P.IVA preferred, codice_fiscale
    fallback) and a denominazione. Lat/lng are required for downstream
    Solar verification — when missing we fill 0/0 and let the caller
    skip the row if needed.
    """
    piva = payload.get("piva") or payload.get("partita_iva")
    cf = payload.get("cf") or payload.get("codice_fiscale")
    identifier = (
        piva
        if isinstance(piva, str) and piva.strip()
        else cf
        if isinstance(cf, str) and cf.strip()
        else None
    )
    if not identifier:
        return None

    denominazione = (
        payload.get("denominazione") or payload.get("ragione_sociale") or payload.get("nome")
    )
    if not isinstance(denominazione, str) or not denominazione.strip():
        return None

    # Coordinates may live at top level or under a nested "coordinate"
    # / "geo" object — we try both shapes.
    lat = _coerce_float(payload.get("lat"))
    lng = _coerce_float(payload.get("lng") or payload.get("lon"))
    if lat is None or lng is None:
        nested = payload.get("coordinate") or payload.get("geo") or {}
        if isinstance(nested, dict):
            lat = lat or _coerce_float(nested.get("lat"))
            lng = lng or _coerce_float(nested.get("lng") or nested.get("lon"))

    return ProspectorPlace(
        # Prefix the P.IVA so the dashboard can tell registry rows
        # apart from Google Places rows in the audit log.
        google_place_id=f"openapi-it:{identifier}",
        display_name=denominazione.strip(),
        formatted_address=_build_formatted_address(payload),
        lat=lat or 0.0,
        lng=lng or 0.0,
        types=["italian_business_registry"],
        business_status=payload.get("stato_attivita") or payload.get("stato"),
        user_ratings_total=None,
        rating=None,
        website=payload.get("sito") or payload.get("website"),
        phone=payload.get("telefono") or payload.get("phone"),
        google_maps_uri=None,
    )


async def search_companies_by_ateco(
    *,
    ateco_codes: list[str],
    province_code: str | None = None,
    revenue_min: int | None = None,
    revenue_max: int | None = None,
    employees_min: int | None = None,
    employees_max: int | None = None,
    keyword: str | None = None,
    limit: int = 60,
    client: httpx.AsyncClient | None = None,
) -> CompanyLookupResult:
    """Query OpenAPI.it /advance for companies under the given ATECO codes.

    The function sweeps each ATECO in order, deduping by identifier so
    the same company doesn't appear twice when it carries two activity
    codes (e.g. an administrator with both 68.32 and 81.10).

    Returns an empty result (no error) when the token is missing — the
    caller decides whether to fall back to Google Places.
    """
    token = settings.openapi_it_token.strip() if settings.openapi_it_token else ""
    if not token:
        log.info("italian_company_lookup.skip_no_token")
        return CompanyLookupResult(items=[], total=0)

    if not ateco_codes:
        return CompanyLookupResult(items=[], total=0)

    capped_limit = min(limit, MAX_RECORDS_PER_CALL)
    headers = {"Authorization": f"Bearer {token}"}

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=_TIMEOUT)

    deduped: dict[str, ProspectorPlace] = {}
    total_returned = 0

    try:
        for ateco in ateco_codes:
            if len(deduped) >= capped_limit:
                break
            params: dict[str, Any] = {
                "codice_ateco": _normalise_ateco(ateco),
                "limit": capped_limit - len(deduped),
                "skip": 0,
            }
            if province_code:
                params["provincia"] = province_code.upper().strip()
            if revenue_min is not None:
                params["fatturato_min"] = revenue_min
            if revenue_max is not None:
                params["fatturato_max"] = revenue_max
            if employees_min is not None:
                params["dipendenti_min"] = employees_min
            if employees_max is not None:
                params["dipendenti_max"] = employees_max
            if keyword and keyword.strip():
                params["denominazione"] = keyword.strip()

            try:
                resp = await client.get(
                    f"{_base_url()}/advance",
                    headers=headers,
                    params=params,
                )
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                log.warning(
                    "italian_company_lookup.http_error",
                    ateco=ateco,
                    err=type(exc).__name__,
                )
                continue

            if resp.status_code == 401 or resp.status_code == 403:
                log.error(
                    "italian_company_lookup.auth_failed",
                    status=resp.status_code,
                    note="OPENAPI_IT_TOKEN missing/expired/scoped wrong",
                )
                break  # token broken — no point trying more ATECO codes
            if resp.status_code >= 400:
                log.warning(
                    "italian_company_lookup.bad_response",
                    ateco=ateco,
                    status=resp.status_code,
                )
                continue

            try:
                payload = resp.json()
            except ValueError:
                log.warning("italian_company_lookup.json_decode_error", ateco=ateco)
                continue

            # OpenAPI.it wraps the records under different keys depending
            # on the endpoint version. We try the documented shapes in
            # order: `data` (most common), `result`, `imprese`.
            raw_items = payload.get("data") or payload.get("result") or payload.get("imprese") or []
            if not isinstance(raw_items, list):
                continue
            total_returned += len(raw_items)
            for raw in raw_items:
                if not isinstance(raw, dict):
                    continue
                cand = _parse_company(raw)
                if cand is None:
                    continue
                if cand.google_place_id in deduped:
                    continue
                deduped[cand.google_place_id] = cand
                if len(deduped) >= capped_limit:
                    break
        return CompanyLookupResult(
            items=list(deduped.values()),
            total=total_returned,
        )
    finally:
        if owns_client:
            await client.aclose()
