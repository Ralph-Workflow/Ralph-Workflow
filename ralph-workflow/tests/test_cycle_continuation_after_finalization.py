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

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.cycle_timing import RoutingTiming
from ralph.pipeline.events import AnalysisDecisionEvent, PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


@lru_cache(maxsize=1)
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


def test_cycle_is_timed_even_when_planning_analysis_is_skipped() -> None:
    """A skipped planning-analysis must not leave the whole cycle untimed.

    The timer is bound to the declared `planning_analysis -> development`
    edge so unrelated routes cannot restart it. When that phase is skipped
    because its loop budget is spent, the run still enters development to
    begin a cycle — and that cycle has to be on the clock like any other.
    """
    policy = _policy()
    state = PipelineState(
        phase="planning",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"planning_analysis_iteration": 3},
    )

    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(0.0))

    assert state.phase == "development"
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 0.0


def test_redirect_reason_survives_to_the_run_time_report(tmp_path: Path) -> None:
    """The operator must still be told a deadline forced the finalization.

    The reason is the only record of WHY the cycle ended where it did, and the
    report reads it off the final state — so clearing it at the commit that
    concludes the redirected cycle would erase it one step before it is read.
    """
    from ralph.pipeline.run_time_report import emit_run_time_report

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
    state = _drive_to_next_cycle(state, policy, elapsed=7200.0)

    # Emitted, not merely rendered: the report is validated against a CLOSED
    # markdown spec on the way out, and an undeclared section fails that
    # validation and discards the whole report — so a renderer-only assertion
    # passes while the operator receives nothing.
    emit_run_time_report(
        tmp_path,
        state=state,
        outcome="completed",
        elapsed_seconds=7200.0,
        cycle_timebox=policy.cycle_timebox,
    )
    report = (tmp_path / ".agent" / "artifacts" / "run_time_report.md").read_text(
        encoding="utf-8"
    )

    assert "[CT-2] Redirected cycles: 1;" in report
    assert "cycle timebox reached" in report
    # The reason ends with the phase the cycle was redirected to, so a report
    # that truncates it names a phase that does not exist.
    assert "redirecting to development_final_commit_cleanup" in report


def test_bypassed_analysis_into_finalization_ends_the_cycle() -> None:
    """Reaching the final-commit path via a bypass must end the cycle timer.

    The timebox is applied to the pending target, which a bypass can then
    rewrite to the finalization entry. Missing that rewrite left the timer
    running, so the NEXT cycle inherited a spent clock and had its first
    development entry redirected immediately — burning a dev cycle without
    doing any development.
    """
    policy = _policy()
    state = PipelineState(
        phase="development_commit",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"development_analysis_iteration": 5},
        last_execution_result_status="partial",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=3600.0,
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(3600.0))

    assert state.phase == "development_final_commit_cleanup"
    assert state.cycle_timebox_active is False


def test_next_cycle_after_a_bypassed_finalization_reaches_development() -> None:
    """The cycle that follows must get its own full budget, not the leftovers."""
    policy = _policy()
    state = PipelineState(
        phase="development_commit",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"development_analysis_iteration": 5},
        last_execution_result_status="partial",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=3600.0,
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(3600.0))
    state = _drive_to_next_cycle(state, policy, elapsed=3600.0)
    assert state.phase == "planning"

    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(3600.0))
    assert state.phase == "planning_analysis"
    state, _ = reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy, routing_timing=_rt(3600.0))

    assert state.phase == "development"
    assert state.cycle_timebox_consumed_seconds == 0.0


def test_bypassed_start_edge_does_not_redirect_the_cycle_it_just_started() -> None:
    """A cycle that starts on this very transition must not be judged as expired.

    When the start source is itself skipped for a spent loop budget, the timer
    starts and its consumed count resets to zero — but the elapsed seconds
    sampled for this step still describe the PREVIOUS cycle. Judging the new
    cycle against them redirects it instantly, burning a dev cycle that never
    ran any development.
    """
    policy = _policy()
    state = PipelineState(
        phase="planning_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"planning_analysis_iteration": 3},
        cycle_timebox_active=False,
        cycle_timebox_consumed_seconds=7200.0,
    )

    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(7200.0))

    assert state.phase == "development"
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 0.0


def test_a_completed_cycle_ends_its_timer_even_when_finalization_is_skipped() -> None:
    """Completing a cycle ends its clock, however the run got there.

    The timer's only end used to be ENTRY to the finalization path, but a
    result-status override or an agent-chain fallback can route straight past
    it into the next cycle. The timer then never restarted (a start requires
    an inactive cycle), so the next cycle inherited a spent clock and was
    redirected before doing any development.
    """
    base = _policy()
    development = base.phases["development"]
    policy = base.model_copy(
        update={
            "phases": {
                **base.phases,
                "development": development.model_copy(
                    update={"result_status_post_commit": {"partial": "planning"}}
                ),
            }
        }
    )
    state = PipelineState(
        phase="development_commit",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        post_commit_phase_override="planning",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=6000.0,
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(6000.0))

    assert state.phase == "planning"
    assert state.cycle_timebox_active is False


def test_intermediate_commit_does_not_destroy_a_recorded_verdict() -> None:
    """Only the commit that closes a cycle consumes that cycle's verdict.

    A graph that reviews before committing records `failed` and then passes
    through an intermediate commit on its way to the final one. Clearing the
    verdict at every commit-role phase threw it away, and the outcome-less
    cycle was then routed as `completed` — a failed run reporting success.
    """
    policy = _policy()
    state = PipelineState(
        phase="development_commit",
        budget_caps={"iteration": 1},
        outer_progress={"iteration": 0},
        pending_cycle_outcome="failed",
        last_execution_result_status="failed",
        loop_iterations={"development_analysis_iteration": 5},
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(100.0))
    assert state.phase == "development_final_commit_cleanup"

    state = _drive_to_next_cycle(state, policy, elapsed=100.0)

    assert state.phase == "failed_terminal"


def test_the_recovery_failed_route_keeps_the_cycle_timer_armed() -> None:
    """The recovery failed route is a terminal here but does not end the run.

    The effect router turns it back into the phase the run was in, so the run
    re-enters the same cycle. Disarming the timer on the way through left the
    rest of that cycle unbounded, with no way to re-arm — starting a timer
    requires an inactive one. A run that really does die inside its cycle is
    handled by the report, which describes the cycle as open at exit rather
    than as still running.
    """
    policy = _policy()
    state = PipelineState(
        phase="development_commit_cleanup",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=1200.0,
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy, routing_timing=_rt(1200.0))

    assert state.phase in {"failed_terminal", "development_commit_cleanup"}
    assert state.cycle_timebox_active is True


def test_a_decision_out_of_the_cycle_ends_the_timer() -> None:
    """Any route out of the cycle ends its clock, including an analysis decision.

    Wiring the conclusion at individual call sites leaves whichever paths
    nobody thought of still leaking: a decision that abandons the cycle back
    to planning kept the timer running, so the next cycle was redirected on
    the previous one's clock and burned a budget cycle doing nothing.
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
                            "request_changes": PhaseDecisionRoute(
                                target="planning", reset_loop=True
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
        outer_progress={"iteration": 1},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=3000.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(3000.0),
    )

    assert state.phase == "planning"
    assert state.cycle_timebox_active is False


def test_a_loopback_out_of_the_cycle_ends_the_timer() -> None:
    """The same holds for a plain transition that leaves the cycle."""
    base = _policy()
    development = base.phases["development"]
    policy = base.model_copy(
        update={
            "phases": {
                **base.phases,
                "development": development.model_copy(
                    update={
                        "transitions": development.transitions.model_copy(
                            update={"on_loopback": "planning"}
                        )
                    }
                ),
            }
        }
    )
    state = PipelineState(
        phase="development",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=3000.0,
    )

    state, _ = reduce(state, PipelineEvent.PHASE_LOOPBACK, policy, routing_timing=_rt(3000.0))

    assert state.phase == "planning"
    assert state.cycle_timebox_active is False


def test_commit_cleanup_loop_cap_still_counts_after_a_charged_analysis_cycle() -> None:
    """A cleanup loop must remain bounded however the cycle got there.

    The charged-cycle flag is set when the invocation gate admits analysis and
    cleared only on entry to an execution phase, so it is still set at the
    cleanup phase later in the same cycle. Suppressing the charge there froze
    that phase's own counter, its cap never fired, and a cleanup that kept
    asking to loop looped forever — with the cycle timer already concluded, so
    nothing could bound it.
    """
    policy = _policy()
    state = PipelineState(
        phase="development_final_commit_cleanup",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        execution_cycle_charged=True,
    )

    for _ in range(4):
        state, _ = reduce(state, PipelineEvent.PHASE_LOOPBACK, policy, routing_timing=_rt(10.0))
        if state.phase != "development_final_commit_cleanup":
            break

    assert state.phase == "development_final_commit", (
        "the cleanup cap must still release the run after its declared maximum"
    )


def test_a_redirect_reason_does_not_ride_into_the_next_cycle() -> None:
    """The reason belongs to the cycle that earned it.

    A redirected cycle is normally followed by another, and a reason carried
    across would make the fresh cycle's report claim a deadline it never hit.
    Driven end to end because a hand-built state cannot show the clearing.
    """
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
    assert state.cycle_timebox_redirect_reason is not None
    state = _drive_to_next_cycle(state, policy, elapsed=7200.0)

    # Plan the next cycle and enter development, starting a fresh timer.
    assert state.phase == "planning"
    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(0.0))
    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(0.0))

    assert state.phase == "development"
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_redirect_reason is None
    # The running total still records that an earlier cycle was redirected.
    assert state.cycle_timebox_redirects == 1
