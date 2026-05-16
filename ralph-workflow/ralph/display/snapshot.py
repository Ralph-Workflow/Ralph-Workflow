"""Immutable pipeline snapshot models.

This module projects pipeline state into a presentation-agnostic data shape
consumed by display panels and subscribers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.pipeline.progress import review_issues_found as _review_issues_found
from ralph.pipeline.worker_state import WorkerState, WorkerStatus

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy


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
    error_message: str | None
    dropped_lines: int = 0


@dataclass(frozen=True, slots=True)
class BudgetProgress:
    """Immutable progress record for a single policy-declared budget counter."""

    completed: int
    cap: int
    description: str
    tracks_budget: bool


@dataclass(frozen=True, slots=True)
class PipelineSnapshot:
    """Immutable pipeline state snapshot for transcript rendering."""

    phase: str
    previous_phase: str | None
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
    plan_summary: str | None = None
    plan_scope_items: tuple[str, ...] = ()
    plan_total_steps: int = 0
    plan_current_step: int | None = None
    plan_risks: tuple[str, ...] = ()
    active_agent: str | None = None
    active_tool: str | None = None
    active_path: str | None = None
    active_unit_id: str | None = None
    active_workdir: str | None = None
    active_command: str | None = None
    active_pattern: str | None = None
    last_activity_line: str | None = None
    waiting_status_line: str | None = None
    analysis_phase: str | None = None
    analysis_decision: str | None = None
    analysis_reason: str | None = None
    decision_log: tuple[tuple[str, str, str, str], ...] = field(default_factory=tuple)
    # Recovery observability fields
    recovery_cycle_count: int = 0
    recovery_cycle_cap: int = 200
    fallover_history: tuple[tuple[str, str, str, str], ...] = field(default_factory=tuple)
    last_failure_category: str | None = None
    last_connectivity_state: str = "unknown"
    # Policy-derived terminal flags — populated when pipeline_policy is available
    is_terminal_success: bool = False
    is_terminal_failure: bool = False
    current_phase_role: str | None = None
    previous_phase_role: str | None = None
    terminal_failure_route: str | None = None
    # Generic budget progress keyed by policy-declared counter name
    budget_progress: dict[str, BudgetProgress] = field(default_factory=dict)
    outer_dev_iteration: int | None = None  # Computed from budget_progress tracks_budget counter
    # MCP health observability
    mcp_restart_count: int = 0
    # ProcessManager-backed active process labels (compact, label-driven)
    active_process_labels: tuple[str, ...] = ()


def snapshot_from_state(
    state: PipelineState,
    *,
    prompt_path: str | None,
    prompt_preview: tuple[str, ...],
    run_id: str | None,
    pipeline_policy: PipelinePolicy | None = None,
    plan_summary: str | None = None,
    plan_scope_items: tuple[str, ...] = (),
    plan_total_steps: int = 0,
    plan_current_step: int | None = None,
    plan_risks: tuple[str, ...] = (),
    active_agent: str | None = None,
    active_tool: str | None = None,
    active_path: str | None = None,
    active_unit_id: str | None = None,
    active_workdir: str | None = None,
    active_command: str | None = None,
    active_pattern: str | None = None,
    last_activity_line: str | None = None,
    waiting_status_line: str | None = None,
    analysis_phase: str | None = None,
    analysis_decision: str | None = None,
    analysis_reason: str | None = None,
    decision_log: tuple[tuple[str, str, str, str], ...] = (),
    mcp_restart_count: int = 0,
    active_process_labels: tuple[str, ...] = (),
) -> PipelineSnapshot:
    """Project PipelineState into an immutable pipeline snapshot."""
    created_at = datetime.now(UTC)
    workers = _snapshot_workers(state)

    # Convert fallover_history to tuple of tuples for frozen dataclass
    fallover_tuples = tuple(
        (fo.phase, fo.from_agent, fo.to_agent, fo.timestamp_iso) for fo in state.fallover_history
    )

    is_terminal_success = False
    is_terminal_failure = False
    current_phase_role: str | None = None
    previous_phase_role: str | None = None
    terminal_failure_route: str | None = None

    if pipeline_policy is not None:
        phase_def = pipeline_policy.phases.get(state.phase)
        prev_def = (
            pipeline_policy.phases.get(state.previous_phase) if state.previous_phase else None
        )
        if phase_def is not None:
            current_phase_role = phase_def.role
            is_terminal_success = (
                phase_def.role == "terminal" and phase_def.terminal_outcome == "success"
            ) or state.phase == pipeline_policy.terminal_phase
            is_terminal_failure = (
                phase_def.role == "terminal" and phase_def.terminal_outcome == "failure"
            )
        if prev_def is not None:
            previous_phase_role = prev_def.role
        # Resolve terminal failure route from the first failure-terminal phase found
        for _pname, _pdef in pipeline_policy.phases.items():
            if _pdef.role == "terminal" and _pdef.terminal_outcome == "failure":
                terminal_failure_route = _pname
                break
        if terminal_failure_route is None:
            terminal_failure_route = pipeline_policy.recovery.failed_route

    budget_progress: dict[str, BudgetProgress] = {}
    if pipeline_policy is not None:
        for bp_name, bp_cfg in pipeline_policy.budget_counters.items():
            budget_progress[bp_name] = BudgetProgress(
                completed=state.get_outer_progress(bp_name),
                cap=state.get_budget_cap(bp_name),
                description=bp_cfg.description or bp_name,
                tracks_budget=bp_cfg.tracks_budget,
            )
    else:
        for bp_name, cap in state.budget_caps.items():
            budget_progress[bp_name] = BudgetProgress(
                completed=state.get_outer_progress(bp_name),
                cap=cap,
                description=bp_name,
                tracks_budget=False,
            )

    return PipelineSnapshot(
        phase=state.phase,
        previous_phase=state.previous_phase,
        review_issues_found=_review_issues_found(state, pipeline_policy),
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
        plan_summary=plan_summary,
        plan_scope_items=plan_scope_items,
        plan_total_steps=plan_total_steps,
        plan_current_step=plan_current_step,
        plan_risks=plan_risks,
        active_agent=active_agent,
        active_tool=active_tool,
        active_path=active_path,
        active_unit_id=active_unit_id,
        active_workdir=active_workdir,
        active_command=active_command,
        active_pattern=active_pattern,
        last_activity_line=last_activity_line,
        waiting_status_line=waiting_status_line,
        analysis_phase=analysis_phase,
        analysis_decision=analysis_decision,
        analysis_reason=analysis_reason,
        decision_log=tuple(decision_log),
        recovery_cycle_count=state.recovery_cycle_count,
        recovery_cycle_cap=state.recovery_cycle_cap,
        fallover_history=fallover_tuples,
        last_failure_category=state.last_failure_category,
        last_connectivity_state=state.last_connectivity_state,
        is_terminal_success=is_terminal_success,
        is_terminal_failure=is_terminal_failure,
        current_phase_role=current_phase_role,
        previous_phase_role=previous_phase_role,
        terminal_failure_route=terminal_failure_route,
        budget_progress=budget_progress,
        outer_dev_iteration=next(
            (
                bp.completed
                for bp in budget_progress.values()
                if bp.tracks_budget and isinstance(bp.completed, int) and bp.completed > 0
            ),
            None,
        ),
        mcp_restart_count=mcp_restart_count,
        active_process_labels=active_process_labels,
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
        error_message=worker.error_message,
    )


def _elapsed_seconds(worker: WorkerState) -> float:
    if worker.started_at is None:
        return 0.0
    if worker.finished_at is not None:
        return (worker.finished_at - worker.started_at).total_seconds()
    return (datetime.now(UTC) - worker.started_at).total_seconds()


__all__ = ["BudgetProgress", "PipelineSnapshot", "WorkerSnapshot", "snapshot_from_state"]
