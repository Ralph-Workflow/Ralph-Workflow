"""Live operator surface for the plan-to-final-commit cycle timebox.

The cycle deadline was previously invisible while a run was in flight: the
elapsed/remaining numbers appeared only in the end-of-run report, so an
operator watching the run could not tell how much of the cycle budget was
left, nor that a phase change had been forced by the deadline. These tests
pin the timebox status onto the live phase-transition banner.
"""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline import runner as runner_module
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULT_POLICY = load_policy(Path(__file__).parent.parent / "ralph" / "policy" / "defaults")

# 80% of the bundled 7200s cycle budget: the soft-warning point.
_WARNED_CONSUMED_SECONDS = 5760.0


def _banner_output(state: PipelineState, previous_phase: str) -> str:
    from tests._runner_phase_transition_banner_helper__stubdisplay import _StubDisplay

    display = _StubDisplay()
    runner_module.emit_phase_transition_if_changed(
        display,
        previous_phase,
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )
    return str(display._ctx.console.export_text())


def test_active_cycle_shows_elapsed_and_remaining_on_phase_banner() -> None:
    """An active cycle puts its consumed/remaining budget on every phase change."""
    state = PipelineState(
        phase="development",
        previous_phase="development_commit",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_WARNED_CONSUMED_SECONDS,
    )

    output = _banner_output(state, "development_commit")

    assert "cycle timebox" in output
    assert "96m/120m" in output
    assert "24m left" in output


def test_inactive_cycle_shows_no_timebox_item() -> None:
    """Before the cycle starts there is no deadline to report."""
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        cycle_timebox_active=False,
    )

    output = _banner_output(state, "planning")

    assert "cycle timebox" not in output


def test_deadline_redirect_is_reported_on_the_phase_banner() -> None:
    """A deadline-forced finalization says so where the operator can see it."""
    state = PipelineState(
        phase="development_final_commit_cleanup",
        previous_phase="development_analysis",
        cycle_timebox_active=False,
        cycle_timebox_consumed_seconds=7200.0,
        pending_cycle_outcome="completed",
        cycle_timebox_redirect_reason=(
            "cycle timebox reached 7200s (elapsed 7200s); "
            "redirecting to development_final_commit_cleanup"
        ),
    )

    output = _banner_output(state, "development_analysis")

    assert "cycle timebox" in output
    assert "deadline reached" in output


def test_redirect_notice_stops_once_the_redirected_cycle_is_committed() -> None:
    """The notice covers the wind-down, not the fresh cycle that follows it."""
    from ralph.pipeline.cycle_timing import RoutingTiming
    from ralph.pipeline.events import AnalysisDecisionEvent, PipelineEvent
    from ralph.pipeline.reducer import reduce

    policy = _DEFAULT_POLICY.pipeline
    timing = RoutingTiming(monotonic_now=7200.0, total_elapsed_seconds=7200.0)
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
        routing_timing=timing,
    )
    state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, routing_timing=timing)
    state, _ = reduce(state, PipelineEvent.COMMIT_SUCCESS, policy, routing_timing=timing)
    assert state.phase == "planning"

    output = _banner_output(state, "development_final_commit")

    assert "cycle timebox" not in output
