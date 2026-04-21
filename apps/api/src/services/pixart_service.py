"""Pixart "lettera fisica" outbound service.

Pixart is the Italian direct-mail partner we use for personalised
residential letters. The integration has two halves:

  1. Outbound (this module) — trigger print+ship for a list of
     addresses with a per-tenant template. Submit the job, store the
     returned tracking id, surface it in the dashboard.
  2. Inbound webhook (`routes/webhooks.py::pixart_webhook`) — already
     wired. Receives delivery events (printed → shipped → delivered
     → returned) and drives the Tracking agent.

Behaviour by config:

* ``PIXART_API_KEY`` not set → **stub mode**: generates a local job id
  (``stub_<uuid>``) and logs the payload. Downstream code (campaign row,
  Tracking Agent) works normally so the full pipeline can be exercised
  in dev and staging without a live Pixart account.
* ``PIXART_API_KEY`` set → calls Pixart's ``/v1/campaigns`` REST
  endpoint. The response shape follows Pixart's documented API (v1,
  Oct 2024): ``{"job_id": "...", "accepted": N}``. If the endpoint
  changes the only required update is the response field extraction
  at the bottom of ``submit_letter_campaign``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class LetterCampaignRequest:
    """Shape we hand Pixart for a batch campaign.

    `addresses` here are CAP-level — we don't have civic addresses
    for residential B2C. Pixart's `bulk CAP` product handles
    distribution via Poste Italiane per-CAP shards, so the tenant
    prints one template and Poste scatters it across the zone.
    """

    tenant_id: UUID | str
    audience_id: UUID | str
    template_id: str
    caps: list[str]
    tenant_brand_name: str | None = None
    copy_overrides: dict[str, str] | None = None


@dataclass(slots=True, frozen=True)
class LetterCampaignResult:
    """Returned by `submit_letter_campaign`. The dashboard stores
    `pixart_job_id` so webhook events (keyed on that id) can later
    update the campaign's status."""

    pixart_job_id: str
    caps_submitted: int
    stub: bool  # True when running without Pixart creds (dev/ci)


async def submit_letter_campaign(
    request: LetterCampaignRequest,
) -> LetterCampaignResult:
    """Submit a per-CAP letter campaign to Pixart.

    Returns the Pixart job id and the CAP count actually accepted.
    If `PIXART_API_KEY` isn't configured we run in stub mode: we
    generate a local job id (`stub_<uuid>`) and log the payload so
    developer workflows don't block on a Pixart account.

    Phase 4 will replace the stub branch with a real HTTPS call to
    Pixart's `/v1/campaigns` endpoint.
    """
    if not settings.pixart_api_key:
        job_id = f"stub_{uuid4().hex[:12]}"
        log.info(
            "pixart.submit.stub",
            extra={
                "tenant_id": str(request.tenant_id),
                "audience_id": str(request.audience_id),
                "template_id": request.template_id,
                "caps": len(request.caps),
                "reason": "PIXART_API_KEY unset — running stub",
            },
        )
        return LetterCampaignResult(
            pixart_job_id=job_id,
            caps_submitted=len(request.caps),
            stub=True,
        )

    # ---- Live Pixart submission ----
    import httpx

    body = {
        "template_id": request.template_id,
        "caps": request.caps,
        "audience_id": str(request.audience_id),
        "copy_overrides": request.copy_overrides or {},
    }
    if request.tenant_brand_name:
        body["brand_name"] = request.tenant_brand_name

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            "https://api.pixart.it/v1/campaigns",
            headers={
                "Authorization": f"Bearer {settings.pixart_api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    res.raise_for_status()
    resp_data = res.json()
    # Pixart API (v1, Oct 2024): {"job_id": "...", "accepted": N}
    # If the schema changes on their side only these two lines need updating.
    job_id: str = resp_data.get("job_id") or resp_data.get("id") or ""
    accepted: int = int(resp_data.get("accepted") or len(request.caps))
    log.info(
        "pixart.submit.ok",
        extra={
            "tenant_id": str(request.tenant_id),
            "job_id": job_id,
            "caps": accepted,
        },
    )
    return LetterCampaignResult(
        pixart_job_id=job_id,
        caps_submitted=accepted,
        stub=False,
    )


def resolve_template_id(tenant_id: UUID | str, bucket: str) -> str:
    """Map a tenant + income bucket to a Pixart template id.

    Template ids are provisioned out-of-band in Pixart's UI — we keep
    a tenant-scoped registry under `tenants.settings.pixart_templates`
    (future enhancement). Until that ships, we return a deterministic
    string so the stub path doesn't blow up and the submitted payload
    is grep-able in logs.
    """
    _ = tenant_id  # reserved for the per-tenant registry lookup
    return f"solarld_b2c_{bucket}_v1"


def build_copy_overrides(
    *,
    tenant_brand_name: str | None,
    cta_primary: str | None,
) -> dict[str, Any]:
    """Render the set of `{{variable}}` placeholders Pixart will merge
    into the template. Keep the set small — every new placeholder
    doubles the template-config surface area."""
    return {
        "brand_name": tenant_brand_name or "Il vostro installatore",
        "cta_primary": cta_primary or "Prenota un sopralluogo gratuito",
    }
