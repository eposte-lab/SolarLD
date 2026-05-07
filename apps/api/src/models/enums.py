"""Domain enums — mirror Postgres enum types from migrations."""

from __future__ import annotations

from enum import StrEnum


class TenantTier(StrEnum):
    FOUNDING = "founding"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class TenantStatus(StrEnum):
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    PAUSED = "paused"
    CHURNED = "churned"


class TerritoryType(StrEnum):
    CAP = "cap"
    COMUNE = "comune"
    PROVINCIA = "provincia"
    REGIONE = "regione"


class RoofDataSource(StrEnum):
    GOOGLE_SOLAR = "google_solar"
    MAPBOX_AI_FALLBACK = "mapbox_ai_fallback"


class SubjectType(StrEnum):
    B2B = "b2b"
    B2C = "b2c"
    UNKNOWN = "unknown"


class RoofStatus(StrEnum):
    DISCOVERED = "discovered"
    IDENTIFIED = "identified"
    SCORED = "scored"
    RENDERED = "rendered"
    OUTREACH_SENT = "outreach_sent"
    ENGAGED = "engaged"
    CONVERTED = "converted"
    BLACKLISTED = "blacklisted"
    REJECTED = "rejected"


class LeadScoreTier(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    REJECTED = "rejected"


class LeadStatus(StrEnum):
    NEW = "new"
    SENT = "sent"
    DELIVERED = "delivered"
    OPENED = "opened"
    CLICKED = "clicked"
    ENGAGED = "engaged"
    WHATSAPP = "whatsapp"
    APPOINTMENT = "appointment"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"
    BLACKLISTED = "blacklisted"


class OutreachChannel(StrEnum):
    EMAIL = "email"
    POSTAL = "postal"
    WHATSAPP = "whatsapp"


class InstallerFeedback(StrEnum):
    QUALIFIED = "qualified"
    NOT_INTERESTED = "not_interested"
    NOT_REACHABLE = "not_reachable"
    CONTRACT_SIGNED = "contract_signed"
    WRONG_DATA = "wrong_data"


class CampaignStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BlacklistReason(StrEnum):
    USER_OPTOUT = "user_optout"
    MANUAL = "manual"
    REGULATORY = "regulatory"
    BOUNCE_HARD = "bounce_hard"
    COMPLAINT = "complaint"
