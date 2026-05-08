"""Haiku-powered rationale generation for the Imminence Predictor.

Called only for leads whose deterministic ``imminence_score >= 60``
(see ``LLM_REASONING_THRESHOLD`` in ``imminence_service``). Cost per
call ≈ €0.001 — capped naturally because we only ask for the top
candidates per tenant per day.

Returns a dict with:
    - primary_reasons: list[str]   (3-5 colloquial Italian phrases)
    - talking_points : list[str]   (2 conversation openers)
    - suggested_action: 'call_now' | 'call_today' | 'send_followup' | 'wait_24h'
    - suggested_channel: 'phone' | 'email' | 'whatsapp'
    - best_time_to_contact: 'morning_9_11' | 'afternoon_14_17' | 'now'

Failure mode: returns ``None``. The cron logs the error and persists
the prediction without reasoning — UI shows "AI" badge with the score
and a generic copy fallback.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic

from ..core.config import settings
from ..core.logging import get_logger
from .imminence_service import ImminenceScores, LeadInputs, _video_seconds

log = get_logger(__name__)

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


SYSTEM_PROMPT = (
    "Sei un assistente commerciale per installatori fotovoltaici B2B in Italia. "
    "Il tuo compito è spiegare in modo CONCRETO e SPECIFICO perché un lead "
    'merita di essere chiamato OGGI. Niente frasi generiche tipo "lead '
    'promettente". Ogni motivazione deve citare un fatto reale (numero di '
    "visite, sezione vista, tempo speso, settore con tasso di conversione X%). "
    "Lingua: italiano colloquiale ma professionale. Restituisci SOLO il JSON "
    "richiesto, niente preamboli."
)


def _build_user_prompt(inputs: LeadInputs, scores: ImminenceScores) -> str:
    visits_count = len(
        {e.get("session_id") for e in inputs.portal_events_last_7d if e.get("session_id")}
    )
    video_sec = _video_seconds(inputs.portal_events_last_7d)
    bolletta = any(
        e.get("event_kind") == "portal.bolletta_uploaded" for e in inputs.portal_events_last_7d
    )
    cta_clicks = sum(
        1
        for e in inputs.portal_events_last_7d
        if e.get("event_kind")
        in ("portal.whatsapp_click", "portal.appointment_click", "portal.email_reply_click")
    )

    sector = inputs.predicted_sector or "non classificato"
    business = inputs.business_name or "questa azienda"
    employees = inputs.employees if inputs.employees is not None else "n/d"
    kwp = f"{int(inputs.estimated_kwp)} kW" if inputs.estimated_kwp else "non stimato"

    return f"""# Lead
- Azienda: {business}
- Settore: {sector}
- Dipendenti: {employees}
- Impianto stimato: {kwp}

# Engagement (ultimi 7 giorni)
- Sessioni distinte sul portale: {visits_count}
- Tempo totale su video: {video_sec}s
- Bolletta caricata: {"sì" if bolletta else "no"}
- Click su CTA (WhatsApp/Appuntamento/Risposta email): {cta_clicks}
- Engagement score: {inputs.engagement_score}/100

# Punteggi predittivi
- Imminence: {scores.final}/100
- Comportamentale: {scores.behavioral}/100
- Temporale: {scores.temporal}/100
- Contestuale: {scores.contextual}/100
- Comparativo: {scores.comparative}/100

# Output richiesto
Restituisci SOLO il JSON nel seguente formato (senza commenti, senza markdown):
{{
  "primary_reasons": ["motivazione 1 (max 90 caratteri)", "motivazione 2", "motivazione 3"],
  "talking_points": ["argomento da aprire (max 80 caratteri)", "secondo punto"],
  "suggested_action": "call_now|call_today|send_followup|wait_24h",
  "suggested_channel": "phone|email|whatsapp",
  "best_time_to_contact": "morning_9_11|afternoon_14_17|now"
}}

Regole:
- 3 reasons specifiche al lead, basate sui dati sopra
- Se "Bolletta caricata: sì" o cta_clicks >= 1 → suggested_action = "call_now"
- Se sessioni >= 3 → suggested_action almeno "call_today"
- Niente saluti, niente "ciao", solo JSON."""


async def generate_reasoning(inputs: LeadInputs, scores: ImminenceScores) -> dict[str, Any] | None:
    """Async-safe Haiku call. Returns None on any failure."""
    try:
        client = _get_client()
    except RuntimeError as exc:
        log.warning("imminence.haiku_disabled", err=str(exc))
        return None

    try:
        resp = await client.messages.create(
            model=settings.anthropic_haiku_model,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(inputs, scores)}],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("imminence.haiku_request_failed", err=str(exc))
        return None

    text = (resp.content[0].text if resp.content else "").strip()
    if text.startswith("```"):
        # Strip accidental code fence even though prompt forbids it.
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("imminence.haiku_invalid_json", err=str(exc), raw=text[:200])
        return None

    # Defensive shape check — we control the prompt but a misbehaving
    # response shouldn't crash the cron.
    return {
        "primary_reasons": _ensure_str_list(parsed.get("primary_reasons"), max_n=5),
        "talking_points": _ensure_str_list(parsed.get("talking_points"), max_n=3),
        "suggested_action": _coerce_enum(
            parsed.get("suggested_action"),
            ("call_now", "call_today", "send_followup", "wait_24h"),
        ),
        "suggested_channel": _coerce_enum(
            parsed.get("suggested_channel"), ("phone", "email", "whatsapp")
        ),
        "best_time_to_contact": _coerce_enum(
            parsed.get("best_time_to_contact"),
            ("morning_9_11", "afternoon_14_17", "now"),
        ),
    }


def _ensure_str_list(v: Any, *, max_n: int) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:200])
            if len(out) >= max_n:
                break
    return out


def _coerce_enum(v: Any, allowed: tuple[str, ...]) -> str | None:
    return v if isinstance(v, str) and v in allowed else None
