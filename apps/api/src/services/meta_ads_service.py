"""Meta Marketing API integration — B2C lead ads.

Two moving parts:

  1. Outbound campaign creation (this module) — given an audience
     (CAP list + income bucket), build a Meta Custom Audience via
     Pixel + upload a `lookalike_by_cap`, then submit a lead-form
     campaign that asks for name/email/phone/consent.
  2. Inbound webhook (`routes/meta_webhooks.py`) — receives lead
     submissions and upserts into `leads` with `source='b2c_meta_ads'`.

Meta's Marketing API requires an app review before an installer's ad
account can be reached programmatically. Until that lands for the
SolarLead Meta app, this module runs in stub mode: it accepts a
campaign submission, logs the payload, and returns a synthetic id.
The API surface is stable so Phase 4 (review + real calls) is a drop-
in swap inside `create_lead_campaign`.

We deliberately keep persistence out of this service — the route
handler writes the `meta_connections` row + audience metadata. This
way the service is a pure Meta-API adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from ..core.logging import get_logger

log = get_logger(__name__)


# Interest ids from Meta's detailed targeting taxonomy. Stable strings;
# reproducible via Meta's Ads Manager "Audience insights" export.
DEFAULT_B2C_INTERESTS: tuple[str, ...] = (
    "6003246554566",  # Risparmio energetico
    "6003252301838",  # Energia solare
    "6003183139293",  # Case unifamiliari
    "6003020834693",  # Giardinaggio
)


@dataclass(slots=True)
class MetaCampaignRequest:
    tenant_id: UUID | str
    audience_id: UUID | str
    caps: list[str]
    reddito_bucket: str
    daily_budget_eur: float
    cta_primary: str
    # Optional — Meta ad review prefers a 25-word or fewer body.
    body_copy: str | None = None


@dataclass(slots=True, frozen=True)
class MetaCampaignResult:
    campaign_id: str
    ad_set_id: str
    status: str
    stub: bool


async def create_lead_campaign(
    request: MetaCampaignRequest,
    *,
    connection: dict[str, Any] | None,
) -> MetaCampaignResult:
    """Create a Meta Lead Ads campaign targeting the audience's CAPs.

    Requires a live `meta_connections` row for the tenant. If
    `connection` is None we return a stub response so dashboard flows
    remain exercisable before OAuth is wired.
    """
    if connection is None:
        return _stub_result(request, reason="no_connection")

    access_token = connection.get("access_token")
    ad_account_id = connection.get("meta_ad_account_id")
    if not access_token or not ad_account_id:
        return _stub_result(request, reason="incomplete_connection")

    # ---- Real Graph API call lands in Phase 4 ----
    # Outline:
    #   async with httpx.AsyncClient(timeout=30.0) as c:
    #       # 1. Create saved audience (CAP geo targeting + interests)
    #       # 2. Create campaign (objective=LEAD_GENERATION)
    #       # 3. Create ad set (daily budget, optimization_goal=LEADS)
    #       # 4. Create ad creative + ad (linking lead form)
    raise NotImplementedError(
        "Live Meta Marketing API submission lands in Phase 4 — stub "
        "mode returns a synthetic id when no connection is registered."
    )


def _stub_result(
    request: MetaCampaignRequest, *, reason: str
) -> MetaCampaignResult:
    cid = f"stub_campaign_{uuid4().hex[:10]}"
    asid = f"stub_adset_{uuid4().hex[:10]}"
    log.info(
        "meta.campaign.stub",
        extra={
            "tenant_id": str(request.tenant_id),
            "audience_id": str(request.audience_id),
            "caps": len(request.caps),
            "bucket": request.reddito_bucket,
            "reason": reason,
        },
    )
    return MetaCampaignResult(
        campaign_id=cid,
        ad_set_id=asid,
        status="stub_submitted",
        stub=True,
    )


def build_targeting_spec(
    *, caps: list[str], interests: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Shape the Meta targeting payload. Exposed so tests can assert
    the structure without hitting the network."""
    return {
        "geo_locations": {
            # Meta accepts Italian CAPs as zips. Country code is
            # redundant when zips are provided but Meta requires it
            # in the schema; not carried in the audience row, so we
            # hardcode.
            "zips": [{"key": f"IT:{c}"} for c in caps],
        },
        "interests": [
            {"id": iid, "name": ""}
            for iid in (interests or DEFAULT_B2C_INTERESTS)
        ],
        "age_min": 35,
        "age_max": 70,
    }


def recommended_daily_budget(n_caps: int) -> float:
    """Scale the daily budget with audience breadth. €10/day per CAP
    as a sane default; installers can override in the campaign form.
    Capped at €200/day so a misconfigured 50-CAP audience doesn't
    start a €500/day burn."""
    return min(200.0, max(10.0, n_caps * 10.0))
