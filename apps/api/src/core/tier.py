"""Tier gating — backend enforcement of commercial-tier capabilities.

Mirror of `apps/dashboard/src/lib/data/tier.ts`. The matrix below is
the single source of truth for what each tier can do on the backend
(agents, API endpoints, queue workers). The dashboard version must
stay in sync — any change here should be reflected there in the same
commit.

Resolution order (first wins):
  1. Explicit override in ``tenants.settings.feature_flags[<key>]``.
  2. Default from ``CAPABILITIES[tier][<key>]``.

Budgets follow the same rule but ``None`` means "unlimited".

Usage::

    from src.core.tier import can_tenant_use, Capability

    tenant = await fetch_tenant_row(tenant_id)  # dict with tier, settings
    if not can_tenant_use(tenant, Capability.POSTAL_OUTREACH):
        raise TierGateError(Capability.POSTAL_OUTREACH, tenant["tier"])
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, TypedDict

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

TenantTier = Literal["founding", "pro", "enterprise"]

TIER_ORDER: tuple[TenantTier, ...] = ("founding", "pro", "enterprise")

TIER_LABEL: dict[TenantTier, str] = {
    "founding": "Founding",
    "pro": "Pro",
    "enterprise": "Enterprise",
}


class Capability(str, Enum):
    """Every gated feature must be represented here."""

    EMAIL_OUTREACH = "email_outreach"
    POSTAL_OUTREACH = "postal_outreach"
    WHATSAPP_OUTREACH = "whatsapp_outreach"
    REALTIME_TIMELINE = "realtime_timeline"
    ADVANCED_ANALYTICS = "advanced_analytics"
    TEMPLATE_EDITOR = "template_editor"
    AB_TESTING_TEMPLATES = "ab_testing_templates"
    CRM_OUTBOUND_WEBHOOKS = "crm_outbound_webhooks"
    API_ACCESS = "api_access"
    CUSTOM_BRAND_DOMAIN = "custom_brand_domain"
    BULK_EXPORT = "bulk_export"


class Budget(str, Enum):
    """Monthly budget knobs (cents, or None=unlimited)."""

    MONTHLY_SCAN_BUDGET_CENTS = "monthly_scan_budget_cents"
    MONTHLY_OUTREACH_BUDGET_CENTS = "monthly_outreach_budget_cents"


class TenantRow(TypedDict, total=False):
    """Subset of ``tenants`` we need for tier resolution."""

    id: str
    business_name: str
    tier: TenantTier
    settings: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Matrix — keep in sync with dashboard/src/lib/data/tier.ts
# ---------------------------------------------------------------------------

CAPABILITIES: dict[TenantTier, dict[Capability, bool]] = {
    "founding": {
        Capability.EMAIL_OUTREACH: True,
        Capability.POSTAL_OUTREACH: False,
        Capability.WHATSAPP_OUTREACH: False,
        Capability.REALTIME_TIMELINE: False,
        Capability.ADVANCED_ANALYTICS: False,
        Capability.TEMPLATE_EDITOR: False,
        Capability.AB_TESTING_TEMPLATES: False,
        Capability.CRM_OUTBOUND_WEBHOOKS: False,
        Capability.API_ACCESS: False,
        Capability.CUSTOM_BRAND_DOMAIN: False,
        Capability.BULK_EXPORT: False,
    },
    "pro": {
        Capability.EMAIL_OUTREACH: True,
        Capability.POSTAL_OUTREACH: True,
        Capability.WHATSAPP_OUTREACH: True,
        Capability.REALTIME_TIMELINE: True,
        Capability.ADVANCED_ANALYTICS: True,
        Capability.TEMPLATE_EDITOR: False,
        Capability.AB_TESTING_TEMPLATES: False,
        Capability.CRM_OUTBOUND_WEBHOOKS: True,
        Capability.API_ACCESS: False,
        Capability.CUSTOM_BRAND_DOMAIN: True,
        Capability.BULK_EXPORT: True,
    },
    "enterprise": {
        Capability.EMAIL_OUTREACH: True,
        Capability.POSTAL_OUTREACH: True,
        Capability.WHATSAPP_OUTREACH: True,
        Capability.REALTIME_TIMELINE: True,
        Capability.ADVANCED_ANALYTICS: True,
        Capability.TEMPLATE_EDITOR: True,
        Capability.AB_TESTING_TEMPLATES: True,
        Capability.CRM_OUTBOUND_WEBHOOKS: True,
        Capability.API_ACCESS: True,
        Capability.CUSTOM_BRAND_DOMAIN: True,
        Capability.BULK_EXPORT: True,
    },
}

BUDGETS: dict[TenantTier, dict[Budget, int | None]] = {
    "founding":   {Budget.MONTHLY_SCAN_BUDGET_CENTS: 15_000, Budget.MONTHLY_OUTREACH_BUDGET_CENTS: 10_000},
    "pro":        {Budget.MONTHLY_SCAN_BUDGET_CENTS: 50_000, Budget.MONTHLY_OUTREACH_BUDGET_CENTS: 40_000},
    "enterprise": {Budget.MONTHLY_SCAN_BUDGET_CENTS: None,   Budget.MONTHLY_OUTREACH_BUDGET_CENTS: None},
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


@dataclass
class TierGateError(Exception):
    """Raised when a tenant attempts an action their tier forbids."""

    capability: Capability
    current_tier: TenantTier
    required_tier: TenantTier

    def __str__(self) -> str:
        return (
            f"Capability '{self.capability.value}' requires tier "
            f"'{self.required_tier}', current is '{self.current_tier}'"
        )


@dataclass
class BudgetExceededError(Exception):
    """Raised when a monthly budget (scan/outreach) is exhausted."""

    budget: Budget
    current_tier: TenantTier
    limit_cents: int
    used_cents: int

    def __str__(self) -> str:
        return (
            f"Budget '{self.budget.value}' exceeded: used {self.used_cents} / "
            f"{self.limit_cents} cents on tier '{self.current_tier}'"
        )


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


def _feature_flag(tenant: TenantRow, key: str) -> Any:
    settings = tenant.get("settings") or {}
    flags = settings.get("feature_flags") or {}
    return flags.get(key)


def can_tenant_use(tenant: TenantRow, capability: Capability) -> bool:
    """Return True if the tenant is allowed to use ``capability``.

    Considers explicit ``feature_flags`` overrides first.
    """
    tier: TenantTier = tenant.get("tier") or "founding"
    override = _feature_flag(tenant, capability.value)
    if isinstance(override, bool):
        return override
    return CAPABILITIES[tier].get(capability, False)


def get_tier_budget(tenant: TenantRow, budget: Budget) -> int | None:
    """Monthly budget for the tenant, applying override then default.

    Returns None for unlimited (enterprise, or explicit ``null`` flag).
    """
    tier: TenantTier = tenant.get("tier") or "founding"
    override = _feature_flag(tenant, budget.value)
    if override is None and budget.value in (tenant.get("settings") or {}):
        # explicit null override ("unlimited")
        return None
    if isinstance(override, int):
        return override
    return BUDGETS[tier].get(budget)


def minimum_tier_for(capability: Capability) -> TenantTier:
    """Lowest tier that includes ``capability`` by default."""
    for t in TIER_ORDER:
        if CAPABILITIES[t].get(capability, False):
            return t
    return "enterprise"


def require_capability(tenant: TenantRow, capability: Capability) -> None:
    """Raise ``TierGateError`` if the tenant lacks ``capability``."""
    if not can_tenant_use(tenant, capability):
        tier: TenantTier = tenant.get("tier") or "founding"
        raise TierGateError(
            capability=capability,
            current_tier=tier,
            required_tier=minimum_tier_for(capability),
        )
