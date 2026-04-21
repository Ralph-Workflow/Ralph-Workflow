"""Tests for the ralph cleanup CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import typer
from typer.testing import CliRunner

from ralph.cli.commands.cleanup import cleanup
from ralph.git.subprocess_runner import GitRunResult
from ralph.git.worktree_manager import WorktreeManager
from ralph.process.manager import ProcessStatus, get_process_manager, reset_process_manager

if TYPE_CHECKING:
    from pathlib import Path

_app = typer.Typer()
_app.command()(cleanup)
runner = CliRunner()
EXPECTED_GIT_CALLS = 2

_EMPTY_GIT_RESULT = GitRunResult(
    args=("git", "branch", "-D", "branch"), returncode=0, stdout="", stderr=""
)


def test_cleanup_removes_worktrees(tmp_path: Path) -> None:
    """With --force, removes worktree dirs and deletes tracking branches."""
    worktrees = tmp_path / ".worktrees"
    (worktrees / "unit-A").mkdir(parents=True)
    (worktrees / "unit-B").mkdir(parents=True)

    with (
        patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path),
        patch("ralph.cli.commands.cleanup.WorktreeManager") as mock_wm_cls,
        patch(
            "ralph.cli.commands.cleanup.run_git", return_value=_EMPTY_GIT_RESULT
        ) as mock_run_git,
    ):
        mock_manager = mock_wm_cls.return_value

        def _fake_destroy(unit_id: str) -> None:
            worktree_path = worktrees / unit_id
            if worktree_path.exists():
                worktree_path.rmdir()

        mock_manager.destroy.side_effect = _fake_destroy

        result = runner.invoke(_app, ["--force"])

    assert result.exit_code == 0, result.output
    assert not (worktrees / "unit-A").exists()
    assert not (worktrees / "unit-B").exists()
    mock_manager.destroy.assert_any_call("unit-A")
    mock_manager.destroy.assert_any_call("unit-B")
    assert mock_run_git.call_count == EXPECTED_GIT_CALLS


def test_cleanup_dry_run_no_removal(tmp_path: Path) -> None:
    """With --dry-run, lists worktrees but does NOT remove them."""
    worktrees = tmp_path / ".worktrees"
    (worktrees / "unit-A").mkdir(parents=True)
    (worktrees / "unit-B").mkdir(parents=True)

    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = runner.invoke(_app, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert (worktrees / "unit-A").exists()
    assert (worktrees / "unit-B").exists()
    assert "unit-A" in result.output
    assert "unit-B" in result.output


def test_cleanup_empty_no_crash(tmp_path: Path) -> None:
    """No worktrees present → exits 0 with 'No orphaned worktrees found'."""
    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = runner.invoke(_app, [])

    assert result.exit_code == 0, result.output
    assert "No orphaned worktrees found" in result.output


def _assert_full_lifecycle(events: list, label: str) -> None:
    """Assert each PID with the given label emitted SPAWNED->RUNNING->EXITED."""
    labeled = [e for e in events if e.record.label == label]
    assert labeled, f"Expected events with label '{label}'"

    pids = dict.fromkeys(e.record.pid for e in labeled)
    assert pids, f"Expected at least one tracked spawn with label '{label}'"

    for pid in pids:
        pid_events = [e for e in labeled if e.record.pid == pid]
        transitions = [(e.previous_status, e.new_status) for e in pid_events]
        assert (ProcessStatus.SPAWNED, ProcessStatus.RUNNING) in transitions, (
            f"Process {pid} (label {label!r}) missing SPAWNED->RUNNING; "
            f"got {transitions}"
        )
        assert (ProcessStatus.RUNNING, ProcessStatus.EXITED) in transitions, (
            f"Process {pid} (label {label!r}) missing RUNNING->EXITED; "
            f"got {transitions}"
        )


def test_cleanup_command_emits_git_cleanup_lifecycle_events(tmp_git_repo: Path) -> None:
    """cleanup CLI command routes branch delete through ProcessManager with full lifecycle.

    Invokes the public cleanup command (not _delete_branch directly), mocking
    WorktreeManager.destroy to isolate the branch-delete path and assert that the
    'git-cleanup' labeled git child goes through the full SPAWNED->RUNNING->EXITED sequence.
    """
    worktrees_dir = tmp_git_repo / ".worktrees"
    (worktrees_dir / "unit-pm-test").mkdir(parents=True)

    reset_process_manager()
    events: list = []
    unsubscribe = get_process_manager().register_listener(events.append)

    try:
        with (
            patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_git_repo),
            patch.object(WorktreeManager, "destroy"),
        ):
            result = runner.invoke(_app, ["--force"])
    finally:
        unsubscribe()
        reset_process_manager()

    assert result.exit_code == 0, result.output
    _assert_full_lifecycle(events, "git-cleanup")
