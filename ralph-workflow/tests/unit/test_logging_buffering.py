"""Tests for loguru buffering behavior in Ralph Workflow logging."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.logging import configure_logging
from ralph.logging_worker_sink import bind_worker_sink, remove_worker_sink

if TYPE_CHECKING:
    import pytest


def test_worker_sink_flushes_on_remove(tmp_path: Path) -> None:
    """Verify that a per-worker sink flushes on logger.remove()."""
    unit_id = "test-unit"
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    handle = bind_worker_sink(unit_id, log_dir, run_id="test-run")

    logger.bind(unit_id=unit_id).info("test message")

    assert handle.log_path.exists()

    remove_worker_sink(handle)

    content = handle.log_path.read_text()
    assert "test message" in content


def test_worker_sink_uses_block_buffering(tmp_path: Path) -> None:
    """Verify that a single small record does not hit disk immediately under block buffering."""
    unit_id = "test-unit-buffered"
    log_dir = tmp_path / "logs-buffered"
    log_dir.mkdir(parents=True, exist_ok=True)

    handle = bind_worker_sink(unit_id, log_dir, run_id="test-run-buffered")

    logger.bind(unit_id=unit_id).info("small record")

    size_before = handle.log_path.stat().st_size

    assert size_before == 0, "Block buffering should delay small records from hitting disk"

    remove_worker_sink(handle)

    size_after = handle.log_path.stat().st_size

    assert size_after > 0, (
        "After remove(), the buffer should be flushed and file should have content"
    )
    assert "small record" in handle.log_path.read_text()


def test_text_log_unbuffered_structured_block_buffered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-013 P1 liveness contract: the operator-facing text log MUST stay
    line-buffered (no ``buffering=`` kwarg) so ``tail -f`` and the live
    watchdog see records immediately; the structured JSONL sink and the
    per-worker sink MUST use ``buffering=8192`` so high-volume batch writes
    don't generate a per-line fsevent storm.

    Pins the RFC-013 P1 contract against a regression where the text log
    is unintentionally given a ``buffering=8192`` kwarg (which would
    silently delay operator-visible log lines by up to 8 KB of buffer
    pressure).
    """
    captured: list[dict[str, object]] = []
    real_add = logger.add

    def capturing_add(*args: object, **kwargs: object) -> int:
        record: dict[str, object] = {
            "args": list(args),
            "kwargs": dict(kwargs),
        }
        captured.append(record)
        return real_add(*args, **kwargs)

    monkeypatch.setattr(logger, "add", capturing_add)
    try:
        configure_logging(
            verbosity=2, log_directory=tmp_path, run_id="run-x", structured=True, rotation=None
        )
        logger.info("smoke line")
    finally:
        with contextlib.suppress(ValueError):
            logger.remove()

    text_log_calls = [
        c
        for c in captured
        if isinstance(c["args"], list)
        and len(c["args"]) > 0
        and isinstance(c["args"][0], (str, Path))
        and str(c["args"][0]).endswith("ralph.log")
    ]
    structured_calls = [
        c
        for c in captured
        if isinstance(c["args"], list)
        and len(c["args"]) > 0
        and isinstance(c["args"][0], (str, Path))
        and str(c["args"][0]).endswith("ralph.jsonl")
    ]

    assert text_log_calls, "expected a ralph.log (text log) sink to be added"
    assert structured_calls, "expected a ralph.jsonl (structured log) sink to be added"

    for call in text_log_calls:
        assert "buffering" not in call["kwargs"], (
            f"operator text log MUST NOT use block buffering; tail -f would "
            f"show stale lines. Got kwargs: {call['kwargs']!r}"
        )

    for call in structured_calls:
        assert call["kwargs"].get("buffering") == 8192, (
            f"structured JSONL log MUST use buffering=8192 to amortize "
            f"fsevents. Got kwargs: {call['kwargs']!r}"
        )
