"""R4 invariant: a child logs six per-stage flushed lines on startup."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.subprocess_e2e


import io
import logging
from pathlib import Path

from ralph.mcp.server._start_stage_tracker import (
    RUNTIME_STAGES,
    read_last_stage,
    record,
    stage_file_path,
)


@pytest.mark.timeout_seconds(5)
def test_six_runtime_stages_log_each_in_order(tmp_path: Path) -> None:
    logger = logging.getLogger("ralph.mcp.server.runtime.test_stages")
    logger.setLevel(logging.INFO)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        for stage in RUNTIME_STAGES:
            logger.info("ralph-mcp startup: stage=%s", stage)
            record(tmp_path, stage)
        output = stream.getvalue()
        for stage in RUNTIME_STAGES:
            assert f"stage={stage}" in output
        assert read_last_stage(tmp_path) == RUNTIME_STAGES[-1]
    finally:
        logger.removeHandler(handler)


@pytest.mark.timeout_seconds(5)
def test_stage_tracker_writes_atomic_and_replaceable(tmp_path: Path) -> None:
    for stage in RUNTIME_STAGES:
        record(tmp_path, stage)
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[-1]
    on_disk = stage_file_path(tmp_path).read_text(encoding="ascii")
    assert on_disk == RUNTIME_STAGES[-1]
    for prior_stage in RUNTIME_STAGES[:-1]:
        assert on_disk != prior_stage


@pytest.mark.timeout_seconds(5)
def test_stage_tracker_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_last_stage(tmp_path) is None


def test_closed_stage_vocabulary_is_six() -> None:
    assert len(RUNTIME_STAGES) == 6
    assert RUNTIME_STAGES[0] == "env_loaded"
    assert RUNTIME_STAGES[-1] == "ready_to_serve"
