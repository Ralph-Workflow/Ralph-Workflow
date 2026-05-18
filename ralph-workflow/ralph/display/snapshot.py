"""Immutable pipeline snapshot models.

This module projects pipeline state into a presentation-agnostic data shape
consumed by display panels and subscribers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.display.budget_progress import BudgetProgress
from ralph.display.pipeline_snapshot import PipelineSnapshot
from ralph.display.worker_snapshot import WorkerSnapshot
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


@dataclass(frozen=True)
class SnapshotContext:
    """Display context for building a PipelineSnapshot from a PipelineState.

    All fields are optional so callers can populate only what they know.
    """

    prompt_path: str | None = None
    prompt_preview: tuple[str, ...] = ()
    run_id: str | None = None
    pipeline_policy: PipelinePolicy | None = None
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
    decision_log: tuple[tuple[str, str, str, str], ...] = ()
    mcp_restart_count: int = 0
    active_process_labels: tuple[str, ...] = ()


def snapshot_from_state(
    state: PipelineState,
    context: SnapshotContext | None = None,
) -> PipelineSnapshot:
    """Project PipelineState into an immutable pipeline snapshot."""
    effective_context = context or SnapshotContext()
    created_at = datetime.now(UTC)
    workers = _snapshot_workers(state)
    pipeline_policy = effective_context.pipeline_policy

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
        prompt_path=effective_context.prompt_path,
        prompt_preview=effective_context.prompt_preview,
        run_id=effective_context.run_id,
        created_at=created_at,
        plan_summary=effective_context.plan_summary,
        plan_scope_items=effective_context.plan_scope_items,
        plan_total_steps=effective_context.plan_total_steps,
        plan_current_step=effective_context.plan_current_step,
        plan_risks=effective_context.plan_risks,
        active_agent=effective_context.active_agent,
        active_tool=effective_context.active_tool,
        active_path=effective_context.active_path,
        active_unit_id=effective_context.active_unit_id,
        active_workdir=effective_context.active_workdir,
        active_command=effective_context.active_command,
        active_pattern=effective_context.active_pattern,
        last_activity_line=effective_context.last_activity_line,
        waiting_status_line=effective_context.waiting_status_line,
        analysis_phase=effective_context.analysis_phase,
        analysis_decision=effective_context.analysis_decision,
        analysis_reason=effective_context.analysis_reason,
        decision_log=tuple(effective_context.decision_log),
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
        mcp_restart_count=effective_context.mcp_restart_count,
        active_process_labels=effective_context.active_process_labels,
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


__all__ = [
    "BudgetProgress",
    "PipelineSnapshot",
    "SnapshotContext",
    "WorkerSnapshot",
    "snapshot_from_state",
]
