"""Canonical workflow progress accounting.

This module owns all mutations for workflow progress fields, including completed
outer progress counters, inner analysis-loop counters, routing budgets, review-issue
flags tied to progress boundaries, and checkpoint-facing progress mirrors derived
from canonical state and active policy.

Contract:

* Outer progress counters are named by policy (via budget_counters); the runtime
  never assumes a specific counter name.
* Analysis loopbacks mutate only the inner loop counter for the current cycle or pass.
* Capped analysis loopback preserves outer progress and carries the inner loop counter
  to the cap until analysis or commit outcome resets it.
* Skipped commits route onward without incrementing outer progress, but they end
  the current inner loop and therefore reset the corresponding analysis counter.
* Checkpoint mirrors derive from canonical ``PipelineState`` and policy-declared budget
  counters: the first budget-tracked counter (in commit-phase BFS order) maps to
  ``actual_developer_runs``; the second maps to ``actual_reviewer_runs``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ralph.pipeline.handoffs import resolve_phase_drain
from ralph.pipeline.state import CommitState, PipelineState

if TYPE_CHECKING:
    from ralph.checkpoint.run_context import RunContext
    from ralph.config.enums import PipelinePhase
    from ralph.policy.models import PipelinePolicy


def review_issues_found(state: PipelineState, policy: PipelinePolicy | None = None) -> bool:
    """Return True when the current review outcome indicates issues were found.

    When policy is provided, checks the active review phase's clean_outcome to
    determine whether the stored review_outcome represents an issues-found state.
    When policy is None or no review phase with clean_outcome is declared, falls
    back to checking whether review_outcome is non-None.
    """
    if state.review_outcome is None:
        return False
    if policy is not None:
        from ralph.policy.models import PhaseDefinition  # noqa: PLC0415

        for phase_def in policy.phases.values():
            if (
                isinstance(phase_def, PhaseDefinition)
                and phase_def.role == "review"
                and phase_def.clean_outcome is not None
            ):
                return state.review_outcome != phase_def.clean_outcome
    return True


def resolve_analysis_cap(
    state: PipelineState,
    iteration_field: str,
    policy: PipelinePolicy,
    *,
    fallback_max: int,
) -> int:
    """Resolve the effective analysis cap from canonical state/policy sources."""
    cap_value = state.loop_caps.get(iteration_field)
    if cap_value is not None:
        return cap_value
    if iteration_field in policy.loop_counters:
        return policy.loop_counters[iteration_field].default_max
    return fallback_max


def is_final_analysis_iteration(current_iteration: int, max_iterations: int) -> bool:
    """Return True when the current analysis state should be treated as final.

    This intentionally matches the user-facing label semantics.
    """
    return current_iteration >= max_iterations - 1


def should_skip_analysis_reentry(current_iteration: int, max_iterations: int) -> bool:
    """Return True when a later attempt to enter analysis must be skipped.

    This intentionally uses the same predicate as the user-facing FINAL label.
    """
    return is_final_analysis_iteration(current_iteration, max_iterations)


def advance_phase(
    state: PipelineState,
    target_phase: PipelinePhase,
    *,
    policy: PipelinePolicy | None,
) -> PipelineState:
    """Advance phases while applying only canonical routing-budget bookkeeping."""
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
        "session_preserve_retry_pending": False,
    }

    phase_def = policy.phases.get(target_phase)
    is_commit_phase = phase_def is not None and phase_def.role == "commit"

    if is_commit_phase:
        updates["commit"] = CommitState()

    updates["current_drain"] = resolve_phase_drain(target_phase, policy)

    return state.copy_with(**updates)


def apply_analysis_success(
    state: PipelineState,
    advanced_state: PipelineState,
    *,
    policy: PipelinePolicy | None = None,
) -> PipelineState:
    """Reset inner-loop progress when analysis exits successfully to commit/approval."""
    result = advanced_state.copy_with(review_outcome=None)
    if policy is not None:
        phase_def = policy.phases.get(state.phase)
        if phase_def is not None:
            from ralph.policy.models import PhaseLoopPolicy  # noqa: PLC0415

            if isinstance(phase_def.loop_policy, PhaseLoopPolicy):
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
) -> PipelineState:
    """Apply canonical loopback bookkeeping for an analysis phase."""
    clamped = max(0, min(state.get_loop_iteration(iteration_field) + 1, max_iterations))
    result = advanced_state.with_loop_iteration(iteration_field, clamped)
    if review_outcome is not None:
        result = result.copy_with(review_outcome=review_outcome)
    return result


def apply_commit_outcome(
    state: PipelineState,
    advanced_state: PipelineState,
    *,
    skipped: bool,
    policy: PipelinePolicy | None = None,
) -> PipelineState:
    """Apply canonical outer-progress semantics for commit success vs skip.

    Policy is required. The counter and loop resets are driven by commit_policy.
    When commit_policy is absent on the phase, returns advanced_state unchanged.
    """
    if policy is None:
        msg = f"apply_commit_outcome requires PipelinePolicy for commit-role phase {state.phase!r}"
        raise ValueError(msg)
    phase_def = policy.phases.get(state.phase)
    if phase_def is not None and phase_def.commit_policy is not None:
        return _apply_commit_outcome_policy_driven(
            state, advanced_state, skipped, phase_def.commit_policy
        )
    return advanced_state


def _apply_commit_outcome_policy_driven(
    state: PipelineState,
    advanced_state: PipelineState,
    skipped: bool,
    commit_policy: object,
) -> PipelineState:
    """Apply commit outcome using policy-declared commit_policy."""
    from ralph.policy.models import PhaseCommitPolicy  # noqa: PLC0415

    if not isinstance(commit_policy, PhaseCommitPolicy):
        return advanced_state

    # Reset loop counters declared in loop_resets via with_loop_iteration
    # (handles both legacy typed fields and custom dict-based fields)
    result = advanced_state
    for field_name in commit_policy.loop_resets:
        result = result.with_loop_iteration(field_name, 0)

    counter = commit_policy.increments_counter
    if counter is None:
        return result

    result = result.with_budget_remaining(
        counter,
        max(0, state.get_budget_remaining(counter) - 1),
    )
    if skipped:
        return result

    return result.with_outer_progress(
        counter,
        state.get_outer_progress(counter) + 1,
    )


def _tracked_budget_counters_in_commit_order(policy: PipelinePolicy) -> list[str]:
    """Return tracked budget counter names in the order their commit phases appear in BFS."""
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

        if phase_def.role == "commit" and phase_def.commit_policy is not None:
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
            ph for ph in [t.on_success, t.on_failure, t.on_loopback]
            if ph and ph not in visited
        ]
        next_phases.extend(
            dr.target for dr in phase_def.decisions.values() if dr.target not in visited
        )
        next_phases.extend(
            tgt for tgt in phase_def.bypass_routes.values() if tgt not in visited
        )
        queue.extend(next_phases)

    return result


def derive_run_context_progress(
    state: PipelineState,
    run_context: RunContext,
    policy: PipelinePolicy | None = None,
) -> RunContext:
    """Derive checkpoint-facing progress mirrors from canonical pipeline state.

    Resolves counter names by BFS through commit phases in the active policy;
    the first budget-tracked counter maps to actual_developer_runs, the second to
    actual_reviewer_runs. When policy is None, both fields are set to 0.
    """
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
