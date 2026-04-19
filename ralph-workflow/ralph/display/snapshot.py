"""Immutable dashboard snapshot models.

This module projects pipeline state into a presentation-agnostic data shape
consumed by display panels and subscribers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.pipeline.worker_state import WorkerState, WorkerStatus

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState


_STATUS_TO_SEMANTIC: dict[str, str] = {
    "PENDING": "pending",
    "RUNNING": "running",
    "SUCCEEDED": "success",
    "FAILED": "error",
    "CANCELLED": "skipped",
}


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    """Immutable projection of a single worker's execution state."""

    unit_id: str
    description: str
    status: str
    status_semantic: str
    started_at: datetime | None
    finished_at: datetime | None
    elapsed_s: float
    exit_code: int | None
    commit_sha: str | None
    error_message: str | None
    dropped_lines: int = 0


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    """Immutable dashboard view of pipeline state."""

    phase: str
    previous_phase: str | None
    iteration: int
    total_iterations: int
    reviewer_pass: int
    total_reviewer_passes: int
    review_issues_found: bool
    interrupted_by_user: bool
    last_error: str | None
    pr_url: str | None
    push_count: int
    total_agent_calls: int
    total_continuations: int
    total_fallbacks: int
    total_retries: int
    workers: tuple[WorkerSnapshot, ...]
    prompt_path: str | None
    prompt_preview: tuple[str, ...]
    run_id: str | None
    created_at: datetime


def snapshot_from_state(
    state: PipelineState,
    *,
    prompt_path: str | None,
    prompt_preview: tuple[str, ...],
    run_id: str | None,
) -> DashboardSnapshot:
    """Project PipelineState into an immutable dashboard snapshot."""

    created_at = datetime.now(UTC)
    workers = _snapshot_workers(state)
    return DashboardSnapshot(
        phase=state.phase,
        previous_phase=state.previous_phase,
        iteration=state.iteration,
        total_iterations=state.total_iterations,
        reviewer_pass=state.reviewer_pass,
        total_reviewer_passes=state.total_reviewer_passes,
        review_issues_found=state.review_issues_found,
        interrupted_by_user=state.interrupted_by_user,
        last_error=state.last_error,
        pr_url=state.pr_url,
        push_count=state.push_count,
        total_agent_calls=state.metrics.total_agent_calls,
        total_continuations=state.metrics.total_continuations,
        total_fallbacks=state.metrics.total_fallbacks,
        total_retries=state.metrics.total_retries,
        workers=workers,
        prompt_path=prompt_path,
        prompt_preview=prompt_preview,
        run_id=run_id,
        created_at=created_at,
    )


def _snapshot_workers(state: PipelineState) -> tuple[WorkerSnapshot, ...]:
    worker_states = state.worker_states
    seen: set[str] = set()
    snapshots: list[WorkerSnapshot] = []

    for work_unit in state.work_units:
        worker = worker_states.get(work_unit.unit_id)
        if worker is None:
            worker = WorkerState(unit_id=work_unit.unit_id)
        snapshots.append(_snapshot_worker(work_unit.description, worker))
        seen.add(work_unit.unit_id)

    for unit_id, worker in worker_states.items():
        if unit_id in seen:
            continue
        snapshots.append(_snapshot_worker(worker.unit_id, worker))

    return tuple(snapshots)


def _snapshot_worker(description: str, worker: WorkerState) -> WorkerSnapshot:
    status = worker.status.value if isinstance(worker.status, WorkerStatus) else str(worker.status)
    return WorkerSnapshot(
        unit_id=worker.unit_id,
        description=description,
        status=status,
        status_semantic=_STATUS_TO_SEMANTIC.get(status, "info"),
        started_at=worker.started_at,
        finished_at=worker.finished_at,
        elapsed_s=_elapsed_seconds(worker),
        exit_code=worker.exit_code,
        commit_sha=worker.commit_sha,
        error_message=worker.error_message,
    )


def _elapsed_seconds(worker: WorkerState) -> float:
    if worker.started_at is None:
        return 0.0
    if worker.finished_at is not None:
        return (worker.finished_at - worker.started_at).total_seconds()
    return (datetime.now(UTC) - worker.started_at).total_seconds()
