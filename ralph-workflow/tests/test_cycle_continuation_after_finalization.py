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
    return RoutingTiming(total_elapsed_seconds=elapsed)


def _drive_to_next_cycle(
    state: PipelineState,
    policy: PipelinePolicy,
    *,
    elapsed: float,
) -> PipelineState:
    assert state.phase == "development_final_commit_review"
    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(elapsed))
    assert state.phase == "development_final_commit"
    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(elapsed))
    return state


def test_exhausted_analysis_loop_starts_next_cycle_when_budget_remains() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development_commit",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"development_analysis_iteration": 5},
        last_execution_result_status="partial",
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(100.0))
    assert state.phase == "development_final_commit_review"

    state = _drive_to_next_cycle(state, policy, elapsed=100.0)

    assert state.phase == "planning"


def test_timebox_redirect_starts_next_cycle_when_budget_remains() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=36000.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(36000.0),
    )
    assert state.phase == "development_final_commit_review"

    state = _drive_to_next_cycle(state, policy, elapsed=36000.0)

    assert state.phase == "planning"


def test_concluded_cycle_does_not_redirect_a_later_development_entry() -> None:
    policy = _policy()
    state = PipelineState(
        phase="planning_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        cycle_timebox_active=False,
        cycle_timebox_consumed_seconds=36000.0,
    )

    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(36000.0))

    assert state.phase == "development"
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 0.0


def test_recorded_failure_outranks_a_deadline_redirect() -> None:
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
        cycle_timebox_consumed_seconds=36000.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="failed"),
        policy,
        routing_timing=_rt(36000.0),
    )
    assert state.phase == "development_final_commit_review"

    state = _drive_to_next_cycle(state, policy, elapsed=36000.0)

    assert state.phase == "failed_terminal"


def test_analysis_success_without_a_decision_starts_the_next_cycle() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"development_analysis_iteration": 1},
    )

    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(100.0))
    assert state.phase == "development_final_commit_review"

    state = _drive_to_next_cycle(state, policy, elapsed=100.0)

    assert state.phase == "planning"


def test_inactive_timer_does_not_guard_a_development_entry() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        cycle_timebox_active=False,
        cycle_timebox_consumed_seconds=36000.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(36000.0),
    )

    assert state.phase == "development"


def test_timebox_redirect_completes_run_when_budget_is_spent() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 5},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=36000.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(36000.0),
    )
    assert state.phase == "development_final_commit_review"

    state = _drive_to_next_cycle(state, policy, elapsed=36000.0)

    assert state.phase == "complete"


def test_timebox_finalization_outcome_is_policy_declared() -> None:
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
        cycle_timebox_consumed_seconds=36000.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(36000.0),
    )
    state = _drive_to_next_cycle(state, policy, elapsed=36000.0)

    assert state.phase == "failed_terminal"


def test_resumed_state_without_a_recorded_outcome_starts_the_next_cycle() -> None:
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
    policy = _policy()
    state = PipelineState(
        phase="development_commit",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 5},
        loop_iterations={"development_analysis_iteration": 5},
        last_execution_result_status="partial",
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=_rt(100.0))
    assert state.phase == "development_final_commit_review"

    state = _drive_to_next_cycle(state, policy, elapsed=100.0)

    assert state.phase == "complete"


def test_cycle_is_timed_even_when_planning_analysis_is_skipped() -> None:
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
    from ralph.pipeline.run_time_report import emit_run_time_report

    policy = _policy()
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 5},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=36000.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(36000.0),
    )
    state = _drive_to_next_cycle(state, policy, elapsed=36000.0)

    emit_run_time_report(
        tmp_path,
        state=state,
        outcome="completed",
        elapsed_seconds=36000.0,
        cycle_timebox=policy.cycle_timebox,
    )
    report = (tmp_path / ".agent" / "artifacts" / "run_time_report.md").read_text(encoding="utf-8")

    assert "[CT-2] Redirected cycles: 1;" in report
    assert "cycle timebox reached" in report
    assert "redirecting to development_final_commit_review" in report


def test_bypassed_analysis_into_finalization_preserves_the_cycle() -> None:
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

    assert state.phase == "development_final_commit_review"
    assert state.cycle_timebox_active is True


def test_next_cycle_after_a_bypassed_finalization_reaches_development() -> None:
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
    policy = _policy()
    state = PipelineState(
        phase="planning_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        loop_iterations={"planning_analysis_iteration": 3},
        cycle_timebox_active=False,
        cycle_timebox_consumed_seconds=36000.0,
    )

    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(36000.0))

    assert state.phase == "development"
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 0.0


def test_skipping_final_commit_does_not_reset_the_cycle_timer() -> None:
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
    assert state.cycle_timebox_active is True


def test_intermediate_commit_does_not_destroy_a_recorded_verdict() -> None:
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
    assert state.phase == "development_final_commit_review"

    state = _drive_to_next_cycle(state, policy, elapsed=100.0)

    assert state.phase == "failed_terminal"


def test_the_recovery_failed_route_keeps_the_cycle_timer_armed() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development_commit_review",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=1200.0,
    )

    state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy, routing_timing=_rt(1200.0))

    assert state.phase in {"failed_terminal", "development_commit_review"}
    assert state.cycle_timebox_active is True


def test_a_decision_that_skips_final_commit_preserves_the_timer() -> None:
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
    assert state.cycle_timebox_active is True


def test_a_loopback_that_skips_final_commit_preserves_the_timer() -> None:
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
    assert state.cycle_timebox_active is True


def test_commit_review_loop_cap_still_counts_after_a_charged_analysis_cycle() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development_final_commit_review",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        execution_cycle_charged=True,
    )

    for _ in range(4):
        state, _ = reduce(state, PipelineEvent.PHASE_LOOPBACK, policy, routing_timing=_rt(10.0))
        if state.phase != "development_final_commit_review":
            break

    assert state.phase == "development_final_commit", (
        "the cleanup cap must still release the run after its declared maximum"
    )


def test_a_redirect_reason_does_not_ride_into_the_next_cycle() -> None:
    policy = _policy()
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 1},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=36000.0,
    )

    state, _ = reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        policy,
        routing_timing=_rt(36000.0),
    )
    assert state.cycle_timebox_redirect_reason is not None
    state = _drive_to_next_cycle(state, policy, elapsed=36000.0)

    assert state.phase == "planning"
    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(0.0))
    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=_rt(0.0))

    assert state.phase == "development"
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_redirect_reason is None
    assert state.cycle_timebox_redirects == 1
