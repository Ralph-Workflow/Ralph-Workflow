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
    reset_spawn_id()
    record(tmp_path, "env_loaded", timeout_s=5.0)
    assert read_last_stage(tmp_path) == "env_loaded"

    record(tmp_path, "session_ready", timeout_s=5.0)
    assert read_last_stage(tmp_path) == "session_ready"


def test_record_with_zero_or_negative_timeout_raises_timeout_error(tmp_path: Path) -> None:
    with pytest.raises(TimeoutError, match="budget.*<= 0"):
        record(tmp_path, "env_loaded", timeout_s=0.0)

    with pytest.raises(TimeoutError, match="budget.*<= 0"):
        record(tmp_path, "env_loaded", timeout_s=-0.5)


def test_record_with_timeout_blocked_raises_timeout_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    def failing_do_record(root: Path, stage: str) -> None:
        raise OSError("Simulated disk I/O error")

    monkeypatch.setattr(stage_tracker_mod, "_do_record", failing_do_record)

    with pytest.raises(OSError, match="Simulated disk I/O error"):
        record(tmp_path, "env_loaded", timeout_s=1.0)
