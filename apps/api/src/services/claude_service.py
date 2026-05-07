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
    """Request a JSON-only response and parse it. Best-effort.

    Tolerates Claude returning the JSON wrapped in markdown code fences
    (``` or ```json) or with leading/trailing prose. Extracts the first
    JSON object found; returns {} only if nothing parseable exists.
    """

    full_system = (
        (system or "")
        + "\n\nYou MUST respond with a single JSON object matching this schema:\n"
        + schema_hint
        + "\nReturn ONLY the JSON object. No prose, no markdown code fences."
    )
    text = await complete(prompt, system=full_system, max_tokens=max_tokens, temperature=0.0)
    parsed = _parse_json_lenient(text)
    if parsed is None:
        log.warning("claude_json_parse_failed", raw=text[:500])
        return {}
    return parsed


def _parse_json_lenient(text: str) -> dict[str, Any] | None:
    """Strip code fences / leading prose and extract the first JSON object.

    Handles three real-world Claude failure modes we've hit:
      1. ```json\n{...}\n```  — markdown fences
      2. "Here is the JSON:\n\n{...}"  — preamble prose
      3. {...}\n\nNote: ...  — trailing commentary

    Returns the parsed dict, or None if no valid object is found.
    """
    import json
    import re

    if not text:
        return None
    stripped = text.strip()

    # 1. Markdown code fences.
    if stripped.startswith("```"):
        # Remove opening fence (with optional 'json' tag) and closing fence.
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```\s*$", "", stripped)
        stripped = stripped.strip()

    # 2. Direct parse first — most common path.
    try:
        result = json.loads(stripped)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 3. Find the first '{' and try to parse a balanced object from there.
    #    Handles preamble prose AND trailing commentary in one pass.
    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start : i + 1]
                try:
                    result = json.loads(candidate)
                    return result if isinstance(result, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
