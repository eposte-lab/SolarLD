"""Compliance Agent — opt-outs, blacklist, retention, GDPR audit.

Responsibilities:
 - One-click opt-out (email link, QR code, inbound email)
 - Hard-bounce / complaint auto-blacklist
 - Propagate blacklist → cancel pending campaigns
 - Monthly retention cron (delete leads > 24 months)
 - Right-to-erasure requests
"""

from __future__ import annotations

import hashlib
import unicodedata

from pydantic import BaseModel

from ..core.supabase_client import get_service_client
from ..models.enums import BlacklistReason
from .base import AgentBase


class ComplianceInput(BaseModel):
    pii_hash: str
    reason: BlacklistReason
    source: str | None = None
    notes: str | None = None


class ComplianceOutput(BaseModel):
    added_to_blacklist: bool = False
    campaigns_cancelled: int = 0


class ComplianceAgent(AgentBase[ComplianceInput, ComplianceOutput]):
    name = "agent.compliance"

    async def execute(self, payload: ComplianceInput) -> ComplianceOutput:
        sb = get_service_client()

        # 1) Insert into global_blacklist (idempotent via UNIQUE(pii_hash))
        try:
            sb.table("global_blacklist").insert(
                {
                    "pii_hash": payload.pii_hash,
                    "reason": payload.reason.value,
                    "source": payload.source,
                    "notes": payload.notes,
                }
            ).execute()
            added = True
        except Exception:
            added = False  # already present → no-op

        # 2) Cancel any pending campaigns for matching subjects
        subjects = (
            sb.table("subjects")
            .select("id, tenant_id")
            .eq("pii_hash", payload.pii_hash)
            .execute()
        )
        cancelled = 0
        for subj in subjects.data or []:
            leads = (
                sb.table("leads")
                .select("id")
                .eq("subject_id", subj["id"])
                .execute()
            )
            lead_ids = [x["id"] for x in (leads.data or [])]
            if not lead_ids:
                continue
            res = (
                sb.table("campaigns")
                .update({"status": "cancelled", "failure_reason": "blacklisted"})
                .in_("lead_id", lead_ids)
                .eq("status", "pending")
                .execute()
            )
            cancelled += len(res.data or [])
            sb.table("leads").update({"pipeline_status": "blacklisted"}).in_(
                "id", lead_ids
            ).execute()

        await self._emit_event(
            event_type="compliance.blacklist_added",
            payload={
                "pii_hash": payload.pii_hash,
                "reason": payload.reason.value,
                "campaigns_cancelled": cancelled,
            },
        )

        return ComplianceOutput(added_to_blacklist=added, campaigns_cancelled=cancelled)

    # ---- Hash utilities ----

    @staticmethod
    def hash_b2b(business_name: str, vat_number: str) -> str:
        return _sha256_normalized(f"{business_name}|{vat_number}")

    @staticmethod
    def hash_b2c(full_name: str, full_address: str) -> str:
        return _sha256_normalized(f"{full_name}|{full_address}")


def _sha256_normalized(text: str) -> str:
    """Unicode-normalize, casefold, then SHA-256."""
    norm = unicodedata.normalize("NFKD", text).casefold().strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()
