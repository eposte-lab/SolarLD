"""Thin wrapper around the Anthropic Claude SDK.

Centralizes model selection, retries, structured-output parsing.
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    reraise=True,
)
async def complete(
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.4,
    model: str | None = None,
) -> str:
    """Simple text completion with retries."""
    client = get_client()
    msg = await client.messages.create(
        model=model or settings.anthropic_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system or "",
        messages=[{"role": "user", "content": prompt}],
    )
    # Block union: extract first text block
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            return block.text  # type: ignore[attr-defined]
    return ""


async def complete_json(
    prompt: str,
    *,
    schema_hint: str,
    system: str | None = None,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """Request a JSON-only response and parse it. Best-effort."""
    import json

    full_system = (
        (system or "")
        + "\n\nYou MUST respond with a single JSON object matching this schema:\n"
        + schema_hint
        + "\nNo prose, no code fences, just raw JSON."
    )
    text = await complete(
        prompt, system=full_system, max_tokens=max_tokens, temperature=0.0
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("claude_json_parse_failed", raw=text[:500])
        return {}
