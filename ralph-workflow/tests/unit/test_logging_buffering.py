"""Tests for loguru buffering behavior in Ralph Workflow logging."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ralph.logging_worker_sink import bind_worker_sink, remove_worker_sink


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
