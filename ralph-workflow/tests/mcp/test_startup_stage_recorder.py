"""Dedicated tests for bounded per-spawn stage recording in _start_stage_tracker.

Validates that stage label recording supports fail-closed bounding by timeout,
avoiding unbounded delays when the underlying filesystem or temp storage stalls.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.server._start_stage_tracker as stage_tracker_mod
from ralph.mcp.server._start_stage_tracker import (
    read_last_stage,
    record,
    reset_spawn_id,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_record_with_timeout_succeeds_promptly(tmp_path: Path) -> None:
    """When the filesystem responds normally, record with timeout succeeds and updates stage."""
    reset_spawn_id()
    record(tmp_path, "env_loaded", timeout_s=5.0)
    assert read_last_stage(tmp_path) == "env_loaded"

    record(tmp_path, "session_ready", timeout_s=5.0)
    assert read_last_stage(tmp_path) == "session_ready"


def test_record_with_zero_or_negative_timeout_raises_timeout_error(tmp_path: Path) -> None:
    """A zero or negative timeout_s raises TimeoutError immediately without attempting I/O."""
    with pytest.raises(TimeoutError, match="budget.*<= 0"):
        record(tmp_path, "env_loaded", timeout_s=0.0)

    with pytest.raises(TimeoutError, match="budget.*<= 0"):
        record(tmp_path, "env_loaded", timeout_s=-0.5)


def test_record_with_timeout_blocked_raises_timeout_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the underlying stage file write blocks, record with timeout_s raises TimeoutError."""
    stall_gate = threading.Event()

    def blocked_do_record(root: Path, stage: str) -> None:
        stall_gate.wait(timeout=10.0)

    monkeypatch.setattr(stage_tracker_mod, "_do_record", blocked_do_record)

    try:
        with pytest.raises(TimeoutError, match="stage recording timed out"):
            record(tmp_path, "env_loaded", timeout_s=0.05)
    finally:
        stall_gate.set()


def test_record_with_timeout_propagates_inner_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the underlying stage file write raises an unexpected OSError, it is re-raised."""

    def failing_do_record(root: Path, stage: str) -> None:
        raise OSError("Simulated disk I/O error")

    monkeypatch.setattr(stage_tracker_mod, "_do_record", failing_do_record)

    with pytest.raises(OSError, match="Simulated disk I/O error"):
        record(tmp_path, "env_loaded", timeout_s=1.0)
