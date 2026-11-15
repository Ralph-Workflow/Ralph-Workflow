from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ralph.display.auto_integrate_message import format_auto_integrate_message
from ralph.pipeline import runner as runner_module
from ralph.pipeline.rebase_state import RebaseState


def test_non_landing_rebase_emits_warn(tmp_path: Path) -> None:
    display = MagicMock()

    runner_module.log_auto_integrate_outcome(
        display,
        RebaseState(
            last_action="rebased",
            last_target="main",
            last_reason="target worktree dirty",
            fast_forwarded=False,
        ),
        root=tmp_path,
    )

    display.emit_warn_line.assert_called_once()
    _, _, message = display.emit_warn_line.call_args.args
    assert "target worktree dirty" in message
    display.emit.assert_not_called()


def test_clean_success_stays_info(tmp_path: Path) -> None:
    display = MagicMock()

    runner_module.log_auto_integrate_outcome(
        display,
        RebaseState(last_action="rebased", last_target="main", fast_forwarded=True),
        root=tmp_path,
    )

    display.emit.assert_called_once()
    display.emit_warn_line.assert_not_called()


def test_stale_target_refresh_is_visible_in_the_auto_integrate_line(tmp_path: Path) -> None:
    display = MagicMock()

    runner_module.log_auto_integrate_outcome(
        display,
        RebaseState(
            last_action="rebased",
            last_target="main",
            fast_forwarded=True,
            last_refresh="origin unreachable",
        ),
        root=tmp_path,
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


def test_a_repeated_identical_skip_line_for_one_worktree_is_emitted_once(tmp_path: Path) -> None:
    display = MagicMock()
    skipped = RebaseState(
        last_action="skipped",
        last_target="main",
        last_reason="retained resolution is still owned by live Git state; replay was not started",
    )

    runner_module.log_auto_integrate_outcome(display, skipped, root=tmp_path / "a")
    runner_module.log_auto_integrate_outcome(display, skipped, root=tmp_path / "a")
    runner_module.log_auto_integrate_outcome(display, skipped, root=tmp_path / "b")

    assert display.emit_warn_line.call_count == 2
