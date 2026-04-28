"""Bolletta OCR — Claude Vision over Italian utility bills.

Sprint 8 Fase B.2.

Italian utility bills (Enel, ENI Plenitude, A2A, Iren, Acea, Hera,
Sorgenia, ...) all share a similar header layout: total kWh consumed
in the period, total euro charged, billing period dates. The exact
labels differ ("Consumo annuo stimato" vs "Consumo del periodo" vs
"Energia utilizzata"), and many bills are scanned as PDF or photo.

We let Claude Vision do the heavy lifting:
  * one model call per upload
  * the prompt asks for a strict JSON envelope
  * we coerce/clamp the values, then return an :class:`OcrResult`
  * confidence below ``MIN_CONFIDENCE`` (0.55) is reported as success
    but flagged ``manual_required=True`` — the UI surfaces the
    extracted values for the user to confirm/correct.

Fallback path: when the API key is missing or the call fails, the
caller still inserts the upload row with ``ocr_error`` populated and
``source='upload_manual'`` — the user types the values themselves.

Pricing: Claude Sonnet ~$3/M input + $15/M output tokens. A typical
bolletta image is ~1500 input + 200 output → ~$0.008. Budget ≈ 1¢
per uploaded bill.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

_client: AsyncAnthropic | None = None

# Anthropic limits a single image to 5 MB raw bytes after base64
# decoding. We refuse anything bigger before the round-trip — the
# upload endpoint already caps at 10 MB, but PDF→image rasterizing
# could push us over.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Below this, we mark ``manual_required`` so the UI prompts the
# user to confirm the values. 0.55 is empirically the boundary
# where Claude Sonnet starts hallucinating bills it can't read.
MIN_CONFIDENCE = 0.55

OCR_PROVIDER_TAG = "claude-sonnet-vision"

SYSTEM_PROMPT = (
    "You are a careful Italian utility-bill data-extraction assistant. "
    "You receive an image (or scanned PDF page) of a luce/gas/elettricità "
    "bolletta from an Italian provider (Enel, ENI Plenitude, A2A, Iren, "
    "Acea, Hera, Sorgenia, Edison, etc.). Reply with EXACTLY the JSON "
    "object the user describes — no prose, no code fences. If a value "
    "is uncertain, lower the confidence; never invent values."
)

USER_PROMPT = """\
Extract the following fields from this Italian utility bill image:

  - kwh_yearly: total kWh consumed in the most-recent 12-month window
    visible on the bill (look for "Consumo annuo", "Energia consumata
    ultimi 12 mesi", or sum the periodi shown). If only a 1-2 month
    period is visible, ANNUALISE it (multiply by 12 / months_visible).

  - eur_yearly: total euro charged for that same 12-month window,
    INCLUSIVE of taxes (importo totale fattura × annualisation factor
    if needed).

  - billing_period_months: the number of months actually shown on this
    bill (1, 2, 3, 6, or 12 typically).

  - provider_name: e.g. "Enel Energia", "ENI Plenitude", "A2A", or
    "unknown".

  - confidence: your confidence the extraction is correct, 0.0 to 1.0.
    Below 0.5 means you can't reliably read the bill. Above 0.9 means
    the values are clearly printed and unambiguous.

Respond with EXACTLY this JSON (no extra keys, no commentary):
{
  "kwh_yearly": number,
  "eur_yearly": number,
  "billing_period_months": number,
  "provider_name": string,
  "confidence": number,
  "notes": string
}
"""


@dataclass
class OcrResult:
    """Outcome of an OCR run on one bolletta upload.

    ``raw_response`` is stored on ``bolletta_uploads.ocr_raw_response``
    so we can re-extract with a different model later without forcing
    the user to re-upload.
    """

    success: bool
    kwh_yearly: float | None = None
    eur_yearly: float | None = None
    billing_period_months: int | None = None
    provider_name: str | None = None
    confidence: float | None = None
    notes: str | None = None
    error: str | None = None
    manual_required: bool = False
    raw_response: dict[str, Any] = field(default_factory=dict)


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
async def extract_from_image(
    image_bytes: bytes,
    mime_type: str,
    *,
    model: str | None = None,
) -> OcrResult:
    """Run Claude Vision over a bolletta image and return the parsed values.

    Never raises on parse failures — returns ``OcrResult(success=False,
    error=...)`` instead, so the caller can persist the upload row
    with the error and surface a manual-entry form.

    Raises only on **infrastructure** failures the caller can
    meaningfully report (no API key configured, network outage after
    retries) — those bubble up so the upload endpoint can return 502.
    """
    if not image_bytes:
        return OcrResult(success=False, error="empty_image")

    if len(image_bytes) > _MAX_IMAGE_BYTES:
        return OcrResult(
            success=False,
            error=f"image_too_large_{len(image_bytes)}b",
        )

    # Anthropic Vision accepts: image/png|jpeg|gif|webp. PDFs are not
    # accepted by the messages API directly — the upload endpoint is
    # responsible for rasterising PDF pages to PNG before calling us.
    accepted = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if mime_type not in accepted:
        return OcrResult(
            success=False,
            error=f"unsupported_mime_{mime_type}",
        )

    client = _get_client()
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")

    msg = await client.messages.create(
        model=model or settings.anthropic_model,
        max_tokens=512,
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
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ],
    )

    text = ""
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            text = block.text  # type: ignore[attr-defined]
            break

    parsed = parse_ocr_response(text)
    if parsed is None:
        log.warning("bolletta.ocr.parse_failed", raw=text[:400])
        return OcrResult(
            success=False,
            error="parse_failed",
            raw_response={"text": text[:2000]},
        )

    confidence = float(parsed.get("confidence", 0.0))
    return OcrResult(
        success=True,
        kwh_yearly=float(parsed["kwh_yearly"]),
        eur_yearly=float(parsed["eur_yearly"]),
        billing_period_months=int(parsed["billing_period_months"]),
        provider_name=str(parsed.get("provider_name") or "unknown"),
        confidence=round(confidence, 2),
        notes=str(parsed.get("notes") or "")[:300] or None,
        manual_required=confidence < MIN_CONFIDENCE,
        raw_response=parsed,
    )


def parse_ocr_response(text: str) -> dict[str, Any] | None:
    """Parse a JSON envelope from Claude, tolerating accidental fences.

    Returns ``None`` if the response is not valid JSON, lacks the
    required keys, or contains values that fail the sanity-clamp.
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

    required = ("kwh_yearly", "eur_yearly", "billing_period_months", "confidence")
    if any(k not in data for k in required):
        return None

    try:
        kwh = float(data["kwh_yearly"])
        eur = float(data["eur_yearly"])
        months = int(data["billing_period_months"])
        conf = float(data["confidence"])
    except (TypeError, ValueError):
        return None

    # Clamp to plausible Italian-residential ranges. A villa uses
    # ~6000 kWh/yr and pays ~€2500; a small business ~30k kWh /
    # ~€8k. We refuse values that are clearly wrong (negative,
    # > 250k kWh, > 100k€) — Claude is told to lower confidence
    # when uncertain, but bad-faith inputs (a meme image instead
    # of a bill) can still slip a "10000000" through.
    if not (0 <= kwh <= 250_000):
        return None
    if not (0 <= eur <= 100_000):
        return None
    if not (1 <= months <= 12):
        return None
    if not (0.0 <= conf <= 1.0):
        return None

    data["kwh_yearly"] = round(kwh, 2)
    data["eur_yearly"] = round(eur, 2)
    data["billing_period_months"] = months
    data["confidence"] = round(conf, 3)
    return data
