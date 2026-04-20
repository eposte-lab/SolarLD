"""Retry-loop tests for create_pv_rendering.

The A40 shared queue on Replicate occasionally OOMs on the first
attempt — we want the second try to land on a different worker.
These tests exercise the outer retry loop in
``create_pv_rendering`` without touching the network: we monkeypatch
``create_prediction`` / ``poll_prediction`` with scripted fakes.
"""

from __future__ import annotations

import pytest

from src.services import replicate_service
from src.services.replicate_service import (
    PredictionResult,
    RenderingPromptContext,
    ReplicateError,
    ReplicateTimeout,
    create_pv_rendering,
)


def _pred(
    *,
    status: str = "succeeded",
    url: str | None = "https://out.example/x.png",
    error: str | None = None,
    pid: str = "p1",
) -> PredictionResult:
    return PredictionResult(
        id=pid, status=status, output_url=url, error=error, logs=None
    )


@pytest.fixture
def ctx() -> RenderingPromptContext:
    return RenderingPromptContext(subject_type="b2c", area_sqm=80.0)


async def test_success_on_first_attempt_returns_immediately(
    monkeypatch: pytest.MonkeyPatch, ctx: RenderingPromptContext
) -> None:
    calls = {"create": 0, "poll": 0}

    async def fake_create(**_: object) -> PredictionResult:
        calls["create"] += 1
        return _pred(status="succeeded")

    async def fake_poll(_id: str, **__: object) -> PredictionResult:
        calls["poll"] += 1
        return _pred(status="succeeded")

    monkeypatch.setattr(replicate_service, "create_prediction", fake_create)
    monkeypatch.setattr(replicate_service, "poll_prediction", fake_poll)

    result = await create_pv_rendering(
        before_image_url="https://in.example/b.png",
        prompt_ctx=ctx,
    )
    assert result.is_success
    assert calls["create"] == 1
    assert calls["poll"] == 0  # cached synchronous result — no poll needed


async def test_retries_once_on_failed_status_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, ctx: RenderingPromptContext
) -> None:
    """First attempt ends `failed`; second succeeds → return the second."""
    sequence = iter(
        [
            _pred(status="failed", url=None, error="OOM on GPU"),
            _pred(status="succeeded"),
        ]
    )
    call_count = {"n": 0}

    async def fake_create(**_: object) -> PredictionResult:
        call_count["n"] += 1
        return next(sequence)

    async def fake_poll(_id: str, **__: object) -> PredictionResult:
        # Second attempt also short-circuits with synchronous success,
        # so poll shouldn't actually run — but keep a stub that would
        # raise to catch accidental invocations.
        raise AssertionError("poll_prediction should not be called")

    monkeypatch.setattr(replicate_service, "create_prediction", fake_create)
    monkeypatch.setattr(replicate_service, "poll_prediction", fake_poll)

    result = await create_pv_rendering(
        before_image_url="https://in.example/b.png",
        prompt_ctx=ctx,
        max_attempts=2,
    )
    assert result.is_success
    assert call_count["n"] == 2


async def test_retries_on_timeout_and_finally_gives_up(
    monkeypatch: pytest.MonkeyPatch, ctx: RenderingPromptContext
) -> None:
    async def fake_create(**_: object) -> PredictionResult:
        return _pred(status="processing", url=None)  # not done → polls

    async def fake_poll(_id: str, **__: object) -> PredictionResult:
        raise ReplicateTimeout("stuck processing")

    monkeypatch.setattr(replicate_service, "create_prediction", fake_create)
    monkeypatch.setattr(replicate_service, "poll_prediction", fake_poll)

    with pytest.raises(ReplicateTimeout, match="stuck processing"):
        await create_pv_rendering(
            before_image_url="https://in.example/b.png",
            prompt_ctx=ctx,
            max_attempts=2,
        )


async def test_raises_replicate_error_after_all_attempts_failed(
    monkeypatch: pytest.MonkeyPatch, ctx: RenderingPromptContext
) -> None:
    async def fake_create(**_: object) -> PredictionResult:
        return _pred(status="failed", url=None, error="NSFW")

    async def fake_poll(_id: str, **__: object) -> PredictionResult:
        raise AssertionError("poll should not run when create returns terminal")

    monkeypatch.setattr(replicate_service, "create_prediction", fake_create)
    monkeypatch.setattr(replicate_service, "poll_prediction", fake_poll)

    with pytest.raises(ReplicateError, match="NSFW"):
        await create_pv_rendering(
            before_image_url="https://in.example/b.png",
            prompt_ctx=ctx,
            max_attempts=2,
        )
