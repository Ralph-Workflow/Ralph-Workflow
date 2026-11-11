
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ralph.pipeline import handoffs
from ralph.pipeline.agent_retry_intent import cleared_agent_retry_intent
from ralph.pipeline.state import CommitState, PipelineState
from ralph.policy.models import (
    LifecyclePhasePolicy,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseLoopPolicy,
)

if TYPE_CHECKING:
    from ralph.checkpoint.run_context import RunContext
    from ralph.config.enums import PipelinePhase
    from ralph.policy.models import PipelinePolicy


def review_issues_found(state: PipelineState, policy: PipelinePolicy | None = None) -> bool:
    if state.review_outcome is None:
        return False
    if policy is not None:
        for phase_def in policy.phases.values():
            if (
                isinstance(phase_def, PhaseDefinition)
                and phase_def.role == "review"
                and phase_def.clean_outcome is not None
            ):
                return state.review_outcome != phase_def.clean_outcome
    return True


@dataclass(frozen=True)
class AnalysisLoopCounter:

    completed: int
    cap: int

    @property
    def display_iteration(self) -> int:
        return min(max(self.completed, 0), max(self.cap - 1, 0)) + 1

    @property
    def is_final(self) -> bool:
        return self.completed >= self.cap - 1

    @property
    def should_skip_reentry(self) -> bool:
        return self.completed >= self.cap

    @property
    def next_completed(self) -> int:
        return max(0, min(self.completed + 1, self.cap))


def resolve_analysis_cap(
    iteration_field: str,
    policy: PipelinePolicy,
) -> int:
    if iteration_field in policy.loop_counters:
        return policy.loop_counters[iteration_field].default_max
    msg = (
        f"Analysis loop counter '{iteration_field}' is not declared in pipeline.loop_counters. "
        "Analysis caps must come from a declared loop counter."
    )
    raise ValueError(msg)


def completed_analysis_passes(
    state: PipelineState,
    phase: str,
    policy: PipelinePolicy,
) -> int:
    phase_def = policy.phases.get(phase)
    if phase_def is None or phase_def.loop_policy is None:
        return 0
    stored = state.get_loop_iteration(phase_def.loop_policy.iteration_state_field)
    if phase_def.invocation_gate is None:
        return stored
    return max(0, stored - 1)


def is_final_analysis_iteration(current_iteration: int, max_iterations: int) -> bool:
    return AnalysisLoopCounter(current_iteration, max_iterations).is_final


def should_skip_analysis_reentry(current_iteration: int, max_iterations: int) -> bool:
    return AnalysisLoopCounter(current_iteration, max_iterations).should_skip_reentry


def advance_phase(
    state: PipelineState,
    target_phase: PipelinePhase,
    *,
    policy: PipelinePolicy | None,
) -> PipelineState:
    if policy is None:
        msg = (
            f"advance_phase requires PipelinePolicy to advance to '{target_phase}'; "
            "pass the active pipeline policy to resolve drain and commit-role semantics"
        )
        raise ValueError(msg)

    updates: dict[str, object] = {
        "phase": target_phase,
        "previous_phase": state.phase,
        "last_agent_session_id": None,
        "agent_retry_intent": cleared_agent_retry_intent(),
        "last_failure_category": None,
        "recovery_epoch": 0,
    }

    phase_def = policy.phases.get(target_phase)
    updates["execution_cycle_charged"] = (
        False
        if phase_def is not None and phase_def.role == "execution"
        else state.execution_cycle_charged
    )
    is_commit_phase = phase_def is not None and phase_def.role == "commit"

    if is_commit_phase:
        updates["commit"] = CommitState()

    updates["current_drain"] = handoffs.resolve_phase_drain(target_phase, policy)

    return state.copy_with(**updates)


def apply_analysis_success(
    state: PipelineState,
    advanced_state: PipelineState,
    *,
    policy: PipelinePolicy | None = None,
) -> PipelineState:
    result = advanced_state.copy_with(review_outcome=None)
    if policy is not None:
        phase_def = policy.phases.get(state.phase)
        if phase_def is not None and isinstance(phase_def.loop_policy, PhaseLoopPolicy):
            iteration_field = phase_def.loop_policy.iteration_state_field
            result = result.with_loop_iteration(iteration_field, 0)
    return result


def apply_analysis_loopback(
    state: PipelineState,
    advanced_state: PipelineState,
    iteration_field: str,
    *,
    max_iterations: int,
    review_outcome: str | None = None,
    increment: bool = True,
) -> PipelineState:
    result = advanced_state
    if increment:
        clamped = AnalysisLoopCounter(
            state.get_loop_iteration(iteration_field),
            max_iterations,
        ).next_completed
        result = result.with_loop_iteration(iteration_field, clamped)
    if review_outcome is not None:
        result = result.copy_with(review_outcome=review_outcome)
    return result


def resolve_execution_cycle_loop_counter(
    phase: str,
    policy: PipelinePolicy,
) -> str | None:
    visited: set[str] = set()
    current: str | None = phase
    while current is not None and current not in visited:
        visited.add(current)
        phase_def = policy.phases.get(current)
        if phase_def is None:
            return None
        loop_policy = phase_def.loop_policy
        if (
            phase_def.role == "analysis"
            and loop_policy is not None
            and phase_def.transitions.on_loopback == phase
        ):
            return loop_policy.iteration_state_field
        current = phase_def.transitions.on_success
    return None


def apply_execution_cycle_outcome(
    state: PipelineState,
    advanced_state: PipelineState,
    *,
    policy: PipelinePolicy,
) -> PipelineState:
    iteration_field = resolve_execution_cycle_loop_counter(state.phase, policy)
    if iteration_field is None:
        return advanced_state
    return advanced_state.with_loop_iteration(
        iteration_field,
        AnalysisLoopCounter(
            state.get_loop_iteration(iteration_field),
            resolve_analysis_cap(iteration_field, policy),
        ).next_completed,
    ).copy_with(execution_cycle_charged=True)


def charge_analysis_cycle_for_gate(
    state: PipelineState,
    execution_phase: str,
    *,
    policy: PipelinePolicy,
) -> PipelineState:
    iteration_field = resolve_execution_cycle_loop_counter(execution_phase, policy)
    if iteration_field is None:
        return state
    return state.with_loop_iteration(
        iteration_field,
        AnalysisLoopCounter(
            state.get_loop_iteration(iteration_field),
            resolve_analysis_cap(iteration_field, policy),
        ).next_completed,
    ).copy_with(execution_cycle_charged=True)


def cumulative_execution_seconds(
    state: PipelineState,
    execution_phase: str,
) -> float:
    start = state.cycle_timing_start_index
    return sum(
        record.elapsed.total_seconds()
        for record in state.phase_timings[start:]
        if execution_phase == record.phase
    )


def analysis_loopback_is_already_charged(
    phase: str,
    state: PipelineState,
    policy: PipelinePolicy,
) -> bool:
    phase_def = policy.phases.get(phase)
    if phase_def is None or phase_def.loop_policy is None or phase_def.role != "analysis":
        return False
    return state.execution_cycle_charged


def apply_commit_outcome(
    state: PipelineState,
    advanced_state: PipelineState,
    *,
    skipped: bool,
    policy: PipelinePolicy | None = None,
) -> PipelineState:
    if policy is None:
        msg = f"apply_commit_outcome requires PipelinePolicy for commit-role phase {state.phase!r}"
        raise ValueError(msg)
    target_phase = state.phase
    phase_def = policy.phases.get(target_phase)
    if phase_def is not None and phase_def.role == "commit_review":
        target_phase = phase_def.transitions.on_success
    lifecycle = policy.lifecycle_phases.get(target_phase)
    if lifecycle is not None:
        return _apply_lifecycle_completion_policy(state, advanced_state, lifecycle)
    target_phase_def = policy.phases.get(target_phase)
    if target_phase_def is not None and target_phase_def.commit_policy is not None:
        return _apply_commit_outcome_policy_driven(
            state, advanced_state, skipped, target_phase_def.commit_policy
        )
    return advanced_state


def consume_post_commit_phase_override(state: PipelineState) -> PipelineState:
    return state.copy_with(post_commit_phase_override=None)


def _apply_lifecycle_completion_policy(
    state: PipelineState,
    advanced_state: PipelineState,
    lifecycle: LifecyclePhasePolicy,
) -> PipelineState:
    result = advanced_state
    for field_name in lifecycle.loop_resets:
        result = result.with_loop_iteration(field_name, 0)
    counter = lifecycle.increments_counter
    if counter is None or counter == "none":
        return result
    result = result.with_outer_progress(counter, state.get_outer_progress(counter) + 1)
    return result.copy_with(cycle_timing_start_index=len(result.phase_timings))


def _apply_commit_outcome_policy_driven(
    state: PipelineState,
    advanced_state: PipelineState,
    skipped: bool,
    commit_policy: object,
) -> PipelineState:
    if not isinstance(commit_policy, PhaseCommitPolicy):
        return advanced_state

    result = advanced_state
    for field_name in commit_policy.loop_resets:
        result = result.with_loop_iteration(field_name, 0)

    counter = commit_policy.increments_counter
    if counter is None:
        return result

    advances = not skipped or commit_policy.skipped_advances_progress
    if advances:
        return result.with_outer_progress(
            counter,
            state.get_outer_progress(counter) + 1,
        )
    return result


def apply_budget_counter_increment(
    state: PipelineState,
    advanced_state: PipelineState,
    counter: str | None,
) -> PipelineState:
    if counter is None or counter == "none":
        return advanced_state
    return advanced_state.with_outer_progress(counter, state.get_outer_progress(counter) + 1)


def _tracked_budget_counters_in_commit_order(policy: PipelinePolicy) -> list[str]:
    phases = policy.phases
    visited: set[str] = set()
    queue: list[str] = [policy.entry_phase]
    result: list[str] = []
    seen: set[str] = set()

    while queue:
        current = queue.pop(0)
        if current in visited or current not in phases:
            continue
        visited.add(current)
        phase_def = phases[current]

        counter: str | None = None
        lifecycle = policy.lifecycle_phases.get(current)
        if lifecycle is not None:
            counter = lifecycle.increments_counter
        elif phase_def.role == "commit" and phase_def.commit_policy is not None:
            counter = phase_def.commit_policy.increments_counter
        if (
            counter
            and counter != "none"
            and counter not in seen
            and counter in policy.budget_counters
            and policy.budget_counters[counter].tracks_budget
        ):
            result.append(counter)
            seen.add(counter)

        t = phase_def.transitions
        next_phases: list[str] = [
            ph for ph in [t.on_success, t.on_failure, t.on_loopback] if ph and ph not in visited
        ]
        next_phases.extend(
            dr.target for dr in phase_def.decisions.values() if dr.target not in visited
        )
        next_phases.extend(tgt for tgt in phase_def.bypass_routes.values() if tgt not in visited)
        queue.extend(next_phases)

    return result


def derive_run_context_progress(
    state: PipelineState,
    run_context: RunContext,
    policy: PipelinePolicy | None = None,
) -> RunContext:
    if policy is not None:
        tracked = _tracked_budget_counters_in_commit_order(policy)
        dev_counter = tracked[0] if len(tracked) > 0 else None
        rev_counter = tracked[1] if len(tracked) > 1 else None
        return replace(
            run_context,
            actual_developer_runs=state.get_outer_progress(dev_counter) if dev_counter else 0,
            actual_reviewer_runs=state.get_outer_progress(rev_counter) if rev_counter else 0,
        )
    return replace(
        run_context,
        actual_developer_runs=0,
        actual_reviewer_runs=0,
    )
