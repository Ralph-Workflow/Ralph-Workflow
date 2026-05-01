"""Checkpoint compatibility tests for parallel work-unit state.

Protects against regressions when checkpoints written before parallel mode
existed are loaded into a pipeline that supports it, and vice versa.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus

if TYPE_CHECKING:
    from pathlib import Path


def _wu(unit_id: str, allowed: list[str] | None = None) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Task {unit_id}",
        allowed_directories=allowed or [f"src/{unit_id}"],
    )


class TestOldCheckpointCompatibility:
    def test_old_checkpoint_without_parallel_fields_loads_cleanly(
        self, tmp_path: Path
    ) -> None:
        """Legacy checkpoint JSON without work_units/worker_states loads with empty defaults."""
        state = PipelineState(
            phase="planning",
            work_units=(_wu("u1"), _wu("u2")),
            worker_states={"u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED)},
        )
        raw = state.model_dump(mode="json")
        del raw["work_units"]
        del raw["worker_states"]
        checkpoint_path = tmp_path / ".agent" / "checkpoint.json"
        checkpoint_path.parent.mkdir(parents=True)
        checkpoint_path.write_text(json.dumps(raw))

        loaded = ckpt.load(checkpoint_path)

        assert loaded is not None
        assert loaded.work_units == ()
        assert loaded.worker_states == {}

    def test_work_units_round_trip_preserves_order_and_dependencies(
        self, tmp_path: Path
    ) -> None:
        """work_units tuple order and dependencies survive a save/load cycle."""
        unit_a = _wu("task-a")
        unit_b = WorkUnit(
            unit_id="task-b",
            description="Task B",
            allowed_directories=["src/b"],
            dependencies=["task-a"],
        )
        unit_c = WorkUnit(
            unit_id="task-c",
            description="Task C",
            allowed_directories=["src/c"],
            dependencies=["task-a"],
        )
        state = PipelineState(phase="planning", work_units=(unit_a, unit_b, unit_c))
        checkpoint_path = tmp_path / ".agent" / "checkpoint.json"
        checkpoint_path.parent.mkdir(parents=True)

        ckpt.save(state, checkpoint_path)
        loaded = ckpt.load(checkpoint_path)

        assert loaded is not None
        assert len(loaded.work_units) == len((unit_a, unit_b, unit_c))
        assert loaded.work_units[0].unit_id == "task-a"
        assert loaded.work_units[1].unit_id == "task-b"
        assert loaded.work_units[2].unit_id == "task-c"
        assert loaded.work_units[1].dependencies == ["task-a"]
        assert loaded.work_units[2].dependencies == ["task-a"]
        assert loaded.work_units[0].allowed_directories == ["src/task-a"]
        assert loaded.work_units[1].allowed_directories == ["src/b"]

    def test_copy_with_does_not_overwrite_set_work_units(self) -> None:
        """copy_with silently ignores work_units updates when already populated.

        work_units is set once during planning and must not be overwritten
        accidentally by subsequent copy_with calls in the pipeline.
        """
        original_units = (_wu("unit-x"), _wu("unit-y"))
        state = PipelineState(phase="planning", work_units=original_units)

        updated = state.copy_with(work_units=(_wu("unit-z"),))

        assert updated.work_units == original_units, (
            "copy_with must not overwrite an already-set work_units tuple"
        )

    def test_worker_state_status_round_trip(self, tmp_path: Path) -> None:
        """Worker statuses (SUCCEEDED, FAILED) survive JSON serialization."""
        state = PipelineState(
            phase="planning",
            work_units=(_wu("wa"), _wu("wb")),
            worker_states={
                "wa": WorkerState(unit_id="wa", status=WorkerStatus.SUCCEEDED),
                "wb": WorkerState(unit_id="wb", status=WorkerStatus.FAILED),
            },
        )
        checkpoint_path = tmp_path / ".agent" / "checkpoint.json"
        checkpoint_path.parent.mkdir(parents=True)

        ckpt.save(state, checkpoint_path)
        loaded = ckpt.load(checkpoint_path)

        assert loaded is not None
        assert loaded.worker_states["wa"].status == WorkerStatus.SUCCEEDED
        assert loaded.worker_states["wb"].status == WorkerStatus.FAILED

    @pytest.mark.parametrize(
        "status",
        [WorkerStatus.PENDING, WorkerStatus.RUNNING, WorkerStatus.SUCCEEDED, WorkerStatus.FAILED],
    )
    def test_all_worker_statuses_round_trip(self, tmp_path: Path, status: WorkerStatus) -> None:
        """Every WorkerStatus value survives a checkpoint save/load cycle."""
        state = PipelineState(
            phase="planning",
            work_units=(_wu("w1"),),
            worker_states={"w1": WorkerState(unit_id="w1", status=status)},
        )
        checkpoint_path = tmp_path / ".agent" / "checkpoint.json"
        checkpoint_path.parent.mkdir(parents=True)

        ckpt.save(state, checkpoint_path)
        loaded = ckpt.load(checkpoint_path)

        assert loaded is not None
        assert loaded.worker_states["w1"].status == status
