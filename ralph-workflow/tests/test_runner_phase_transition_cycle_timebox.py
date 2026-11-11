from __future__ import annotations

from pathlib import Path

from ralph.pipeline import runner as runner_module
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULT_POLICY = load_policy(Path(__file__).parent.parent / "ralph" / "policy" / "defaults")

_WARNED_CONSUMED_SECONDS = 28800.0


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
    state = PipelineState(
        phase="development",
        previous_phase="development_commit",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_WARNED_CONSUMED_SECONDS,
    )

    output = _banner_output(state, "development_commit")

    assert "cycle timebox" in output
    assert "480m/600m" in output
    assert "120m left" in output


def test_inactive_cycle_shows_no_timebox_item() -> None:
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        cycle_timebox_active=False,
    )

    output = _banner_output(state, "planning")

    assert "cycle timebox" not in output


def test_redirect_leaves_no_banner_item_because_the_operator_never_sees_one() -> None:
    from ralph.pipeline.cycle_timing import cycle_timebox_status_item

    state = PipelineState(
        phase="development_final_commit_review",
        previous_phase="development_analysis",
        cycle_timebox_active=False,
        cycle_timebox_consumed_seconds=36000.0,
        pending_cycle_outcome="completed",
        cycle_timebox_redirect_reason=(
            "cycle timebox reached 36000s (elapsed 36000s); "
            "redirecting to development_final_commit_review"
        ),
    )

    assert cycle_timebox_status_item(state, policy=_DEFAULT_POLICY.pipeline) is None
    assert "cycle timebox" not in _banner_output(state, "development_analysis")
