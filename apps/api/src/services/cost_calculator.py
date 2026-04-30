"""Centralised cost-calculator for the SolarLead pipeline (ADR-005).

Single source of truth for every per-unit cost the pipeline incurs and
the projection function operations uses to size campaigns / set
budgets.  Existing scattered constants in service modules
(`italian_business_service.py`, `google_solar_service.py`,
`dialog360_service.py`, `neverbounce_service.py`,
`resend_service.py`) are intentionally left untouched — this module
duplicates them deliberately so a refactor of one service does not
silently move the projection numbers.  Callers that want the
authoritative cost should import from here going forward.

Provenance of the Atoka cost
----------------------------
ADR-005 fixes Atoka pricing post-tutto-in-uno based on the actual
purchase invoice:

  - **Initial purchase**: 8 000 credits for €3 000 → €0.375 / credit
  - **Expected runway**: 8 000 credits ≈ 2 calendar months ≈ 44
    working days at the negotiated cap

  ⇒ ~182 credits/working-day amortised. This is the divisor used in
  ``estimate_atoka_runway_days()``.

The historic ``ATOKA_COST_PER_CALL_CENTS = 15`` (€0.15) in
``italian_business_service.py`` reflected the old per-call rate before
the all-in-one bundle. Do not use it for new projections — read
``ATOKA_COST_PER_CALL_EUR`` from this module instead.

Pipeline survival rate
----------------------
The previous 9-phase pipeline produced ~43 % survival from raw Atoka
record to delivered email.  Atoka tutto-in-uno + the offline filter
cascade (post ADR-002) lifts the bar to ~60 %; this is the value used
to size Atoka spend per delivered email.

Subscriptions
-------------
Fixed monthly infra costs (deliverability domains warm-up, mailbox
seats, Dialog360 BSP fee) are kept here for the projection function
even though they are not yet tracked in code.  Update when invoices
land.

Numbers in this module should be read as **planning estimates**, not
authoritative per-call charges. The cost we *actually* booked against
a tenant's budget is recorded on each `campaigns.cost_eur` row by the
service that paid for it; this module only feeds the operations
dashboard ("how many emails can we afford this month?").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Per-unit costs (euro)
# ---------------------------------------------------------------------------
#
# All costs are stored in euro (float).  Subscription costs are
# monthly. Variable costs are per-unit (per call / per email / per
# WhatsApp / per render).  A single source-of-truth dict is exposed
# at the bottom for serialisation to dashboards.

# Variable — per call / per record
ATOKA_COST_PER_CALL_EUR: float = 0.375
"""ADR-005: 8 000 credits / €3 000 purchase. One credit ≈ one call."""

ATOKA_DISCOVERY_COST_PER_RECORD_EUR: float = 0.01
"""Discovery search returns an Atoka record list; thinner endpoint."""

GOOGLE_SOLAR_BUILDING_INSIGHTS_EUR: float = 0.02
GOOGLE_SOLAR_DATA_LAYERS_EUR: float = 0.03

NEVERBOUNCE_PER_VERIFY_EUR: float = 0.01
RESEND_PER_EMAIL_EUR: float = 0.0004
WHATSAPP_PER_MESSAGE_EUR: float = 0.08
"""Source: 360dialog Italy tier-1 BSP estimate (utility template)."""

# Per delivered email — Kling/Runway is the dominant unit cost; we
# keep one line item here for projections even though the true cost
# depends on the renderer chosen at runtime.
RENDERING_PER_EMAIL_EUR: float = 0.49

# Fixed — monthly subscriptions
DELIVERABILITY_INFRA_MONTHLY_EUR: float = 187.0
"""Multi-domain warm-up, DNS / DKIM / DMARC, monitoring."""

WORKSPACE_MAILBOX_MONTHLY_EUR: float = 36.0
"""Per-tenant Google Workspace seat for outreach From-address."""

DIALOG360_SUBSCRIPTION_MONTHLY_EUR: float = 50.0
"""360dialog monthly BSP subscription, charged regardless of volume."""


# ---------------------------------------------------------------------------
# Pipeline shape parameters
# ---------------------------------------------------------------------------

PIPELINE_SURVIVAL_RATE_V2: float = 0.60
"""Fraction of Atoka raw records that survive the v2 funnel and
become a delivered email. ADR-002 lifted this from 0.43 to 0.60 by
removing the dead Phase-3 immobiliare check."""

OFFLINE_FILTERS_SURVIVAL_RATE: float = 0.85
"""Fraction surviving the offline filters (sede/affidabilità/trend/
proprieta/anti-uffici/consumi) — used to size the *Solar API* spend
since Solar is called only on rows that passed the cheap filters."""

# Atoka working-day model — driven by the actual purchase invoice.
ATOKA_PURCHASE_CREDITS: int = 8_000
ATOKA_PURCHASE_PRICE_EUR: float = 3_000.0
ATOKA_PURCHASE_RUNWAY_WORKING_DAYS: int = 44
"""Two calendar months at five working days per week ≈ 44 days."""

# Channel mix used by ``estimate_monthly_costs``. Values from ADR-005.
CHANNEL_MIX_EMAIL: float = 0.85
CHANNEL_MIX_WHATSAPP: float = 0.10
CHANNEL_MIX_PHONE_ONLY: float = 0.05

# Working days per calendar month — IT business norm (5 d/w × ~22 wk
# = 22 d/mo). Used everywhere a "month of pipeline" is referenced.
WORKING_DAYS_PER_MONTH: int = 22


# ---------------------------------------------------------------------------
# Projection results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonthlyCostBreakdown:
    """Output of :func:`estimate_monthly_costs`.

    Frozen + slotted: the result is a value object that downstream
    code reads only for display; making it immutable prevents the
    dashboard from accidentally mutating one tenant's projection
    while iterating over many.
    """

    daily_cap: int
    monthly_email_target: int

    # Variable spend
    atoka_calls: float
    atoka_eur: float
    solar_eur: float
    rendering_eur: float
    validation_eur: float
    email_send_eur: float
    whatsapp_send_eur: float

    # Fixed spend
    infrastructure_eur: float

    # Totals
    total_monthly_eur: float
    cost_per_delivered_email_eur: float

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the operations dashboard / export."""
        return {
            "daily_cap": self.daily_cap,
            "monthly_email_target": self.monthly_email_target,
            "variable": {
                "atoka_calls": round(self.atoka_calls, 1),
                "atoka_eur": round(self.atoka_eur, 2),
                "solar_eur": round(self.solar_eur, 2),
                "rendering_eur": round(self.rendering_eur, 2),
                "validation_eur": round(self.validation_eur, 2),
                "email_send_eur": round(self.email_send_eur, 2),
                "whatsapp_send_eur": round(self.whatsapp_send_eur, 2),
            },
            "fixed": {
                "infrastructure_eur": round(self.infrastructure_eur, 2),
            },
            "totals": {
                "total_monthly_eur": round(self.total_monthly_eur, 2),
                "cost_per_delivered_email_eur": round(
                    self.cost_per_delivered_email_eur, 4
                ),
            },
        }


# ---------------------------------------------------------------------------
# Projection functions
# ---------------------------------------------------------------------------


def estimate_atoka_runway_days(
    credits_remaining: int,
    *,
    daily_call_rate: float | None = None,
) -> float:
    """How many working days a credit balance lasts.

    The default ``daily_call_rate`` is derived from the purchase
    invoice (8 000 credits ÷ 44 working days ≈ 182 calls/day).
    Callers can override when they know the tenant's actual
    consumption from `prospector_runs` / `campaigns` history.

    Returns 0.0 if ``credits_remaining`` is non-positive (calling
    code uses this to alert ops).
    """
    if credits_remaining <= 0:
        return 0.0
    if daily_call_rate is None:
        daily_call_rate = (
            ATOKA_PURCHASE_CREDITS / ATOKA_PURCHASE_RUNWAY_WORKING_DAYS
        )
    if daily_call_rate <= 0:
        return float("inf")
    return credits_remaining / daily_call_rate


def estimate_monthly_costs(
    daily_cap: int,
    *,
    survival_rate: float = PIPELINE_SURVIVAL_RATE_V2,
    offline_survival: float = OFFLINE_FILTERS_SURVIVAL_RATE,
    channel_mix_email: float = CHANNEL_MIX_EMAIL,
    channel_mix_whatsapp: float = CHANNEL_MIX_WHATSAPP,
    working_days: int = WORKING_DAYS_PER_MONTH,
) -> MonthlyCostBreakdown:
    """Project monthly cost for a tenant running at ``daily_cap``.

    The mental model:

      Monthly target = daily_cap × working days
      Atoka calls    = target ÷ overall survival rate (raw → delivered)
      Solar calls    = atoka_calls × offline_survival_rate
                       (Solar is only called on rows that passed
                       cheap filters)
      Validation     = target × 1.0 (we verify every email we send)
      Email sends    = target × email channel share
      WhatsApp sends = target × whatsapp channel share
      Rendering      = target (one render per delivered piece)
      Fixed infra    = constants

    The pure arithmetic here lets ops sweep a daily_cap slider in
    the dashboard and watch the numbers update — no DB hit, no async
    work.
    """
    if daily_cap <= 0:
        raise ValueError("daily_cap must be positive")
    if not (0.0 < survival_rate <= 1.0):
        raise ValueError("survival_rate must be in (0, 1]")
    if not (0.0 < offline_survival <= 1.0):
        raise ValueError("offline_survival must be in (0, 1]")
    if working_days <= 0:
        raise ValueError("working_days must be positive")

    monthly_target = daily_cap * working_days
    atoka_calls = monthly_target / survival_rate
    atoka_eur = atoka_calls * ATOKA_COST_PER_CALL_EUR

    # Solar API spend — Building Insights on every offline-survivor,
    # Data Layers only on delivered-email candidates.
    solar_bi_calls = atoka_calls * offline_survival
    solar_eur = (
        solar_bi_calls * GOOGLE_SOLAR_BUILDING_INSIGHTS_EUR
        + monthly_target * GOOGLE_SOLAR_DATA_LAYERS_EUR
    )

    rendering_eur = monthly_target * RENDERING_PER_EMAIL_EUR
    validation_eur = monthly_target * NEVERBOUNCE_PER_VERIFY_EUR

    email_volume = monthly_target * channel_mix_email
    whatsapp_volume = monthly_target * channel_mix_whatsapp
    email_send_eur = email_volume * RESEND_PER_EMAIL_EUR
    whatsapp_send_eur = whatsapp_volume * WHATSAPP_PER_MESSAGE_EUR

    infrastructure_eur = (
        DELIVERABILITY_INFRA_MONTHLY_EUR
        + WORKSPACE_MAILBOX_MONTHLY_EUR
        + DIALOG360_SUBSCRIPTION_MONTHLY_EUR
    )

    total = (
        atoka_eur
        + solar_eur
        + rendering_eur
        + validation_eur
        + email_send_eur
        + whatsapp_send_eur
        + infrastructure_eur
    )

    return MonthlyCostBreakdown(
        daily_cap=daily_cap,
        monthly_email_target=monthly_target,
        atoka_calls=atoka_calls,
        atoka_eur=atoka_eur,
        solar_eur=solar_eur,
        rendering_eur=rendering_eur,
        validation_eur=validation_eur,
        email_send_eur=email_send_eur,
        whatsapp_send_eur=whatsapp_send_eur,
        infrastructure_eur=infrastructure_eur,
        total_monthly_eur=total,
        cost_per_delivered_email_eur=total / monthly_target,
    )


def cost_constants_snapshot() -> dict[str, float]:
    """Flat dump of every per-unit constant for the ops dashboard.

    Useful for showing the configuration that produced a
    projection — ops want to see "we assumed €0.375/Atoka,
    €0.08/WhatsApp" alongside the monthly total.
    """
    return {
        "atoka_per_call_eur": ATOKA_COST_PER_CALL_EUR,
        "atoka_discovery_per_record_eur": ATOKA_DISCOVERY_COST_PER_RECORD_EUR,
        "google_solar_building_insights_eur": GOOGLE_SOLAR_BUILDING_INSIGHTS_EUR,
        "google_solar_data_layers_eur": GOOGLE_SOLAR_DATA_LAYERS_EUR,
        "neverbounce_per_verify_eur": NEVERBOUNCE_PER_VERIFY_EUR,
        "resend_per_email_eur": RESEND_PER_EMAIL_EUR,
        "whatsapp_per_message_eur": WHATSAPP_PER_MESSAGE_EUR,
        "rendering_per_email_eur": RENDERING_PER_EMAIL_EUR,
        "deliverability_infra_monthly_eur": DELIVERABILITY_INFRA_MONTHLY_EUR,
        "workspace_mailbox_monthly_eur": WORKSPACE_MAILBOX_MONTHLY_EUR,
        "dialog360_subscription_monthly_eur": DIALOG360_SUBSCRIPTION_MONTHLY_EUR,
        "pipeline_survival_rate_v2": PIPELINE_SURVIVAL_RATE_V2,
        "offline_filters_survival_rate": OFFLINE_FILTERS_SURVIVAL_RATE,
        "channel_mix_email": CHANNEL_MIX_EMAIL,
        "channel_mix_whatsapp": CHANNEL_MIX_WHATSAPP,
        "channel_mix_phone_only": CHANNEL_MIX_PHONE_ONLY,
        "working_days_per_month": WORKING_DAYS_PER_MONTH,
        "atoka_purchase_credits": ATOKA_PURCHASE_CREDITS,
        "atoka_purchase_price_eur": ATOKA_PURCHASE_PRICE_EUR,
        "atoka_purchase_runway_working_days": ATOKA_PURCHASE_RUNWAY_WORKING_DAYS,
    }
