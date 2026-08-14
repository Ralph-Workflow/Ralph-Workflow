"""Reducer tests for cycle continuation after a short-circuited finalization.

A development cycle can reach the final-commit path in three ways: an analysis
phase approves it, the analysis loop budget is exhausted, or the cycle timebox
redirects an expired development entry. All three conclude the SAME cycle, so
all three must leave the run able to start the next cycle while the iteration
budget still has room. These tests drive the pure ``reduce`` state machine over
the bundled default policy and assert on the phase the run lands in after the
final commit — the operator-visible behavior — never on internal routing state.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.cycle_timing import RoutingTiming
from ralph.pipeline.events import AnalysisDecisionEvent, PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _policy() -> PipelinePolicy:
    from ralph.policy.loader import load_policy

    return load_policy(_DEFAULTS_DIR).pipeline


def _rt(elapsed: float) -> RoutingTiming:
    return RoutingTiming(monotonic_now=elapsed, total_elapsed_seconds=elapsed)


def _drive_to_next_cycle(
    state: PipelineState,
    policy: PipelinePolicy,
    *,
    elapsed: float,
) -> PipelineState:
    """Advance from the final-commit cleanup phase through the final commit."""
    assert state.phase == "development_final_commit_cleanup"
    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(elapsed))
    assert state.phase == "development_final_commit"
    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(elapsed))
    return state


def test_exhausted_analysis_loop_starts_next_cycle_when_budget_remains() -> None:
    """An exhausted development-analysis loop finalizes, then plans the next cycle."""
    policy = _policy()
    state = PipelineState(
        phase="development_commit",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"development_analysis_iteration": 5},
        last_execution_result_status="partial",
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(100.0))
    assert state.phase == "development_final_commit_cleanup"

    state = _drive_to_next_cycle(state, policy, elapsed=100.0)

    assert state.phase == "planning"


def test_timebox_redirect_starts_next_cycle_when_budget_remains() -> None:
    """A deadline-redirected cycle finalizes, then plans the next cycle."""
    policy = _policy()
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=7200.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(7200.0),
    )
    assert state.phase == "development_final_commit_cleanup"

    state = _drive_to_next_cycle(state, policy, elapsed=7200.0)

    assert state.phase == "planning"


def test_concluded_cycle_does_not_redirect_a_later_development_entry() -> None:
    """Consumed seconds survive a concluded cycle and must not redirect the next one."""
    policy = _policy()
    state = PipelineState(
        phase="planning_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        cycle_timebox_active=False,
        cycle_timebox_consumed_seconds=7200.0,
    )

    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(7200.0))

    assert state.phase == "development"
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 0.0


def test_recorded_failure_outranks_a_deadline_redirect() -> None:
    """Running out of time is not a verdict and must not overwrite one.

    The analysis phase records `failed` and its route re-enters development,
    which the expired deadline then redirects to finalization. The recorded
    failure has to survive that redirect, or an out-of-budget failed run
    reports success.
    """
    from ralph.policy.models import PhaseDecisionRoute

    base = _policy()
    analysis = base.phases["development_analysis"]
    policy = base.model_copy(
        update={
            "phases": {
                **base.phases,
                "development_analysis": analysis.model_copy(
                    update={
                        "decisions": {
                            **analysis.decisions,
                            "failed": PhaseDecisionRoute(
                                target="development",
                                reset_loop=False,
                                cycle_outcome="failed",
                            ),
                        }
                    }
                ),
            }
        }
    )
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 5},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=7200.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="failed"),
        policy,
        routing_timing=_rt(7200.0),
    )
    assert state.phase == "development_final_commit_cleanup"

    state = _drive_to_next_cycle(state, policy, elapsed=7200.0)

    assert state.phase == "failed_terminal"


def test_analysis_success_without_a_decision_starts_the_next_cycle() -> None:
    """Leaving analysis forward concludes the cycle even with no decision artifact."""
    policy = _policy()
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"development_analysis_iteration": 1},
    )

    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(100.0))
    assert state.phase == "development_final_commit_cleanup"

    state = _drive_to_next_cycle(state, policy, elapsed=100.0)

    assert state.phase == "planning"


def test_inactive_timer_does_not_guard_a_development_entry() -> None:
    """A stale consumed count must not redirect an entry made with no cycle running."""
    policy = _policy()
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        cycle_timebox_active=False,
        cycle_timebox_consumed_seconds=7200.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(7200.0),
    )

    assert state.phase == "development"


def test_timebox_redirect_completes_run_when_budget_is_spent() -> None:
    """A deadline-redirected cycle still ends the run once no dev cycles remain."""
    policy = _policy()
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 5},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=7200.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(7200.0),
    )
    assert state.phase == "development_final_commit_cleanup"

    state = _drive_to_next_cycle(state, policy, elapsed=7200.0)

    assert state.phase == "complete"


def test_timebox_finalization_outcome_is_policy_declared() -> None:
    """Declaring `failed` sends an out-of-budget timed-out cycle to the failure terminal."""
    from ralph.policy.models import CycleTimeboxPolicy

    base = _policy()
    assert base.cycle_timebox is not None
    policy = base.model_copy(
        update={
            "cycle_timebox": CycleTimeboxPolicy(
                **{
                    **base.cycle_timebox.model_dump(),
                    "finalization_cycle_outcome": "failed",
                }
            )
        }
    )
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 5},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=7200.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(7200.0),
    )
    state = _drive_to_next_cycle(state, policy, elapsed=7200.0)

    assert state.phase == "failed_terminal"


def test_resumed_state_without_a_recorded_outcome_starts_the_next_cycle() -> None:
    """A checkpoint written before cycle outcomes existed must not end the run early."""
    policy = _policy()
    state = PipelineState(
        phase="development_final_commit",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 2},
        pending_cycle_outcome=None,
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(0.0))

    assert state.phase == "planning"


def test_exhausted_analysis_loop_completes_run_when_budget_is_spent() -> None:
    """With no iteration budget left, the same path ends the run instead of looping."""
    policy = _policy()
    state = PipelineState(
        phase="development_commit",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 5},
        loop_iterations={"development_analysis_iteration": 5},
        last_execution_result_status="partial",
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(100.0))
    assert state.phase == "development_final_commit_cleanup"

    state = _drive_to_next_cycle(state, policy, elapsed=100.0)

    assert state.phase == "complete"
