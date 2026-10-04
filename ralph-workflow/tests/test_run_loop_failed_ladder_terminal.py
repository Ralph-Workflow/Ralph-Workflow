"""Regression coverage for the conflict-ladder-only terminal exit."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ralph.pipeline import run_loop
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState


def test_exhausted_conflict_ladder_exits_once_before_ordinary_dispatch() -> None:
    """The generic failed-route recovery remains elsewhere; this case stops here."""
    display = MagicMock()
    policy = MagicMock()
    policy.terminal_phase = "complete"
    policy.recovery.failed_route = "failed_terminal"
    policy.cycle_timebox = None
    ctx = SimpleNamespace(
        policy_bundle=SimpleNamespace(pipeline=policy),
        active_display=display,
    )
    state = PipelineState(
        phase="failed_terminal",
        rebase=RebaseState(
            resolution_exhausted=True,
            conflict_strategies_tried=(
                "rebase_resolver: unresolved a.py",
                "merge_instead: unresolved a.py",
            ),
        ),
    )

    result, previous_phase, exit_code = run_loop._run_inner_loop_after_startup(
        state, ctx, "development"
    )

    assert exit_code == 1
    assert previous_phase == "development"
    assert result.phase == "failed_terminal"
    assert "rebase_resolver: unresolved a.py" in result.last_error
    assert "merge_instead: unresolved a.py" in result.last_error
    display.emit.assert_called_once()
