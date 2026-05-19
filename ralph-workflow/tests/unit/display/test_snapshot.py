from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from datetime import UTC, datetime

import pytest

from ralph.display.snapshot import (
    PipelineSnapshot,
    SnapshotContext,
    WorkerSnapshot,
    snapshot_from_state,
)
from ralph.pipeline.state import PipelineState, RunMetrics
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus

ITERATION = 3
TOTAL_ITERATIONS = 10
TOTAL_REVIEWER_PASSES = 4
PUSH_COUNT = 2
TOTAL_AGENT_CALLS = 9
TOTAL_CONTINUATIONS = 8
TOTAL_FALLBACKS = 7
TOTAL_RETRIES = 6


def _make_state(*, worker_states: dict[str, WorkerState] | None = None) -> PipelineState:
    return PipelineState(
        phase="development",
        previous_phase="planning",
        outer_progress={"iteration": ITERATION, "reviewer_pass": 1},
        budget_caps={"iteration": TOTAL_ITERATIONS, "reviewer_pass": TOTAL_REVIEWER_PASSES},
        review_outcome="has_issues",
        interrupted_by_user=False,
        last_error="boom",
        pr_url="https://example.com/pr/123",
        push_count=PUSH_COUNT,
        metrics=RunMetrics(
            total_agent_calls=TOTAL_AGENT_CALLS,
            total_continuations=TOTAL_CONTINUATIONS,
            total_fallbacks=TOTAL_FALLBACKS,
            total_retries=TOTAL_RETRIES,
        ),
        worker_states=worker_states or {},
        work_units=(
            WorkUnit(unit_id="unit-1", description="first task"),
            WorkUnit(unit_id="unit-2", description="second task"),
            WorkUnit(unit_id="unit-3", description="third task"),
        ),
    )


def test_snapshot_from_state_projects_exact_fields_and_order() -> None:
    state = _make_state(
        worker_states={
            "unit-1": WorkerState(
                unit_id="unit-1",
                status=WorkerStatus.RUNNING,
                started_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
            ),
            "unit-2": WorkerState(
                unit_id="unit-2",
                status=WorkerStatus.SUCCEEDED,
                started_at=datetime(2026, 4, 18, 11, 0, tzinfo=UTC),
                finished_at=datetime(2026, 4, 18, 11, 45, tzinfo=UTC),
                exit_code=0,
            ),
            "unit-4": WorkerState(unit_id="unit-4", status=WorkerStatus.CANCELLED),
        },
    )

    snapshot = snapshot_from_state(
        state,
        SnapshotContext(
            prompt_path="PROMPT.md",
            prompt_preview=("# title",),
            run_id="run-123",
        ),
    )

    assert snapshot.phase == "development"
    assert snapshot.previous_phase == "planning"
    assert snapshot.budget_progress["iteration"].completed == ITERATION
    assert snapshot.budget_progress["iteration"].cap == TOTAL_ITERATIONS
    assert snapshot.budget_progress["reviewer_pass"].completed == 1
    assert snapshot.budget_progress["reviewer_pass"].cap == TOTAL_REVIEWER_PASSES
    assert snapshot.review_issues_found is True
    assert snapshot.interrupted_by_user is False
    assert snapshot.last_error == "boom"
    assert snapshot.pr_url == "https://example.com/pr/123"
    assert snapshot.push_count == PUSH_COUNT
    assert snapshot.total_agent_calls == TOTAL_AGENT_CALLS
    assert snapshot.total_continuations == TOTAL_CONTINUATIONS
    assert snapshot.total_fallbacks == TOTAL_FALLBACKS
    assert snapshot.total_retries == TOTAL_RETRIES
    assert snapshot.prompt_path == "PROMPT.md"
    assert snapshot.prompt_preview == ("# title",)
    assert snapshot.run_id == "run-123"
    assert isinstance(snapshot.workers, tuple)
    assert [worker.unit_id for worker in snapshot.workers] == [
        "unit-1",
        "unit-2",
        "unit-3",
        "unit-4",
    ]

    assert [worker.description for worker in snapshot.workers] == [
        "first task",
        "second task",
        "third task",
        "unit-4",
    ]

    assert [field.name for field in WorkerSnapshot.__dataclass_fields__.values()] == [
        "unit_id",
        "description",
        "status",
        "status_semantic",
        "started_at",
        "finished_at",
        "elapsed_s",
        "exit_code",
        "error_message",
        "dropped_lines",
    ]
    assert not hasattr(snapshot.workers[0], "worktree_path")
    assert not hasattr(snapshot.workers[0], "log_file")

    worker_1 = snapshot.workers[0]
    assert worker_1.status == "RUNNING"
    assert worker_1.status_semantic == "running"
    assert worker_1.started_at == datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
    assert worker_1.finished_at is None
    assert worker_1.exit_code is None
    assert worker_1.dropped_lines == 0

    worker_2 = snapshot.workers[1]
    assert worker_2.status == "SUCCEEDED"
    assert worker_2.status_semantic == "success"
    assert worker_2.exit_code == 0
    assert worker_2.finished_at == datetime(2026, 4, 18, 11, 45, tzinfo=UTC)
    assert worker_2.dropped_lines == 0

    worker_3 = snapshot.workers[2]
    assert worker_3.status == "PENDING"
    assert worker_3.status_semantic == "pending"
    assert worker_3.started_at is None
    assert worker_3.finished_at is None
    assert worker_3.elapsed_s == 0.0

    worker_4 = snapshot.workers[3]
    assert worker_4.status == "CANCELLED"
    assert worker_4.status_semantic == "skipped"
    assert worker_4.description == "unit-4"


def test_snapshot_from_state_maps_unknown_status_to_info() -> None:
    unknown = WorkerState.model_construct(unit_id="unit-1", status="MYSTERY")
    state = _make_state(worker_states={"unit-1": unknown})
    snapshot = snapshot_from_state(state)

    assert snapshot.workers[0].status_semantic == "info"
    assert snapshot.workers[0].status == "MYSTERY"


def test_snapshot_dataclasses_are_frozen_and_slotted() -> None:
    assert is_dataclass(WorkerSnapshot)
    assert is_dataclass(PipelineSnapshot)
    assert WorkerSnapshot.__dict__["__dataclass_params__"].frozen is True
    assert PipelineSnapshot.__dict__["__dataclass_params__"].frozen is True

    snapshot = PipelineSnapshot(
        phase="planning",
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=0,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path=None,
        prompt_preview=(),
        run_id=None,
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
    )

    with pytest.raises(FrozenInstanceError):
        snapshot.phase = "development"

    assert hasattr(WorkerSnapshot, "__slots__")
    assert hasattr(PipelineSnapshot, "__slots__")


def test_snapshot_field_names_match_plan_exactly() -> None:
    assert [field.name for field in WorkerSnapshot.__dataclass_fields__.values()] == [
        "unit_id",
        "description",
        "status",
        "status_semantic",
        "started_at",
        "finished_at",
        "elapsed_s",
        "exit_code",
        "error_message",
        "dropped_lines",
    ]


def test_snapshot_elapsed_seconds_cover_running_finished_and_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FixedDateTime:
        @staticmethod
        def now(tz: object = None) -> datetime:
            assert tz is UTC
            return datetime(2026, 4, 18, 12, 0, 10, tzinfo=UTC)

    monkeypatch.setattr("ralph.display.snapshot.datetime", _FixedDateTime)

    state = _make_state(
        worker_states={
            "running": WorkerState(
                unit_id="running",
                status=WorkerStatus.RUNNING,
                started_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
            ),
            "finished": WorkerState(
                unit_id="finished",
                status=WorkerStatus.SUCCEEDED,
                started_at=datetime(2026, 4, 18, 11, 59, tzinfo=UTC),
                finished_at=datetime(2026, 4, 18, 12, 0, 5, tzinfo=UTC),
            ),
            "pending": WorkerState(unit_id="pending", status=WorkerStatus.PENDING),
        },
    )

    snapshot = snapshot_from_state(state)

    elapsed = {worker.unit_id: worker.elapsed_s for worker in snapshot.workers}
    assert elapsed["running"] == pytest.approx(10.0)
    assert elapsed["finished"] == pytest.approx(65.0)
    assert elapsed["pending"] == 0.0
