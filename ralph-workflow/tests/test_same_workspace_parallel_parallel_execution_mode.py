"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

from ralph.pipeline.parallel.mode import ParallelExecutionMode
from ralph.pipeline.work_units import (
    WorkUnit,
)


def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


class TestParallelExecutionMode:
    def test_same_workspace_is_only_supported_mode(self) -> None:
        modes = list(ParallelExecutionMode)
        assert len(modes) == 1
        assert ParallelExecutionMode.SAME_WORKSPACE in modes

    def test_same_workspace_string_value(self) -> None:
        assert str(ParallelExecutionMode.SAME_WORKSPACE) == "same_workspace"
