"""Practice document extraction — Claude Vision over customer-supplied PDFs/images.

Livello 2 Sprint 4.

The installer drag-drops a customer document (visura camerale, carta
d'identità, visura catastale, recent bolletta showing the POD) onto
the practice detail page; this service runs Claude Vision over the
file and returns a structured payload the dashboard surfaces as
"applicable suggestions" next to MissingDataPanel.

Why one service per document kind (not a generic OCR):
  * each kind has a *very* specific Italian field set we know how to
    validate (POD format, codice fiscale checksum, foglio/particella
    look-up) — a generic prompt would lose precision
  * confidence thresholding is per-kind: a misread POD is much worse
    than a misread "altro" field, since the POD goes on every GSE form
  * apply targets differ: visura_cciaa fields go to *both* tenant
    (if owner uploads their own) and subject (if cliente's), with the
    routing decided in routes/practices.py at apply time, not here

Flow at runtime:
  1. routes upload endpoint stores the file → row in practice_uploads
     with extraction_status='pending'
  2. arq enqueues extract_practice_upload_task(upload_id)
  3. worker downloads bytes, calls extract_for_kind() → ExtractionResult
  4. row updated: extraction_status, extracted_data, confidence
  5. dashboard polls / re-fetches and shows "Applica" button

Costs (Sonnet 4.5 vision):
  ~1500 input + 300 output tokens per call → ~$0.009.
  Worst case 4 docs per practice → ≤4¢/practice. Negligible vs the
  15 minutes of installer time saved.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

UploadKind = Literal[
    "visura_cciaa",
    "visura_catastale",
    "documento_identita",
    "bolletta_pod",
    "altro",
]

# Single shared async client — Anthropic SDK handles connection
# pooling internally.
_client: AsyncAnthropic | None = None

# Hard cap below the Anthropic 5 MB image limit. PDFs are rasterised
# at 200 DPI which keeps the average page <1 MB.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Below this, the row is flagged manual_required so the UI prompts
# the operator to confirm before any "Applica suggerimenti" button
# becomes enabled.
MIN_CONFIDENCE = 0.60

ACCEPTED_IMAGE_MIMES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)


# ---------------------------------------------------------------------------
# Prompts — one per UploadKind
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Sei un assistente esperto di estrazione dati da documenti italiani. "
    "Ricevi l'immagine (o pagina di PDF) di un documento ufficiale italiano. "
    "Rispondi ESCLUSIVAMENTE con il JSON richiesto: niente prosa, niente code "
    "fences. Se un campo non è leggibile o assente, mettilo a null e abbassa "
    "la confidence. Non inventare mai valori."
)

# All prompts ask for the same envelope shape so callers can validate
# uniformly.  `fields` is the kind-specific payload; the rest is meta.
_ENVELOPE_FOOTER = """
Rispondi esattamente con questo JSON (nessun campo extra, nessun commento):
{
  "fields": { ... campi del documento ... },
  "confidence": number,   // 0.0–1.0, quanto sei sicuro dell'estrazione
  "notes": string         // breve nota se qualcosa è ambiguo (max 200 char)
}
""".strip()

_PROMPT_VISURA_CCIAA = (
    """\
Estrai dalla visura camerale (Camera di Commercio) i seguenti campi:

  - ragione_sociale: denominazione completa dell'azienda (es. "ACME Energy
    Srl")
  - forma_giuridica: "srl" | "spa" | "snc" | "sas" | "ditta_individuale" |
    "altro"
  - partita_iva: 11 cifre, solo numeri
  - codice_fiscale: 16 caratteri (per ditte individuali) o 11 cifre (per
    società); riporta esattamente come stampato
  - numero_cciaa: nel formato "XX-NNNNNNN" o "REA NNNNNNN PROVINCIA";
    estrai il numero REA e la sigla provincia se visibile
  - sede_legale_indirizzo: via + civico
  - sede_legale_cap: 5 cifre
  - sede_legale_citta: comune
  - sede_legale_provincia: 2 lettere (es. "MI", "RM")
  - codice_ateco: codice attività principale (es. "43.21.01")
  - legale_rappresentante_nome: nome del legale rappresentante / titolare
  - legale_rappresentante_cognome: cognome
  - legale_rappresentante_codice_fiscale: 16 caratteri se visibile

"""
    + _ENVELOPE_FOOTER
)

_PROMPT_VISURA_CATASTALE = (
    """\
Estrai dalla visura catastale i seguenti campi (immobile principale):

  - foglio: numero del foglio catastale (es. "127")
  - particella: numero della particella (es. "456")
  - subalterno: numero subalterno se presente (es. "3"); null se non c'è
  - comune: comune di ubicazione
  - provincia: 2 lettere
  - categoria_catastale: es. "A/2", "C/2", "D/7"; null se non visibile
  - rendita_catastale: numero in euro (solo cifra, senza simbolo)
  - intestatario_nome_cognome: nome e cognome dell'intestatario primario
    (o ragione sociale se persona giuridica)
  - intestatario_codice_fiscale: CF intestatario primario
  - quota_possesso: es. "1/1", "1/2"; null se non visibile

Se ci sono più immobili, riporta SOLO il primo (quello con quota piena).

"""
    + _ENVELOPE_FOOTER
)

_PROMPT_DOCUMENTO_IDENTITA = (
    """\
Estrai dal documento d'identità (carta d'identità o patente) i seguenti campi:

  - tipo_documento: "carta_identita" | "patente" | "passaporto" | "altro"
  - numero_documento: alfanumerico stampato sul documento
  - nome: nome di battesimo
  - cognome: cognome
  - codice_fiscale: 16 caratteri se presente sul documento (alcune CIE
    cartacee non lo hanno → null)
  - data_nascita: in formato YYYY-MM-DD
  - luogo_nascita: comune (o stato per nati all'estero)
  - residenza_indirizzo: via + civico
  - residenza_cap: 5 cifre
  - residenza_citta: comune
  - residenza_provincia: 2 lettere
  - data_rilascio: YYYY-MM-DD
  - data_scadenza: YYYY-MM-DD

"""
    + _ENVELOPE_FOOTER
)

_PROMPT_BOLLETTA_POD = (
    """\
Estrai da questa bolletta elettrica i campi anagrafici utili a una pratica
GSE (NON i consumi — quelli si leggono da un altro flusso):

  - pod: codice POD nel formato "IT001E12345678"; sempre 14 caratteri
    alfanumerici
  - distributore: nome del distributore di rete (NON il venditore!).
    Cerca dicitura tipo "Distributore: E-Distribuzione" / "Areti" /
    "Unareti" / "Ireti". Mappa a: "e_distribuzione" | "areti" |
    "unareti" | "ireti" | "altro"
  - tensione_alimentazione: "BT" (bassa tensione) | "MT" | "AT"
  - potenza_disponibile_kw: potenza disponibile contrattuale in kW
    (es. 3.0, 4.5, 6.0)
  - potenza_impegnata_kw: potenza impegnata in kW (può coincidere con
    quella disponibile)
  - intestatario_nome: nome intestatario fornitura
  - intestatario_cognome: cognome (o ragione sociale se persona
    giuridica → metti tutto in nome e lascia cognome=null)
  - intestatario_codice_fiscale: CF/PIVA intestatario
  - indirizzo_fornitura_via: indirizzo del punto di prelievo
  - indirizzo_fornitura_cap: 5 cifre
  - indirizzo_fornitura_citta: comune
  - indirizzo_fornitura_provincia: 2 lettere

"""
    + _ENVELOPE_FOOTER
)

_PROMPT_ALTRO = (
    """\
Documento generico: identifica il tipo se possibile, e estrai i dati
anagrafici principali se presenti:

  - tipo_documento_rilevato: descrizione breve (es. "contratto",
    "scheda tecnica modulo", "verbale assemblea")
  - intestatario_nome_cognome: se applicabile
  - codici_identificativi: lista di stringhe con codici significativi
    trovati (P.IVA, CF, POD, REA, etc.)
  - note: descrizione del contenuto in 1-2 frasi italiane

"""
    + _ENVELOPE_FOOTER
)

PROMPTS: dict[str, str] = {
    "visura_cciaa": _PROMPT_VISURA_CCIAA,
    "visura_catastale": _PROMPT_VISURA_CATASTALE,
    "documento_identita": _PROMPT_DOCUMENTO_IDENTITA,
    "bolletta_pod": _PROMPT_BOLLETTA_POD,
    "altro": _PROMPT_ALTRO,
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """Outcome of one Claude Vision run.

    `manual_required=True` means we got a structured response but the
    confidence is below MIN_CONFIDENCE — the dashboard still shows the
    extracted values but disables the "Applica" button until the
    operator marks them as reviewed.
    """

    success: bool
    upload_kind: str
    fields: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    notes: str | None = None
    error: str | None = None
    manual_required: bool = False
    raw_response: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# PDF rasterisation helper
# ---------------------------------------------------------------------------


def rasterise_pdf_first_page(pdf_bytes: bytes, *, dpi: int = 200) -> bytes:
    """Convert the first page of a PDF to PNG bytes.

    Uses pypdfium2 (pure-Python wheel built on PDFium) so we don't
    pull in a poppler / system-lib dependency.  Returns PNG bytes
    suitable for Claude Vision (base64-encoded by the caller).

    Raises RuntimeError if the PDF is unreadable or has zero pages —
    the caller logs and returns ExtractionResult(success=False).
    """
    try:
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError("pypdfium2 not installed") from e

    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception as e:
        raise RuntimeError(f"unreadable_pdf: {e}") from e

    if len(pdf) == 0:
        raise RuntimeError("empty_pdf")

    page = pdf[0]
    # scale 1.0 ≈ 72 DPI; multiply for higher fidelity. 200 DPI is the
    # sweet spot — text stays crisp, images stay <1 MB even on A4.
    bitmap = page.render(scale=dpi / 72.0)
    pil_img = bitmap.to_pil()

    buf = io.BytesIO()
    pil_img.save(buf, format="PNG", optimize=True)
    pdf.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(min=1, max=8),
    reraise=True,
)
async def extract_for_kind(
    file_bytes: bytes,
    mime_type: str,
    upload_kind: str,
    *,
    model: str | None = None,
) -> ExtractionResult:
    """Run Claude Vision on the supplied bytes for one document kind.

    Never raises on parse failure — returns ExtractionResult with the
    error.  Raises only on infrastructure issues the caller can
    surface (no API key, network outage after retries).
    """
    if upload_kind not in PROMPTS:
        return ExtractionResult(
            success=False,
            upload_kind=upload_kind,
            error=f"unknown_kind:{upload_kind}",
        )

    if not file_bytes:
        return ExtractionResult(
            success=False, upload_kind=upload_kind, error="empty_file"
        )

    # Branch on MIME: PDF → rasterise first page, image → use as-is.
    if mime_type == "application/pdf":
        try:
            file_bytes = rasterise_pdf_first_page(file_bytes)
            mime_type = "image/png"
        except RuntimeError as e:
            return ExtractionResult(
                success=False,
                upload_kind=upload_kind,
                error=f"pdf_rasterise_failed:{e}",
            )

    if mime_type not in ACCEPTED_IMAGE_MIMES:
        return ExtractionResult(
            success=False,
            upload_kind=upload_kind,
            error=f"unsupported_mime:{mime_type}",
        )

    if len(file_bytes) > _MAX_IMAGE_BYTES:
        return ExtractionResult(
            success=False,
            upload_kind=upload_kind,
            error=f"image_too_large:{len(file_bytes)}b",
        )

    client = _get_client()
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")

    msg = await client.messages.create(
        model=model or settings.anthropic_model,
        max_tokens=1024,
        temperature=0.0,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": PROMPTS[upload_kind]},
                ],
            }
        ],
    )

    text = ""
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            text = block.text  # type: ignore[attr-defined]
            break

    parsed = _parse_envelope(text)
    if parsed is None:
        log.warning(
            "practice.extraction.parse_failed",
            upload_kind=upload_kind,
            raw=text[:400],
        )
        return ExtractionResult(
            success=False,
            upload_kind=upload_kind,
            error="parse_failed",
            raw_response={"text": text[:2000]},
        )

    confidence = float(parsed.get("confidence", 0.0))
    fields = parsed.get("fields") or {}
    notes = str(parsed.get("notes") or "")[:200] or None

    return ExtractionResult(
        success=True,
        upload_kind=upload_kind,
        fields=_clean_fields(fields),
        confidence=round(confidence, 2),
        notes=notes,
        manual_required=confidence < MIN_CONFIDENCE,
        raw_response=parsed,
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_envelope(text: str) -> dict[str, Any] | None:
    """Parse the {fields, confidence, notes} envelope from Claude.

    Tolerates accidental code fences. Returns None on JSONDecode error
    or if required keys are missing.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip().rstrip("`").strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if "fields" not in data or "confidence" not in data:
        return None
    if not isinstance(data["fields"], dict):
        return None

    try:
        conf = float(data["confidence"])
    except (TypeError, ValueError):
        return None
    if not (0.0 <= conf <= 1.0):
        return None

    return data


def _clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Strip empties + normalise common Italian field formats.

    * trim whitespace
    * replace empty strings with None
    * uppercase province codes
    * uppercase POD codes
    * strip dots / spaces from P.IVA / CF (but keep CF-letters cased)
    """
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if v is None:
            out[k] = None
            continue
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                out[k] = None
                continue
            if k.endswith("_provincia"):
                v = v.upper()[:2]
            elif k == "pod":
                v = v.upper().replace(" ", "")
            elif k == "partita_iva":
                v = "".join(c for c in v if c.isdigit())
            elif k == "codice_fiscale" or k.endswith("_codice_fiscale"):
                v = v.upper().replace(" ", "")
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Apply-targets routing
# ---------------------------------------------------------------------------
#
# When the operator clicks "Applica suggerimenti", these maps decide
# where each extracted field is written:
#   • tenant   → PATCH /v1/tenants/me      (installer's own visura)
#   • subject  → PATCH /v1/subjects/{id}   (cliente's visura/CI)
#   • practice → PATCH /v1/practices/{id}  (POD, distributore, catastali)
#
# routes/practices.py reads these maps and builds the actual update
# bodies — the service stays pure (no DB writes) so it's easy to test.


# Mapping for visura_cciaa fields → which target table & column.
# The route knows whether the visura belongs to the tenant (operator
# self-onboarding) or to the cliente (most common) and picks the
# subject_target_map vs tenant_target_map accordingly.
VISURA_CCIAA_TENANT_TARGETS: dict[str, str] = {
    "ragione_sociale": "business_name",
    "partita_iva": "vat_number",
    "codice_fiscale": "codice_fiscale",
    "numero_cciaa": "numero_cciaa",
    "sede_legale_indirizzo": "legal_address",
}

VISURA_CCIAA_SUBJECT_TARGETS: dict[str, str] = {
    "ragione_sociale": "business_name",
    "partita_iva": "vat_number",
    "codice_fiscale": "codice_fiscale",
    "sede_legale_indirizzo": "legal_address",
    "sede_legale_citta": "legal_city",
    "sede_legale_provincia": "legal_province",
    "sede_legale_cap": "legal_cap",
    "codice_ateco": "ateco",
    "legale_rappresentante_nome": "owner_first_name",
    "legale_rappresentante_cognome": "owner_last_name",
    "legale_rappresentante_codice_fiscale": "owner_codice_fiscale",
}

VISURA_CATASTALE_PRACTICE_TARGETS: dict[str, str] = {
    "foglio": "catastale_foglio",
    "particella": "catastale_particella",
    "subalterno": "catastale_subalterno",
}

# Catastale extras land in practice.extras under a stable namespace so
# we don't lose data the schema doesn't have a column for.
VISURA_CATASTALE_EXTRAS_TARGETS: dict[str, str] = {
    "comune": "catastale_comune",
    "provincia": "catastale_provincia",
    "categoria_catastale": "catastale_categoria",
    "rendita_catastale": "catastale_rendita",
    "intestatario_nome_cognome": "catastale_intestatario",
    "intestatario_codice_fiscale": "catastale_intestatario_cf",
    "quota_possesso": "catastale_quota",
}

DOCUMENTO_IDENTITA_SUBJECT_TARGETS: dict[str, str] = {
    "nome": "owner_first_name",
    "cognome": "owner_last_name",
    "codice_fiscale": "owner_codice_fiscale",
    "data_nascita": "owner_birth_date",
    "luogo_nascita": "owner_birth_place",
    "residenza_indirizzo": "residence_address",
    "residenza_cap": "residence_cap",
    "residenza_citta": "residence_city",
    "residenza_provincia": "residence_province",
}

BOLLETTA_POD_PRACTICE_TARGETS: dict[str, str] = {
    "pod": "impianto_pod",
    "distributore": "impianto_distributore",
}

BOLLETTA_POD_EXTRAS_TARGETS: dict[str, str] = {
    "tensione_alimentazione": "bolletta_tensione",
    "potenza_disponibile_kw": "bolletta_potenza_disponibile",
    "potenza_impegnata_kw": "bolletta_potenza_impegnata",
    "indirizzo_fornitura_via": "bolletta_indirizzo",
    "indirizzo_fornitura_cap": "bolletta_cap",
    "indirizzo_fornitura_citta": "bolletta_citta",
    "indirizzo_fornitura_provincia": "bolletta_provincia",
}


def build_apply_payload(
    upload_kind: str,
    fields: dict[str, Any],
    *,
    visura_target: Literal["tenant", "subject"] = "subject",
) -> dict[str, dict[str, Any]]:
    """Translate extracted fields into per-target update dicts.

    Returns a dict shaped:
      {
        "tenant":   { ... fields for PATCH /v1/tenants/me ... },
        "subject":  { ... fields for PATCH /v1/subjects/{id} ... },
        "practice": { ... fields for PATCH /v1/practices/{id} ... },
        "extras":   { ... merged into practice.extras ... },
      }
    Empty sections are omitted.
    """
    tenant: dict[str, Any] = {}
    subject: dict[str, Any] = {}
    practice: dict[str, Any] = {}
    extras: dict[str, Any] = {}

    def _copy(src_map: dict[str, str], target: dict[str, Any]) -> None:
        for src, dst in src_map.items():
            v = fields.get(src)
            if v is None or v == "":
                continue
            target[dst] = v

    if upload_kind == "visura_cciaa":
        if visura_target == "tenant":
            _copy(VISURA_CCIAA_TENANT_TARGETS, tenant)
        else:
            _copy(VISURA_CCIAA_SUBJECT_TARGETS, subject)
    elif upload_kind == "visura_catastale":
        _copy(VISURA_CATASTALE_PRACTICE_TARGETS, practice)
        _copy(VISURA_CATASTALE_EXTRAS_TARGETS, extras)
    elif upload_kind == "documento_identita":
        _copy(DOCUMENTO_IDENTITA_SUBJECT_TARGETS, subject)
    elif upload_kind == "bolletta_pod":
        _copy(BOLLETTA_POD_PRACTICE_TARGETS, practice)
        _copy(BOLLETTA_POD_EXTRAS_TARGETS, extras)
    # 'altro' has no automatic targets — operator must transcribe.

    out: dict[str, dict[str, Any]] = {}
    if tenant:
        out["tenant"] = tenant
    if subject:
        out["subject"] = subject
    if practice:
        out["practice"] = practice
    if extras:
        out["extras"] = extras
    return out
