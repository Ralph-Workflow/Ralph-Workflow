"""Regression coverage for auto-integrate outcome observability."""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.pipeline import runner as runner_module
from ralph.pipeline.rebase_state import RebaseState


def test_non_landing_rebase_emits_warn() -> None:
    """Plan Step 3: a completed rebase that cannot land main is a warning."""
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
    """Plan Step 3: a landed integration remains an ordinary activity line."""
    display = MagicMock()

    runner_module._log_auto_integrate_outcome(
        display,
        RebaseState(last_action="rebased", last_target="main", fast_forwarded=True),
    )

    display.emit.assert_called_once()
    display.emit_warn_line.assert_not_called()
