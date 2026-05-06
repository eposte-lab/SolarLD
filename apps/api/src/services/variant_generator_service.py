"""Cluster copy variant generator — Claude Haiku.

Generates pairs of A/B email copy variants for a given
(tenant, cluster_signature, round) using Claude Haiku.  Each variant
carries 4 fields consumed by the premium template:

  copy_subject          — email subject line (≤60 chars)
  copy_opening_line     — first personalised paragraph (1-2 sentences)
  copy_proposition_line — value proposition for the body section
  cta_primary_label     — CTA button label (≤30 chars)

Variant A is always "identificativo" (sector/role-led, lower risk).
Variant B is always "economico-emotivo" (€ savings, higher reward).
From round 2 onward, the previous winner is used as baseline for A and
B is a challenger that varies a single dimension (CTA or opening or
proposition).

All text is in Italian.  The Haiku output is parsed as JSON; on parse
failure we fall back to deterministic templates so a missing API key
never blocks the send pipeline.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from ..core.config import settings
from ..core.logging import get_logger
from .cluster_service import describe_cluster

log = get_logger(__name__)


@dataclass(slots=True)
class VariantSpec:
    """4 copy fields for one A/B variant."""

    copy_subject: str
    copy_opening_line: str
    copy_proposition_line: str
    cta_primary_label: str
    variant_label: str           # 'A' or 'B'
    generated_by: str = "haiku"  # 'haiku' | 'manual' | 'seed'


# ── Fallback seed copy ────────────────────────────────────────────────
# Used when Haiku is unavailable or returns unparseable output.
# Variant A is identificativo, B is economico-emotivo.

_SEED_VARIANT_A = VariantSpec(
    copy_subject="Analisi fotovoltaica per la vostra sede",
    copy_opening_line=(
        "Ho analizzato la vostra sede e i risultati sono interessanti per "
        "il vostro settore."
    ),
    copy_proposition_line=(
        "Vi proponiamo un sopralluogo gratuito per validare i dati e "
        "presentare un preventivo completo senza impegno."
    ),
    cta_primary_label="Scopri l'analisi completa",
    variant_label="A",
    generated_by="seed",
)

_SEED_VARIANT_B = VariantSpec(
    copy_subject="Risparmiate migliaia di euro sulla bolletta ogni anno",
    copy_opening_line=(
        "Con un impianto fotovoltaico sulla vostra sede potreste eliminare "
        "una parte significativa della bolletta energetica — già dal primo anno."
    ),
    copy_proposition_line=(
        "I numeri della vostra analisi sono concreti: un investimento che si "
        "ripaga in pochi anni e genera risparmio per i successivi venti."
    ),
    cta_primary_label="Vedi quanto risparmiate",
    variant_label="B",
    generated_by="seed",
)


# ── Haiku prompt ──────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "Sei un esperto di copywriting B2B per email commerciali nel settore "
    "fotovoltaico industriale italiano. Scrivi copy conciso, diretto e "
    "orientato alla conversione. Non usare emoji. Non usare jargon tecnico "
    "non necessario. Rispondi sempre con JSON valido, nessun testo extra."
)

_USER_PROMPT_TEMPLATE = """
Genera due varianti (A e B) di copy per un'email B2B outreach fotovoltaico.

CONTESTO TENANT:
- Azienda installatore: {tenant_name}
- Settore operativo: fotovoltaico B2B industriale

SEGMENTO TARGET:
- Cluster: {cluster_description}

{previous_winner_section}

ISTRUZIONI:
- Variante A (identificativo): tono professionale basato sul settore/ruolo del decisore. Cita il settore specifico.
- Variante B (economico-emotivo): tono che fa leva sul risparmio concreto in €, sul payback, sul "smettere di pagare bolletta". Usa numeri ipotetici plausibili se non disponibili.
- copy_subject: max 60 caratteri, oggetto email efficace
- copy_opening_line: 1-2 frasi, personalizzazione al cluster, nessun nome proprio
- copy_proposition_line: 1-2 frasi, valore dell'installatore / call to action implicita
- cta_primary_label: max 30 caratteri, testo bottone CTA principale

Rispondi SOLO con questo JSON (nessun markdown, nessun testo prima/dopo):
{{
  "variant_a": {{
    "copy_subject": "...",
    "copy_opening_line": "...",
    "copy_proposition_line": "...",
    "cta_primary_label": "..."
  }},
  "variant_b": {{
    "copy_subject": "...",
    "copy_opening_line": "...",
    "copy_proposition_line": "...",
    "cta_primary_label": "..."
  }}
}}
""".strip()

_CHALLENGER_SECTION = """
ROUND PRECEDENTE — IL VINCITORE È STATO:
- copy_subject: {copy_subject}
- copy_opening_line: {copy_opening_line}
- copy_proposition_line: {copy_proposition_line}
- cta_primary_label: {cta_primary_label}

Per questo nuovo round:
- Variante A = usa il copy vincitore come baseline (puoi affinarlo leggermente)
- Variante B = sfidante che cambia UNA sola dimensione (solo CTA, o solo opening, o solo proposition)
"""


def _build_prompt(
    tenant_name: str,
    cluster_signature: str,
    previous_winner: dict[str, str] | None,
) -> str:
    cluster_description = describe_cluster(cluster_signature)
    previous_section = ""
    if previous_winner:
        previous_section = _CHALLENGER_SECTION.format(
            copy_subject=previous_winner.get("copy_subject", ""),
            copy_opening_line=previous_winner.get("copy_opening_line", ""),
            copy_proposition_line=previous_winner.get("copy_proposition_line", ""),
            cta_primary_label=previous_winner.get("cta_primary_label", ""),
        )
    return _USER_PROMPT_TEMPLATE.format(
        tenant_name=tenant_name,
        cluster_description=cluster_description,
        previous_winner_section=previous_section,
    )


def _parse_haiku_response(text: str) -> tuple[dict, dict] | None:
    """Extract variant_a / variant_b dicts from Haiku response.

    Strips markdown code-fence wrappers if present, then parses JSON.
    Returns None on any parse failure.
    """
    # Remove ```json ... ``` wrappers if present.
    stripped = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
    try:
        data = json.loads(stripped)
        va = data["variant_a"]
        vb = data["variant_b"]
        required = {"copy_subject", "copy_opening_line", "copy_proposition_line", "cta_primary_label"}
        if not (required.issubset(va) and required.issubset(vb)):
            return None
        return va, vb
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _spec_from_dict(d: dict[str, Any], label: str) -> VariantSpec:
    return VariantSpec(
        copy_subject=str(d["copy_subject"])[:60],
        copy_opening_line=str(d["copy_opening_line"]),
        copy_proposition_line=str(d["copy_proposition_line"]),
        cta_primary_label=str(d["cta_primary_label"])[:30],
        variant_label=label,
        generated_by="haiku",
    )


async def generate_variant_pair(
    tenant_name: str,
    cluster_signature: str,
    round_number: int,
    previous_winner: dict[str, str] | None = None,
) -> tuple[VariantSpec, VariantSpec]:
    """Call Claude Haiku to generate A+B copy for this cluster round.

    Falls back to deterministic seed copy on any error so the send
    pipeline is never blocked.

    Args:
        tenant_name: The installer's business name (for personalisation).
        cluster_signature: e.g. "ateco41_m_ceo"
        round_number: Current round (for logging / tracing).
        previous_winner: Dict with the 4 copy fields of the previous
            round winner (None for round 1).

    Returns:
        (variant_a, variant_b) as VariantSpec instances.
    """
    if not settings.anthropic_api_key:
        log.warning(
            "variant_generator: ANTHROPIC_API_KEY not set — returning seed copy",
            cluster=cluster_signature,
            round=round_number,
        )
        seed_a = VariantSpec(**{**_SEED_VARIANT_A.__dict__, "variant_label": "A"})
        seed_b = VariantSpec(**{**_SEED_VARIANT_B.__dict__, "variant_label": "B"})
        return seed_a, seed_b

    try:
        import anthropic  # local import to avoid hard dep at module load

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = _build_prompt(tenant_name, cluster_signature, previous_winner)

        message = await client.messages.create(
            model=settings.anthropic_haiku_model,
            max_tokens=600,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text if message.content else ""
        parsed = _parse_haiku_response(text)

        if parsed is None:
            log.warning(
                "variant_generator: Haiku returned unparseable output — using seed",
                cluster=cluster_signature,
                round=round_number,
                raw=text[:200],
            )
            seed_a = VariantSpec(**{**_SEED_VARIANT_A.__dict__, "variant_label": "A"})
            seed_b = VariantSpec(**{**_SEED_VARIANT_B.__dict__, "variant_label": "B"})
            return seed_a, seed_b

        va_dict, vb_dict = parsed
        return _spec_from_dict(va_dict, "A"), _spec_from_dict(vb_dict, "B")

    except Exception as exc:  # noqa: BLE001
        log.error(
            "variant_generator: Haiku call failed — using seed copy",
            cluster=cluster_signature,
            round=round_number,
            error=str(exc),
        )
        seed_a = VariantSpec(**{**_SEED_VARIANT_A.__dict__, "variant_label": "A"})
        seed_b = VariantSpec(**{**_SEED_VARIANT_B.__dict__, "variant_label": "B"})
        return seed_a, seed_b


# ---------------------------------------------------------------------------
# Template rewrite generator (Phase 4 — generic_outreach AI variants)
# ---------------------------------------------------------------------------

_TEMPLATE_SYSTEM_PROMPT = (
    "Sei un esperto copywriter B2B per email outreach commerciali in italiano. "
    "Ricevi un template HTML esistente e ne generi N varianti riscritte. "
    "DEVI mantenere TUTTE le variabili Jinja2 originali (`{{ variabile }}`) "
    "intatte e nello stesso punto logico — non rimuoverle, non rinominarle, "
    "non aggiungerne di nuove. DEVI mantenere la struttura HTML (tag, "
    "stili inline, attributi href). Cambia solo il testo visibile e "
    "l'oggetto. Non usare emoji. Non usare jargon non necessario. "
    "Rispondi SOLO con JSON valido, nessun testo extra."
)

_TEMPLATE_USER_PROMPT = """
Riscrivi il seguente template email producendo {n} varianti alternative.
Ogni variante deve mantenere lo stesso significato di fondo ma con un
"angolo" diverso (es. urgenza, autorevolezza, focus risparmio €, focus
consulenza tecnica, focus case study). Mantieni TUTTE le variabili
{{ greeting_name }}, {{ business_name }}, {{ unsubscribe_url }}, ecc.
nello stesso ordine logico — non eliminarle.

TEMPLATE ATTUALE:
- Nome interno: {name}
- Oggetto: {subject}
- HTML body:
---HTML-START---
{html}
---HTML-END---

ISTRUZIONI:
- Per ogni variante: produci `subject` (max 80 caratteri), `html` (riscritto, stessa struttura), e `angle` (1 frase italiana che descrive l'angolo, es. "Focus su urgenza di sostituzione contatori").
- Le varianti devono essere distinte fra loro per tono e angolo, non solo per parole sinonime.
- {gdpr_hint}

Rispondi SOLO con questo JSON (nessun markdown, nessun testo prima/dopo):
{{
  "variants": [
    {{ "subject": "...", "html": "...", "angle": "..." }},
    {{ "subject": "...", "html": "...", "angle": "..." }}
  ]
}}
""".strip()


@dataclass(slots=True)
class TemplateRewrite:
    subject: str
    html: str
    angle: str


async def generate_template_rewrite(
    *,
    name: str,
    subject: str,
    html: str,
    n_variants: int = 2,
    gdpr_required_vars: list[str] | None = None,
) -> list[TemplateRewrite]:
    """Ask Haiku to rewrite an existing template into N alternatives.

    Used by `POST /v1/email-templates/{id}/generate-variants` to give the
    operator AI-suggested rewrites. Variants are NOT persisted — the
    caller decides which (if any) to save.

    On any failure (no API key, parse error, network issue) returns an
    empty list. The caller surfaces this to the UI as "Haiku non
    disponibile, riprova".
    """
    if not settings.anthropic_api_key:
        log.warning("template_rewrite.no_api_key")
        return []

    n = max(1, min(int(n_variants), 4))  # cap at 4 to control token spend
    gdpr_hint = ""
    if gdpr_required_vars:
        gdpr_hint = (
            "Le variabili GDPR obbligatorie "
            f"{', '.join('{{ ' + v + ' }}' for v in gdpr_required_vars)} "
            "DEVONO comparire in tutte le varianti (di solito nel footer). "
            "Non rimuoverle per nessun motivo."
        )

    prompt = _TEMPLATE_USER_PROMPT.format(
        n=n,
        name=name,
        subject=subject,
        html=html,
        gdpr_hint=gdpr_hint,
    )

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        message = await client.messages.create(
            model=settings.anthropic_haiku_model,
            max_tokens=4000,  # HTML can be long
            system=_TEMPLATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text if message.content else ""
        # Strip markdown code-fence wrappers.
        stripped = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
        data = json.loads(stripped)
        items = data.get("variants") or []

        out: list[TemplateRewrite] = []
        for v in items[:n]:
            sub = str(v.get("subject", "")).strip()
            body = str(v.get("html", "")).strip()
            angle = str(v.get("angle", "")).strip()
            if not sub or not body:
                continue
            out.append(TemplateRewrite(subject=sub[:500], html=body, angle=angle[:200]))
        log.info(
            "template_rewrite.haiku_ok",
            requested=n,
            returned=len(out),
        )
        return out
    except json.JSONDecodeError as exc:
        log.warning("template_rewrite.parse_failed", err=str(exc)[:200])
        return []
    except Exception as exc:  # noqa: BLE001
        log.error("template_rewrite.haiku_failed", err=str(exc)[:200])
        return []


async def persist_variant_pair(
    supabase: Any,
    tenant_id: str,
    cluster_signature: str,
    round_number: int,
    variant_a: VariantSpec,
    variant_b: VariantSpec,
) -> tuple[str, str]:
    """Insert A+B rows into cluster_copy_variants and return (id_a, id_b)."""
    rows = []
    for spec in (variant_a, variant_b):
        rows.append({
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "cluster_signature": cluster_signature,
            "round_number": round_number,
            "variant_label": spec.variant_label,
            "copy_subject": spec.copy_subject,
            "copy_opening_line": spec.copy_opening_line,
            "copy_proposition_line": spec.copy_proposition_line,
            "cta_primary_label": spec.cta_primary_label,
            "status": "active",
            "generated_by": spec.generated_by,
        })

    resp = await supabase.table("cluster_copy_variants").insert(rows).execute()
    if resp.error:
        raise RuntimeError(f"persist_variant_pair: {resp.error.message}")

    inserted = resp.data or []
    id_a = next((r["id"] for r in inserted if r["variant_label"] == "A"), rows[0]["id"])
    id_b = next((r["id"] for r in inserted if r["variant_label"] == "B"), rows[1]["id"])
    return id_a, id_b
