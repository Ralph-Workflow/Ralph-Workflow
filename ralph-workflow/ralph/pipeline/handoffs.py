"""Pure helpers for phase handoff and drain resolution.

This module centralizes the policy-driven routing and phase-to-drain lookup
used across the reducer and runtime. Keeping these helpers pure makes the
handoff contract easy to unit test and keeps runtime injection limited to the
policy data loaded at the composition root.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline import progress
from ralph.pipeline.exhausted_analysis_bypass_result import ExhaustedAnalysisBypassResult
from ralph.pipeline.exhausted_analysis_skip import ExhaustedAnalysisSkip
from ralph.policy.models import PhaseLoopPolicy

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy


Handoff = ExhaustedAnalysisBypassResult

#: Decision name whose declared ``cycle_outcome`` describes a cycle that reached
#: the finalization path. Every route that skips an analysis phase instead of
#: invoking it concludes the cycle the same way that decision would, so the
#: outcome is read from policy rather than hardcoded per call site.
_FORWARD_DECISION = "completed"


def declared_forward_cycle_outcome(
    phase: PipelinePhase,
    pipeline_policy: PipelinePolicy,
) -> str | None:
    """Return the cycle outcome an analysis phase declares for its forward exit.

    Post-commit routing matches on the cycle outcome carried into the commit
    phase, so any path that skips an analysis phase (an exhausted loop budget,
    a denied invocation gate) must stamp the outcome that phase would have
    stamped. Returns ``None`` when the phase declares no forward decision or
    the decision declares no outcome.
    """
    phase_def = pipeline_policy.phases.get(phase)
    if phase_def is None:
        return None
    route = phase_def.decisions.get(_FORWARD_DECISION)
    return None if route is None else route.cycle_outcome


def resolve_phase_drain(
    phase: PipelinePhase,
    pipeline_policy: PipelinePolicy,
) -> str | None:
    """Return the configured drain for a phase."""
    phase_def = pipeline_policy.phases.get(phase)
    return phase_def.drain if phase_def is not None else None


def resolve_next_phase(
    current_phase: PipelinePhase,
    signal: str,
    pipeline_policy: PipelinePolicy,
) -> PipelinePhase:
    """Resolve the next phase based on a signal and the pipeline policy."""
    phase_def = pipeline_policy.phases.get(current_phase)
    if phase_def is None:
        msg = f"Cannot resolve transition: phase '{current_phase}' not found"
        raise ValueError(msg)

    transitions = phase_def.transitions

    target: str | None
    if signal == "success":
        target = transitions.on_success
    elif signal == "failure":
        target = transitions.on_failure
    elif signal == "loopback":
        target = transitions.on_loopback
    else:
        msg = f"Unknown signal: {signal}"
        raise ValueError(msg)

    if target is None:
        msg = (
            f"No '{signal}' transition defined for phase '{current_phase}'. "
            f"Define on_{signal} in pipeline.toml or set the phase to terminal."
        )
        raise ValueError(msg)

    terminal_states = pipeline_policy.terminal_states()

    if target in terminal_states:
        return target

    if target not in pipeline_policy.phases:
        msg = (
            f"Transition from '{current_phase}' on signal '{signal}' "
            f"references unknown phase '{target}'. "
            f"Either add '{target}' to pipeline.phases or use the declared terminal phase."
        )
        raise ValueError(msg)

    return target


def resolve_exhausted_analysis_bypass(
    state: PipelineState,
    candidate_phase: PipelinePhase,
    pipeline_policy: PipelinePolicy,
) -> ExhaustedAnalysisBypassResult:
    """Resolve exhausted-analysis re-entry from one canonical policy-driven helper.

    The helper accepts an analysis phase candidate and follows that phase's
    declared success transition whenever its loop budget is already exhausted.
    Each skipped analysis phase has its loop counter reset to 0, its declared
    forward cycle outcome stamped onto the state, and its skip metadata
    recorded so reducer and runner paths can reuse the exact same bypass
    contract. Stamping the outcome keeps a skipped analysis phase routing
    through post-commit exactly like an invoked one: the cycle finalizes and
    the next cycle starts while its budget counter still has room.

    Only the reducer's phase-advance path adopts the returned state; the
    runner, effect router, and display callers consume the resolved target and
    the skip metadata for logging and rendering, and discard it. Every runner
    event is routed back through the reducer, so the state changes here are
    applied exactly once.
    """
    current_state = state
    current_target = candidate_phase
    skipped: list[ExhaustedAnalysisSkip] = []
    visited: set[str] = set()

    while current_target not in visited:
        visited.add(current_target)
        phase_def = pipeline_policy.phases.get(current_target)
        if phase_def is None or not isinstance(phase_def.loop_policy, PhaseLoopPolicy):
            break

        iteration_field = phase_def.loop_policy.iteration_state_field
        current_iteration = current_state.get_loop_iteration(iteration_field)
        max_iterations = progress.resolve_analysis_cap(
            iteration_field,
            pipeline_policy,
        )
        if not progress.should_skip_analysis_reentry(current_iteration, max_iterations):
            break

        next_target = resolve_next_phase(current_target, "success", pipeline_policy)
        skipped.append(
            ExhaustedAnalysisSkip(
                phase=current_target,
                target_phase=next_target,
                iteration_field=iteration_field,
                iteration_value=current_iteration,
                max_iterations=max_iterations,
            )
        )
        current_state = current_state.with_loop_iteration(iteration_field, 0).copy_with(
            review_outcome=None
        )
        forward_outcome = declared_forward_cycle_outcome(current_target, pipeline_policy)
        # A verdict already recorded for this cycle (an analysis decision that
        # routed here) outranks the forward outcome of a phase that never ran.
        if forward_outcome is not None and current_state.pending_cycle_outcome is None:
            current_state = current_state.copy_with(pending_cycle_outcome=forward_outcome)
        current_target = next_target

    return ExhaustedAnalysisBypassResult(
        state=current_state,
        target_phase=current_target,
        skipped=tuple(skipped),
    )



def commit_closes_a_cycle(
    phase: PipelinePhase,
    pipeline_policy: PipelinePolicy,
) -> bool:
    """Return whether a commit phase is the one that closes a cycle.

    A cycle's verdict belongs to the cycle, so only the commit that ends it
    may consume it. Clearing at every commit-role phase destroyed a verdict
    recorded before an intermediate commit, and the outcome-less cycle was
    then routed as the forward outcome — a failed run reporting success.
    The closing commit is the one whose budget counter post-commit routing
    reads, resolved exactly as :func:`_compute_budget_state` resolves it.
    """
    return _budget_counter_for_phase(phase, pipeline_policy) is not None


def _budget_counter_for_phase(
    phase: PipelinePhase,
    pipeline_policy: PipelinePolicy,
) -> str | None:
    """Return the tracked budget counter governing a commit phase, if any."""
    lifecycle = pipeline_policy.lifecycle_phases.get(phase)
    if lifecycle is not None:
        counter = lifecycle.increments_counter
    else:
        phase_def = pipeline_policy.phases.get(phase)
        if phase_def is None or phase_def.commit_policy is None:
            return None
        counter = (
            phase_def.commit_policy.route_counter or phase_def.commit_policy.increments_counter
        )
    if not counter or counter == "none":
        return None
    tracked = pipeline_policy.budget_counters.get(counter)
    return counter if tracked is not None and tracked.tracks_budget else None


def resolve_post_commit_phase(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
) -> PipelinePhase:
    """Resolve next phase for a successful commit with optional budget guards.

    Routing is driven by post_commit_routes in policy, matched by phase name
    and budget_state. This works for any commit-role phase, not just the
    canonical development_commit/review_commit names.

    A commit phase whose routes all declare a cycle outcome, reached with no
    outcome recorded on the state, resolves against the forward outcome rather
    than falling through the table: many routes into a final commit never
    record a verdict (an agent-chain workflow fallback, a result-status
    override, a resumed pre-feature checkpoint, an analysis phase that
    succeeded without emitting a decision), and falling through lands on the
    phase's success transition — the terminal — ending the whole run while the
    cycle budget still had room. Phases that declare no routes at all keep
    their success transition unchanged.
    """
    if state.post_commit_phase_override is not None:
        target = state.post_commit_phase_override
        if target not in pipeline_policy.phases and target not in pipeline_policy.terminal_states():
            raise ValueError(f"Post-commit override references unknown phase '{target}'")
        return target

    phase_def = pipeline_policy.phases.get(state.phase)
    is_commit_phase = phase_def is not None and phase_def.role == "commit"

    if is_commit_phase:
        budget_state = _compute_budget_state(state, pipeline_policy)
        if budget_state is not None:
            for effective_outcome in _candidate_cycle_outcomes(state):
                for route in pipeline_policy.post_commit_routes:
                    outcome_matches = (
                        route.when.cycle_outcome is None
                        or route.when.cycle_outcome == effective_outcome
                    )
                    if (
                        route.when.phase == state.phase
                        and route.when.budget_state == budget_state
                        and outcome_matches
                    ):
                        if effective_outcome != state.pending_cycle_outcome:
                            logger.bind(component="policy.routing").warning(
                                "post-commit routing in phase '{}' found no recorded cycle "
                                "outcome; routing as '{}' to '{}' instead of falling through "
                                "to the success transition",
                                state.phase,
                                effective_outcome,
                                route.target,
                            )
                        return route.target

    return resolve_next_phase(state.phase, "success", pipeline_policy)


def _candidate_cycle_outcomes(state: PipelineState) -> tuple[str | None, ...]:
    """Return the outcomes to match against, most authoritative first.

    A recorded outcome is the only candidate. An unrecorded one is retried as
    the forward decision's outcome — the same name an analysis phase stamps on
    its forward exit — so a cycle that finished without a verdict is routed by
    the budget rather than by the table's fallthrough.
    """
    if state.pending_cycle_outcome is not None:
        return (state.pending_cycle_outcome,)
    return (None, _FORWARD_DECISION)


def _compute_budget_state(state: PipelineState, pipeline_policy: PipelinePolicy) -> str | None:
    """Determine the budget_state label for the current commit phase.

    Uses only policy-declared counter names from pipeline.budget_counters.
    Counter identity is fully generic: any counter name declared with
    tracks_budget=True is eligible.

    Returns:
        'remaining'  — the phase's own budget counter still has budget left
        'exhausted'  — this counter is at 0 but another tracked counter has budget
        'no_review'  — this counter is at 0 and no other tracked counter has budget
        None         — no tracked budget counter governs this phase
    """
    counter = _budget_counter_for_phase(state.phase, pipeline_policy)
    if counter is None:
        return None

    if state.get_budget_remaining(counter) > 0:
        return "remaining"

    for other_name, other_cfg in pipeline_policy.budget_counters.items():
        if (
            other_name != counter
            and other_cfg.tracks_budget
            and state.get_budget_remaining(other_name) > 0
        ):
            return "exhausted"

    return "no_review"


__all__ = [
    "ExhaustedAnalysisBypassResult",
    "ExhaustedAnalysisSkip",
    "Handoff",
    "commit_closes_a_cycle",
    "declared_forward_cycle_outcome",
    "resolve_exhausted_analysis_bypass",
    "resolve_next_phase",
    "resolve_phase_drain",
    "resolve_post_commit_phase",
]
