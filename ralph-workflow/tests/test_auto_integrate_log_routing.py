from __future__ import annotations

from unittest.mock import MagicMock

from ralph.display.auto_integrate_message import format_auto_integrate_message
from ralph.pipeline import runner as runner_module
from ralph.pipeline.rebase_state import RebaseState


def test_non_landing_rebase_emits_warn() -> None:
    display = MagicMock()

    runner_module._log_auto_integrate_outcome(
        display,
        RebaseState(
            last_action="rebased",
            last_target="main",
            last_reason="target worktree dirty",
            fast_forwarded=False,
        ),
    )

    display.emit_warn_line.assert_called_once()
    _, _, message = display.emit_warn_line.call_args.args
    assert "target worktree dirty" in message
    display.emit.assert_not_called()


def test_clean_success_stays_info() -> None:
    display = MagicMock()

    runner_module._log_auto_integrate_outcome(
        display,
        RebaseState(last_action="rebased", last_target="main", fast_forwarded=True),
    )

    display.emit.assert_called_once()
    display.emit_warn_line.assert_not_called()


def test_stale_target_refresh_is_visible_in_the_auto_integrate_line() -> None:
    display = MagicMock()

    runner_module._log_auto_integrate_outcome(
        display,
        RebaseState(
            last_action="rebased",
            last_target="main",
            fast_forwarded=True,
            last_refresh="origin unreachable",
        ),
    )

    message = format_auto_integrate_message(
        "rebased",
        "main",
        None,
        fast_forwarded=True,
        refresh="origin unreachable",
    )
    assert "origin unreachable" in message
    assert "fast-forwarded main" in message
    assert (
        format_auto_integrate_message("rebased", "main", None, fast_forwarded=True)
        == "rebased onto target (main), fast-forwarded main"
    )
