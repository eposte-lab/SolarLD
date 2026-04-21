"""Conversation Agent — WhatsApp inbound automation (Part B.8).

Pipeline:
    lead sends WhatsApp message to tenant's WA number
        ↓
    360dialog webhook → POST /v1/webhooks/whatsapp?tenant_id=…
        ↓
    ConversationAgent.execute():
        1. Load or create `conversations` row for (tenant, phone)
        2. If state != 'active' → skip (handoff/closed → operator takes over)
        3. Append inbound message to thread
        4. Check for handoff triggers (human keywords OR turn limit)
        5. Build Claude prompt with full lead context + conversation history
        6. Get Claude reply (conversational, capped to 3 sentences)
        7. Send reply via 360dialog API
        8. Append AI reply to thread + update counters
        9. Emit events (lead.whatsapp_replied, lead.whatsapp_handoff)
        ↓
    Dashboard shows conversation in lead detail page (lead-conversations-card)

Handoff triggers:
  - Message contains one of HANDOFF_KEYWORDS (umano, operatore, consulente, …)
  - auto_replies_count reaches AUTO_REPLY_LIMIT (default 2)

On handoff: send a polite closing message, set state='handoff', emit event.
Operator sees the conversation thread and continues manually on WhatsApp.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..services.claude_service import complete as claude_complete
from ..services.dialog360_service import send_wa_message as _send_wa_message_impl
from .base import AgentBase

log = get_logger(__name__)

# Number of AI auto-replies before handing off to the human operator.
AUTO_REPLY_LIMIT = 2

# Keywords in the inbound message that trigger immediate human handoff.
_HANDOFF_RE = re.compile(
    r"\b(umano|persona|operatore|consulente|addetto|richiamo|callback"
    r"|richiam[ao]|parlare\s+con|metti\s+in\s+contatto|voglio\s+parlare"
    r"|numero\s+di\s+telefono|chiamatemi|chiamami|sentiamoci)\b",
    re.IGNORECASE,
)

# Message sent to the lead when the AI hands off to the operator.
_HANDOFF_MESSAGE = (
    "Grazie per il messaggio! Ho avvisato il nostro consulente che ti "
    "contatterà al più presto. Rimani in attesa — a breve qualcuno del "
    "team si farà vivo."
)

class ConversationInput(BaseModel):
    tenant_id: str
    lead_id: str
    wa_phone: str      # E.164 without "+", e.g. "393331234567"
    incoming_text: str
    message_id: str = Field(default="")   # 360dialog wamid


class ConversationOutput(BaseModel):
    lead_id: str
    conversation_id: str | None = None
    state: str = "active"
    reply_sent: bool = False
    handoff: bool = False
    skipped: bool = False
    reason: str | None = None


class ConversationAgent(AgentBase[ConversationInput, ConversationOutput]):
    name = "agent.conversation"

    async def execute(self, payload: ConversationInput) -> ConversationOutput:  # noqa: C901
        sb = get_service_client()
        now_iso = datetime.now(timezone.utc).isoformat()

        # ------------------------------------------------------------------
        # 1) Load or create conversation row
        # ------------------------------------------------------------------
        conv_res = (
            sb.table("conversations")
            .select("*")
            .eq("tenant_id", payload.tenant_id)
            .eq("whatsapp_phone", payload.wa_phone)
            .limit(1)
            .execute()
        )
        conv: dict[str, Any] | None = (conv_res.data or [None])[0]

        if conv is None:
            # Create new conversation tied to this lead
            insert_res = (
                sb.table("conversations")
                .insert({
                    "tenant_id": payload.tenant_id,
                    "lead_id": payload.lead_id,
                    "channel": "whatsapp",
                    "whatsapp_phone": payload.wa_phone,
                    "state": "active",
                    "messages": [],
                    "last_inbound_id": payload.message_id or None,
                    "last_message_at": now_iso,
                })
                .execute()
            )
            rows = insert_res.data or []
            if not rows:
                log.error(
                    "conversation.create_failed",
                    tenant_id=payload.tenant_id,
                    lead_id=payload.lead_id,
                )
                return ConversationOutput(
                    lead_id=payload.lead_id,
                    skipped=True,
                    reason="db_error",
                )
            conv = rows[0]

        conv_id: str = conv["id"]
        state: str = conv.get("state") or "active"
        messages: list[dict[str, Any]] = list(conv.get("messages") or [])
        auto_replies: int = int(conv.get("auto_replies_count") or 0)

        # ------------------------------------------------------------------
        # 2) Skip if not active (operator has taken over or conversation closed)
        # ------------------------------------------------------------------
        if state != "active":
            log.info(
                "conversation.skipped_inactive",
                conversation_id=conv_id,
                state=state,
            )
            return ConversationOutput(
                lead_id=payload.lead_id,
                conversation_id=conv_id,
                state=state,
                skipped=True,
                reason=f"state_{state}",
            )

        # ------------------------------------------------------------------
        # 3) Append inbound message
        # ------------------------------------------------------------------
        messages.append({
            "role": "lead",
            "content": payload.incoming_text,
            "ts": now_iso,
            **({"id": payload.message_id} if payload.message_id else {}),
        })

        # ------------------------------------------------------------------
        # 4) Handoff check
        # ------------------------------------------------------------------
        needs_handoff = (
            bool(_HANDOFF_RE.search(payload.incoming_text))
            or auto_replies >= AUTO_REPLY_LIMIT
        )

        if needs_handoff:
            # Send polite closing message
            sent = await _send_wa_message(
                phone=payload.wa_phone,
                text=_HANDOFF_MESSAGE,
                tenant_id=payload.tenant_id,
            )
            if sent:
                messages.append({
                    "role": "ai",
                    "content": _HANDOFF_MESSAGE,
                    "ts": now_iso,
                    "handoff_message": True,
                })

            sb.table("conversations").update({
                "state": "handoff",
                "messages": messages,
                "last_inbound_id": payload.message_id or None,
                "last_message_at": now_iso,
                "turn_count": len(messages),
            }).eq("id", conv_id).execute()

            await self._emit_event(
                event_type="lead.whatsapp_handoff",
                payload={
                    "lead_id": payload.lead_id,
                    "conversation_id": conv_id,
                    "reason": "human_request" if _HANDOFF_RE.search(payload.incoming_text) else "turn_limit",
                    "auto_replies_count": auto_replies,
                },
                tenant_id=payload.tenant_id,
                lead_id=payload.lead_id,
            )
            log.info(
                "conversation.handoff",
                conversation_id=conv_id,
                lead_id=payload.lead_id,
                auto_replies=auto_replies,
            )
            return ConversationOutput(
                lead_id=payload.lead_id,
                conversation_id=conv_id,
                state="handoff",
                reply_sent=sent,
                handoff=True,
            )

        # ------------------------------------------------------------------
        # 5) Load lead context for Claude prompt
        # ------------------------------------------------------------------
        lead_res = (
            sb.table("leads")
            .select(
                "id, public_slug, pipeline_status, roi_data, "
                "outreach_sent_at, outreach_opened_at"
            )
            .eq("id", payload.lead_id)
            .eq("tenant_id", payload.tenant_id)
            .limit(1)
            .execute()
        )
        lead: dict[str, Any] = (lead_res.data or [{}])[0]

        subject_res = (
            sb.table("subjects")
            .select(
                "type, business_name, owner_first_name, owner_last_name, "
                "decision_maker_name, postal_city"
            )
            .eq("id", lead.get("subject_id", ""))
            .limit(1)
            .execute()
            if lead.get("subject_id")
            else type("R", (), {"data": []})()
        )
        subject: dict[str, Any] = (
            (subject_res.data or [{}])[0]
            if hasattr(subject_res, "data")
            else {}
        )

        # We need subject_id — load lead with it
        lead_full_res = (
            sb.table("leads")
            .select("subject_id, roof_id, roi_data")
            .eq("id", payload.lead_id)
            .limit(1)
            .execute()
        )
        lead_full: dict[str, Any] = (lead_full_res.data or [{}])[0]

        subject_res2 = (
            sb.table("subjects")
            .select(
                "type, business_name, owner_first_name, owner_last_name, "
                "decision_maker_name, postal_city"
            )
            .eq("id", lead_full.get("subject_id", ""))
            .limit(1)
            .execute()
            if lead_full.get("subject_id")
            else None
        )
        subject = (
            (subject_res2.data or [{}])[0] if subject_res2 and subject_res2.data else {}
        )

        tenant_res = (
            sb.table("tenants")
            .select("business_name")
            .eq("id", payload.tenant_id)
            .limit(1)
            .execute()
        )
        tenant: dict[str, Any] = (tenant_res.data or [{}])[0]
        tenant_name = (tenant.get("business_name") or "SolarLead").strip()

        # Build lead display name
        s_type = (subject.get("type") or "").lower()
        if s_type == "b2b":
            display_name = (
                subject.get("decision_maker_name")
                or subject.get("business_name")
                or "il responsabile"
            ).strip()
        else:
            first = (subject.get("owner_first_name") or "").strip()
            last = (subject.get("owner_last_name") or "").strip()
            display_name = " ".join(p for p in (first, last) if p) or "il proprietario"

        roi = lead_full.get("roi_data") or {}
        kwp = roi.get("estimated_kwp")
        savings = roi.get("yearly_savings_eur")
        payback = roi.get("payback_years")
        city = subject.get("postal_city") or ""

        roi_lines: list[str] = []
        if kwp:
            roi_lines.append(f"- Potenza installabile: {kwp} kWp")
        if savings:
            roi_lines.append(f"- Risparmio annuo stimato: €{int(savings):,}".replace(",", "."))
        if payback:
            roi_lines.append(f"- Rientro stimato: {payback} anni")
        roi_context = "\n".join(roi_lines) if roi_lines else "Dati di stima non ancora disponibili."

        # ------------------------------------------------------------------
        # 6) Build Claude prompt with conversation history
        # ------------------------------------------------------------------
        history_lines: list[str] = []
        # Last 6 messages max to keep prompt short
        for msg in messages[-6:]:
            role_label = "Lead" if msg.get("role") == "lead" else "Assistente AI"
            history_lines.append(f"{role_label}: {msg.get('content', '')}")
        history_text = "\n".join(history_lines) if history_lines else "(prima risposta)"

        location_hint = f" a {city}" if city else ""

        system_prompt = (
            f"Sei l'assistente AI di {tenant_name}, installatore fotovoltaico{location_hint}.\n"
            f"Stai rispondendo su WhatsApp a {display_name}, "
            f"che ha ricevuto un'analisi del suo {'edificio' if s_type == 'b2b' else 'tetto'}.\n\n"
            f"Dati tecnici del lead:\n{roi_context}\n\n"
            f"Linee guida:\n"
            f"- Rispondi in italiano, tono cordiale e professionale\n"
            f"- Massimo 2-3 frasi. WhatsApp, non una mail.\n"
            f"- Non fare promesse su prezzi o tempi\n"
            f"- Se il lead chiede un appuntamento o un preventivo, di' che un nostro consulente "
            f"lo contatterà entro 24 ore e chiedi la disponibilità oraria\n"
            f"- Se non sai rispondere, di' che passerai la conversazione a un consulente\n"
            f"- Non menzionare mai che sei un'AI a meno che non ti venga chiesto direttamente"
        )

        user_prompt = (
            f"Conversazione precedente:\n{history_text}\n\n"
            f"Nuovo messaggio del lead: «{payload.incoming_text}»\n\n"
            f"Rispondi ora. Solo la risposta, senza preamboli."
        )

        ai_reply: str | None = None
        if settings.anthropic_api_key:
            try:
                ai_reply = await claude_complete(
                    user_prompt,
                    system=system_prompt,
                    max_tokens=200,
                    temperature=0.7,
                )
                ai_reply = (ai_reply or "").strip()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "conversation.claude_failed",
                    conversation_id=conv_id,
                    err=str(exc),
                )
                ai_reply = None

        if not ai_reply:
            # Graceful fallback
            ai_reply = (
                f"Grazie per il messaggio! Un nostro consulente di {tenant_name} "
                f"la contatterà al più presto."
            )

        # ------------------------------------------------------------------
        # 7) Send reply via 360dialog
        # ------------------------------------------------------------------
        sent = await _send_wa_message(
            phone=payload.wa_phone,
            text=ai_reply,
            tenant_id=payload.tenant_id,
        )

        # ------------------------------------------------------------------
        # 8) Persist updated thread
        # ------------------------------------------------------------------
        if sent:
            messages.append({
                "role": "ai",
                "content": ai_reply,
                "ts": datetime.now(timezone.utc).isoformat(),
            })

        new_auto_replies = auto_replies + (1 if sent else 0)
        sb.table("conversations").update({
            "messages": messages,
            "last_inbound_id": payload.message_id or None,
            "last_message_at": now_iso,
            "turn_count": len(messages),
            "auto_replies_count": new_auto_replies,
        }).eq("id", conv_id).execute()

        # Advance pipeline to 'whatsapp' if not already engaged further
        _advance_pipeline_whatsapp(sb, payload.lead_id, payload.tenant_id)

        # ------------------------------------------------------------------
        # 9) Emit event
        # ------------------------------------------------------------------
        await self._emit_event(
            event_type="lead.whatsapp_replied",
            payload={
                "lead_id": payload.lead_id,
                "conversation_id": conv_id,
                "auto_replies_count": new_auto_replies,
                "reply_sent": sent,
            },
            tenant_id=payload.tenant_id,
            lead_id=payload.lead_id,
        )

        log.info(
            "conversation.replied",
            conversation_id=conv_id,
            lead_id=payload.lead_id,
            sent=sent,
            auto_replies=new_auto_replies,
        )
        return ConversationOutput(
            lead_id=payload.lead_id,
            conversation_id=conv_id,
            state="active",
            reply_sent=sent,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _send_wa_message(*, phone: str, text: str, tenant_id: str) -> bool:
    """Thin adapter — delegates to dialog360_service.send_wa_message.

    Returns True on success, False on any error.  The conversation row
    is always updated even when this returns False, so a degraded send
    doesn't lose the inbound message.
    """
    wamid = await _send_wa_message_impl(phone=phone, text=text, tenant_id=tenant_id)
    return wamid is not None


_PIPELINE_ORDER = [
    "new", "sent", "delivered", "opened", "clicked",
    "engaged", "whatsapp", "appointment", "closed_won", "closed_lost",
]


def _advance_pipeline_whatsapp(
    sb: Any, lead_id: str, tenant_id: str
) -> None:
    """Advance lead pipeline to 'whatsapp' if not already at a higher stage."""
    try:
        res = (
            sb.table("leads")
            .select("pipeline_status")
            .eq("id", lead_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return
        current = rows[0].get("pipeline_status", "new")
        current_idx = _PIPELINE_ORDER.index(current) if current in _PIPELINE_ORDER else 0
        wa_idx = _PIPELINE_ORDER.index("whatsapp")
        if current_idx < wa_idx:
            sb.table("leads").update({"pipeline_status": "whatsapp"}).eq(
                "id", lead_id
            ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("conversation.pipeline_advance_failed", err=str(exc))
