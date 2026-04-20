"""Replies Agent — analyses inbound email replies from leads (B.2).

Pipeline:
    lead_reply.id (+ tenant_id, lead_id, body_text)
        ↓
    load full lead context (ROI, campaigns, engagement_score)
        ↓
    Claude structured analysis → {sentiment, intent, urgency, suggested_reply}
        ↓
    UPDATE lead_replies SET sentiment=…, intent=…, urgency=…,
        suggested_reply=…, analyzed_at=now()
        ↓
    emit lead.reply_received event (CRM webhook fanout included)
        ↓
    if urgency=='high' → advance pipeline_status to 'engaged'

Degradation:
    * If Claude fails → set analysis_error, emit event anyway (raw reply is
      still visible in the dashboard card).
    * Always idempotent: re-running on an already-analyzed row is a no-op
      (analyzed_at already set).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from ..core.logging import get_logger
from ..core.supabase_client import get_service_client
from ..services.claude_service import complete_json
from .base import AgentBase

log = get_logger(__name__)


class RepliesInput(BaseModel):
    reply_id: str
    tenant_id: str
    lead_id: str


class RepliesOutput(BaseModel):
    reply_id: str
    lead_id: str
    sentiment: str | None = None
    intent: str | None = None
    urgency: str | None = None
    analysis_error: str | None = None
    skipped: bool = False


class RepliesAgent(AgentBase[RepliesInput, RepliesOutput]):
    name = "agent.replies"

    async def execute(self, payload: RepliesInput) -> RepliesOutput:
        sb = get_service_client()

        # ------------------------------------------------------------------
        # 1) Load the reply row
        # ------------------------------------------------------------------
        reply_res = (
            sb.table("lead_replies")
            .select("*")
            .eq("id", payload.reply_id)
            .eq("tenant_id", payload.tenant_id)
            .limit(1)
            .execute()
        )
        rows = reply_res.data or []
        if not rows:
            raise ValueError(f"lead_reply {payload.reply_id} not found")
        reply = rows[0]

        # Idempotency: already analysed
        if reply.get("analyzed_at"):
            log.info(
                "replies.already_analyzed",
                reply_id=payload.reply_id,
            )
            return RepliesOutput(
                reply_id=payload.reply_id,
                lead_id=payload.lead_id,
                sentiment=reply.get("sentiment"),
                intent=reply.get("intent"),
                urgency=reply.get("urgency"),
                skipped=True,
            )

        # ------------------------------------------------------------------
        # 2) Load lead context for a more accurate Claude analysis
        # ------------------------------------------------------------------
        lead_res = (
            sb.table("leads")
            .select(
                "id, pipeline_status, roi_data, engagement_score, "
                "outreach_sent_at, outreach_opened_at, outreach_clicked_at"
            )
            .eq("id", payload.lead_id)
            .eq("tenant_id", payload.tenant_id)
            .limit(1)
            .execute()
        )
        lead: dict[str, Any] = (lead_res.data or [{}])[0]

        # ------------------------------------------------------------------
        # 3) Claude structured analysis
        # ------------------------------------------------------------------
        body = (reply.get("body_text") or "").strip()
        subject = (reply.get("reply_subject") or "").strip()
        from_email = reply.get("from_email") or ""

        sentiment: str | None = None
        intent: str | None = None
        urgency: str | None = None
        suggested_reply: str | None = None
        analysis_error: str | None = None

        if body:
            try:
                result = await _analyse_with_claude(
                    body=body,
                    subject=subject,
                    from_email=from_email,
                    lead=lead,
                )
                sentiment = result.get("sentiment") or "unclear"
                intent = result.get("intent") or "other"
                urgency = result.get("urgency") or "low"
                suggested_reply = (result.get("suggested_reply") or "").strip() or None
            except Exception as exc:  # noqa: BLE001
                analysis_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                log.warning(
                    "replies.claude_failed",
                    reply_id=payload.reply_id,
                    err=analysis_error,
                )
        else:
            analysis_error = "empty_body"

        # ------------------------------------------------------------------
        # 4) Persist analysis result
        # ------------------------------------------------------------------
        now_iso = datetime.now(timezone.utc).isoformat()
        sb.table("lead_replies").update(
            {
                "sentiment": sentiment,
                "intent": intent,
                "urgency": urgency,
                "suggested_reply": suggested_reply,
                "analysis_error": analysis_error,
                "analyzed_at": now_iso,
            }
        ).eq("id", payload.reply_id).execute()

        # ------------------------------------------------------------------
        # 5) Advance pipeline if reply signals high urgency
        # ------------------------------------------------------------------
        if urgency == "high" or intent in ("interested", "appointment_request"):
            current_status = lead.get("pipeline_status") or ""
            # Only advance if the lead hasn't already moved past 'clicked'
            pipeline_hierarchy = [
                "new", "sent", "delivered", "opened", "clicked",
                "engaged", "whatsapp", "appointment",
            ]
            try:
                current_idx = pipeline_hierarchy.index(current_status)
                engaged_idx = pipeline_hierarchy.index("engaged")
                if current_idx < engaged_idx:
                    sb.table("leads").update(
                        {"pipeline_status": "engaged"}
                    ).eq("id", payload.lead_id).execute()
            except ValueError:
                pass  # status not in hierarchy (e.g. closed_won) — leave it

        # ------------------------------------------------------------------
        # 6) Emit event (includes CRM webhook fanout via base class)
        # ------------------------------------------------------------------
        await self._emit_event(
            event_type="lead.reply_received",
            payload={
                "reply_id": payload.reply_id,
                "from_email": from_email,
                "sentiment": sentiment,
                "intent": intent,
                "urgency": urgency,
                "has_suggested_reply": suggested_reply is not None,
                "analysis_error": analysis_error,
            },
            tenant_id=payload.tenant_id,
            lead_id=payload.lead_id,
        )

        return RepliesOutput(
            reply_id=payload.reply_id,
            lead_id=payload.lead_id,
            sentiment=sentiment,
            intent=intent,
            urgency=urgency,
            analysis_error=analysis_error,
        )


# ---------------------------------------------------------------------------
# Claude helper
# ---------------------------------------------------------------------------

_REPLY_SCHEMA = {
    "type": "object",
    "required": ["sentiment", "intent", "urgency", "suggested_reply"],
    "properties": {
        "sentiment": {
            "type": "string",
            "enum": ["positive", "neutral", "negative", "unclear"],
            "description": "Tono emotivo complessivo della risposta",
        },
        "intent": {
            "type": "string",
            "enum": [
                "interested",
                "question",
                "objection",
                "appointment_request",
                "unsubscribe",
                "other",
            ],
            "description": "Intenzione principale espressa nel messaggio",
        },
        "urgency": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": (
                "high = richiede risposta entro 1h (es. appointment_request, "
                "lead molto interessato); medium = entro 24h; low = informativo"
            ),
        },
        "suggested_reply": {
            "type": "string",
            "description": (
                "Bozza di risposta professionale in italiano, max 150 parole. "
                "Vuota ('') se il messaggio è un'unsubscribe o non richiede risposta."
            ),
        },
    },
}


async def _analyse_with_claude(
    *,
    body: str,
    subject: str,
    from_email: str,
    lead: dict[str, Any],
) -> dict[str, Any]:
    """Call Claude with full context and return structured analysis dict."""
    from ..core.config import settings

    if not settings.anthropic_api_key:
        raise RuntimeError("anthropic_api_key not configured")

    pipeline_status = lead.get("pipeline_status") or "unknown"
    roi = lead.get("roi_data") or {}
    annual_savings = roi.get("annual_savings_eur")
    roi_line = (
        f"ROI stimato: €{annual_savings:,.0f}/anno di risparmio" if annual_savings else ""
    )

    prompt = (
        "Sei un assistente CRM per un installatore di pannelli solari italiano. "
        "Hai ricevuto una risposta email da un potenziale cliente (lead). "
        "Analizza il messaggio e restituisci JSON secondo lo schema richiesto.\n\n"
        f"Stato attuale lead nel pipeline: {pipeline_status}\n"
        f"{roi_line}\n\n"
        f"Oggetto email: {subject or '(assente)'}\n"
        f"Da: {from_email}\n\n"
        "--- CORPO MESSAGGIO ---\n"
        f"{body[:3000]}\n"
        "--- FINE MESSAGGIO ---\n\n"
        "Compila i campi:\n"
        "- sentiment: tono emotivo complessivo\n"
        "- intent: intenzione principale\n"
        "- urgency: priorità di risposta (high/medium/low)\n"
        "- suggested_reply: bozza di risposta professionale in italiano "
        "(max 150 parole, niente intro come 'Gentile...', inizia direttamente "
        "con il contenuto)"
    )

    return await complete_json(
        prompt=prompt,
        system=(
            "Sei un esperto di CRM e comunicazione commerciale nel settore "
            "energie rinnovabili italiano. Rispondi SOLO con il JSON richiesto."
        ),
        schema=_REPLY_SCHEMA,
        max_tokens=600,
        temperature=0.3,
    )
