"""Identity Agent — resolves the owner of a roof (B2B/B2C) via enrichment.

Pipeline (Sprint 2):

    roof_id
        ↓
    load roof(tenant_id, lat, lng, classification, comune, cap)
        ↓
    Visura.it cadastral lookup (lat,lng → intestatario)
        ↓
    has partita_iva?
    ┌── YES (B2B) ──────────────────┐   ┌── NO (B2C) ──┐
    │ Atoka profile by P.IVA        │   │ private      │
    │   → ateco, revenue, employees │   │ citizen,     │
    │   → domain                    │   │ postal only  │
    │ Hunter.io email-finder        │   │              │
    │   → decision maker email      │   │              │
    │ NeverBounce verification      │   │              │
    │   → sendable flag             │   │              │
    └───────────────┬───────────────┘   └──────┬───────┘
                    ↓                          ↓
             compute pii_hash(B2B)     compute pii_hash(B2C)
                    ↓                          ↓
                    └─────────┬────────────────┘
                              ↓
                  check global_blacklist(pii_hash)
                              ↓
                   upsert subjects + emit subject.identified
                              ↓
                  update roof.status = 'identified'

Degraded paths (each provider is optional — when a key is missing we
emit an `identity.enrichment_skipped` event and keep the best partial
data we have from previous stages):

  - No Visura key → classification taken from `roofs.classification`
    (set by Hunter's geometric heuristic), no owner name, postal
    address from Mapbox geocoding only.
  - Visura OK + no Atoka key → minimal subject row with just the
    business_name + P.IVA, no financials.
  - Atoka OK + no Hunter.io key → subject row without decision maker
    email (outreach falls back to postal channel).
  - Email found + no NeverBounce key → mark
    `decision_maker_email_verified=false` so Outreach skips until the
    key is configured.
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..models.enums import RoofStatus, SubjectType
from ..services.hunter_io_service import (
    HUNTER_COST_PER_CALL_CENTS,
    HunterIoError,
    find_email,
)
from ..services.italian_business_service import (
    ATOKA_COST_PER_CALL_CENTS,
    VISURA_COST_PER_CALL_CENTS,
    AtokaProfile,
    EnrichmentUnavailable,
    VisuraOwner,
    atoka_lookup_by_vat,
    visura_lookup_by_coords,
)
from ..services.neverbounce_service import (
    NEVERBOUNCE_COST_PER_CALL_CENTS,
    NeverBounceError,
    VerificationResult,
    verify_email,
)
from .base import AgentBase

log = get_logger(__name__)


class IdentityInput(BaseModel):
    roof_id: str
    tenant_id: str


class IdentityOutput(BaseModel):
    subject_id: str | None = None
    classification: SubjectType = SubjectType.UNKNOWN
    enrichment_cost_cents: int = 0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    blacklisted: bool = False
    data_sources: list[str] = Field(default_factory=list)
    skipped_providers: list[str] = Field(default_factory=list)


class IdentityAgent(AgentBase[IdentityInput, IdentityOutput]):
    name = "agent.identity"

    async def execute(self, payload: IdentityInput) -> IdentityOutput:
        sb = get_service_client()

        # 1) Load the roof (tenant-scoped)
        roof_res = (
            sb.table("roofs")
            .select(
                "id, tenant_id, lat, lng, address, cap, comune, provincia, "
                "classification, status"
            )
            .eq("id", payload.roof_id)
            .eq("tenant_id", payload.tenant_id)
            .single()
            .execute()
        )
        roof = roof_res.data
        if not roof:
            raise EnrichmentUnavailable(f"roof {payload.roof_id} not found")

        # 1b) Idempotency — if a subject already exists for this roof, return it
        existing = (
            sb.table("subjects")
            .select("id, type, pii_hash")
            .eq("roof_id", payload.roof_id)
            .eq("tenant_id", payload.tenant_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            row = existing.data[0]
            return IdentityOutput(
                subject_id=row["id"],
                classification=SubjectType(row["type"]),
                confidence=1.0,
                data_sources=["cache"],
            )

        # 2) Visura cadastral lookup
        out = IdentityOutput()
        visura: VisuraOwner | None = None
        async with httpx.AsyncClient(timeout=20.0) as http:
            try:
                visura = await visura_lookup_by_coords(
                    float(roof["lat"]), float(roof["lng"]), client=http
                )
                out.enrichment_cost_cents += VISURA_COST_PER_CALL_CENTS
                out.data_sources.append("visura")
                out.classification = visura.classification
            except EnrichmentUnavailable as exc:
                log.info("visura_unavailable", roof_id=payload.roof_id, err=str(exc))
                out.skipped_providers.append(f"visura:{exc}")
                # Fall back on Hunter's geometric classification
                try:
                    out.classification = SubjectType(roof.get("classification", "unknown"))
                except ValueError:
                    out.classification = SubjectType.UNKNOWN

            # 3) Atoka + Hunter.io branch (B2B only)
            atoka: AtokaProfile | None = None
            email_result: Any | None = None
            email_verified = False
            if visura and visura.vat_number:
                try:
                    atoka = await atoka_lookup_by_vat(visura.vat_number, client=http)
                    out.enrichment_cost_cents += ATOKA_COST_PER_CALL_CENTS
                    out.data_sources.append("atoka")
                except EnrichmentUnavailable as exc:
                    log.info("atoka_unavailable", vat=visura.vat_number, err=str(exc))
                    out.skipped_providers.append(f"atoka:{exc}")

                # Hunter.io email finder — prefer Atoka's website, fall back
                # to a best-effort domain from the legal name (rare edge case).
                domain = atoka.website_domain if atoka else None
                if domain:
                    try:
                        email_result = await find_email(
                            domain=domain,
                            first_name=(atoka.decision_maker_name or "").split(" ")[0]
                            if atoka and atoka.decision_maker_name
                            else None,
                            last_name=(atoka.decision_maker_name or "").split(" ")[-1]
                            if atoka and atoka.decision_maker_name
                            else None,
                            company=atoka.legal_name if atoka else visura.business_name,
                            client=http,
                        )
                        out.enrichment_cost_cents += HUNTER_COST_PER_CALL_CENTS
                        if email_result:
                            out.data_sources.append("hunter_io")
                    except HunterIoError as exc:
                        log.info("hunter_io_unavailable", domain=domain, err=str(exc))
                        out.skipped_providers.append(f"hunter_io:{exc}")

                # 4) NeverBounce verification
                if email_result and email_result.email:
                    try:
                        verify = await verify_email(email_result.email, client=http)
                        out.enrichment_cost_cents += NEVERBOUNCE_COST_PER_CALL_CENTS
                        out.data_sources.append("neverbounce")
                        email_verified = verify.result.sendable
                    except NeverBounceError as exc:
                        log.info("neverbounce_unavailable", err=str(exc))
                        out.skipped_providers.append(f"neverbounce:{exc}")

        # 5) Compute pii_hash (always — even when Visura was skipped, so
        # Compliance can still blacklist on partial data later).
        pii_hash = _compute_pii_hash(
            visura=visura,
            atoka=atoka,
            fallback_city=roof.get("comune"),
            fallback_cap=roof.get("cap"),
        )

        # 6) Check global_blacklist
        blacklisted = False
        bl = (
            sb.table("global_blacklist")
            .select("id")
            .eq("pii_hash", pii_hash)
            .limit(1)
            .execute()
        )
        if bl.data:
            blacklisted = True

        # 7) Upsert subject
        subject_row = _build_subject_row(
            tenant_id=payload.tenant_id,
            roof_id=payload.roof_id,
            classification=out.classification,
            visura=visura,
            atoka=atoka,
            email_result=email_result,
            email_verified=email_verified,
            pii_hash=pii_hash,
            data_sources=out.data_sources,
            enrichment_cost_cents=out.enrichment_cost_cents,
            fallback_address=roof.get("address"),
            fallback_cap=roof.get("cap"),
            fallback_city=roof.get("comune"),
            fallback_province=roof.get("provincia"),
        )
        upserted = (
            sb.table("subjects")
            .upsert(subject_row, on_conflict="tenant_id,roof_id")
            .execute()
        )
        subject_id = (upserted.data or [{}])[0].get("id")
        out.subject_id = subject_id
        out.blacklisted = blacklisted

        # 8) Transition the roof status
        new_status = (
            RoofStatus.BLACKLISTED if blacklisted else RoofStatus.IDENTIFIED
        ).value
        sb.table("roofs").update({"status": new_status}).eq("id", payload.roof_id).execute()

        # 9) Confidence score
        out.confidence = _confidence_score(out.data_sources, email_verified)

        await self._emit_event(
            event_type="subject.identified",
            payload={
                "subject_id": subject_id,
                "classification": out.classification.value,
                "data_sources": out.data_sources,
                "skipped": out.skipped_providers,
                "blacklisted": blacklisted,
                "confidence": out.confidence,
            },
            tenant_id=payload.tenant_id,
        )
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _confidence_score(sources: list[str], email_verified: bool) -> float:
    """Rough 0–1 confidence — compounds per enrichment stage completed."""
    score = 0.0
    if "visura" in sources:
        score += 0.35
    if "atoka" in sources:
        score += 0.25
    if "hunter_io" in sources:
        score += 0.2
    if "neverbounce" in sources:
        score += 0.1
    if email_verified:
        score += 0.1
    return round(min(score, 1.0), 2)


def _compute_pii_hash(
    *,
    visura: VisuraOwner | None,
    atoka: AtokaProfile | None,
    fallback_city: str | None,
    fallback_cap: str | None,
) -> str:
    """Produce a deterministic blacklist hash from whatever we have.

    Priority:
      1. B2B: business_name|vat_number
      2. B2C: full_name|cap|city
      3. Fallback: lat,lng-based placeholder (so the row still has a
         NOT-NULL pii_hash).
    """
    if visura and visura.vat_number and visura.business_name:
        return _sha256_normalized(f"{visura.business_name}|{visura.vat_number}")
    if atoka and atoka.legal_name and atoka.vat_number:
        return _sha256_normalized(f"{atoka.legal_name}|{atoka.vat_number}")
    if visura and (visura.owner_first_name or visura.owner_last_name):
        full = f"{visura.owner_first_name or ''} {visura.owner_last_name or ''}".strip()
        addr = (
            f"{visura.postal_address or ''}|{visura.postal_cap or fallback_cap or ''}|"
            f"{visura.postal_city or fallback_city or ''}"
        )
        return _sha256_normalized(f"{full}|{addr}")
    # Last-resort placeholder so the NOT-NULL column is populated.
    marker = f"anon|{fallback_cap or ''}|{fallback_city or ''}"
    return _sha256_normalized(marker)


def _sha256_normalized(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text).casefold().strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _build_subject_row(
    *,
    tenant_id: str,
    roof_id: str,
    classification: SubjectType,
    visura: VisuraOwner | None,
    atoka: AtokaProfile | None,
    email_result: Any | None,
    email_verified: bool,
    pii_hash: str,
    data_sources: list[str],
    enrichment_cost_cents: int,
    fallback_address: str | None,
    fallback_cap: str | None,
    fallback_city: str | None,
    fallback_province: str | None,
) -> dict[str, Any]:
    """Project enrichment results into a subjects-table row."""
    row: dict[str, Any] = {
        "tenant_id": tenant_id,
        "roof_id": roof_id,
        "type": classification.value,
        "pii_hash": pii_hash,
        "data_sources": data_sources,
        "enrichment_cost_cents": enrichment_cost_cents,
        "enrichment_completed_at": datetime.now(timezone.utc).isoformat(),
        "postal_address_line1": (visura.postal_address if visura else None) or fallback_address,
        "postal_cap": (visura.postal_cap if visura else None) or fallback_cap,
        "postal_city": (visura.postal_city if visura else None) or fallback_city,
        "postal_province": (visura.postal_province if visura else None) or fallback_province,
    }

    if classification == SubjectType.B2B:
        row["business_name"] = (atoka.legal_name if atoka else None) or (
            visura.business_name if visura else None
        )
        row["vat_number"] = visura.vat_number if visura else (atoka.vat_number if atoka else None)
        row["ateco_code"] = atoka.ateco_code if atoka else None
        row["ateco_description"] = atoka.ateco_description if atoka else None
        row["yearly_revenue_cents"] = atoka.yearly_revenue_cents if atoka else None
        row["employees"] = atoka.employees if atoka else None
        if atoka:
            row["decision_maker_name"] = atoka.decision_maker_name
            row["decision_maker_role"] = atoka.decision_maker_role
            row["linkedin_url"] = atoka.linkedin_url
        if email_result and getattr(email_result, "email", None):
            row["decision_maker_email"] = email_result.email
            row["decision_maker_email_verified"] = email_verified
    else:
        row["owner_first_name"] = visura.owner_first_name if visura else None
        row["owner_last_name"] = visura.owner_last_name if visura else None

    return row
