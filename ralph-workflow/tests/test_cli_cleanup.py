"""Tests for the ralph cleanup CLI command."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

import ralph.cli.commands.cleanup
from ralph.cli.commands.cleanup import cleanup
from ralph.mcp.tools import exec_sandbox

if TYPE_CHECKING:
    from pathlib import Path

_app = typer.Typer()
_app.command()(cleanup)
runner = CliRunner()


@pytest.fixture(autouse=True)
def _redirect_exec_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "ralph.cli.commands.cleanup._get_private_exec_base",
        lambda: tmp_path / ".cache" / "ralph" / "exec",
    )


def test_cleanup_removes_worker_namespaces(tmp_path: Path) -> None:
    """With --force, removes stale .agent/workers/* directories."""
    workers_dir = tmp_path / ".agent" / "workers"
    (workers_dir / "unit-A").mkdir(parents=True)
    (workers_dir / "unit-B").mkdir(parents=True)

    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = runner.invoke(_app, ["--force"])

    assert result.exit_code == 0, result.output
    assert not (workers_dir / "unit-A").exists()
    assert not (workers_dir / "unit-B").exists()


def test_cleanup_dry_run_no_removal(tmp_path: Path) -> None:
    """With --dry-run, lists namespaces but does NOT remove them."""
    workers_dir = tmp_path / ".agent" / "workers"
    (workers_dir / "unit-A").mkdir(parents=True)
    (workers_dir / "unit-B").mkdir(parents=True)

    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = runner.invoke(_app, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert (workers_dir / "unit-A").exists()
    assert (workers_dir / "unit-B").exists()
    assert "unit-A" in result.output
    assert "unit-B" in result.output


def test_cleanup_empty_no_crash(tmp_path: Path) -> None:
    """No worker namespaces present → exits 0 with 'No stale worker namespaces found'."""
    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = runner.invoke(_app, [])

    assert result.exit_code == 0, result.output
    assert "No stale worker namespaces found" in result.output


def test_cleanup_no_workers_dir_no_crash(tmp_path: Path) -> None:
    """When .agent/workers/ does not exist, exits 0 cleanly."""
    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = runner.invoke(_app, [])

    assert result.exit_code == 0, result.output
    assert "No stale worker namespaces found" in result.output


def test_cleanup_aborted_without_force_or_dry_run(tmp_path: Path) -> None:
    """Without --force, prompts for confirmation; 'n' input aborts."""
    workers_dir = tmp_path / ".agent" / "workers"
    (workers_dir / "unit-A").mkdir(parents=True)

    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = runner.invoke(_app, [], input="n\n")

    assert result.exit_code == 0, result.output
    assert (workers_dir / "unit-A").exists()
    assert "Aborted" in result.output


def test_cleanup_force_removes_nested_contents(tmp_path: Path) -> None:
    """--force removes worker dirs that contain nested files."""
    workers_dir = tmp_path / ".agent" / "workers"
    unit_dir = workers_dir / "unit-A"
    (unit_dir / "artifacts").mkdir(parents=True)
    (unit_dir / "artifacts" / "plan.json").write_text("{}")

    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = runner.invoke(_app, ["--force"])

    assert result.exit_code == 0, result.output
    assert not unit_dir.exists()
    assert "Removed 1 stale worker namespace" in result.output


def test_cleanup_force_prunes_global_exec_cache(tmp_path: Path) -> None:
    exec_cache_base = tmp_path / ".cache" / "ralph" / "exec"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    key = exec_sandbox._workspace_key(workspace)
    slot_root = exec_cache_base / key / f"slot-{key}-0001"
    (slot_root / "ws").mkdir(parents=True)
    (slot_root / ".ralph-sandbox-ready").write_text('{"ready": true}', encoding="utf-8")
    current_pid, current_started_at = exec_sandbox._current_process_identity()
    payload: dict[str, int | float] = {"pid": current_pid}
    if current_started_at is not None:
        payload["started_at"] = current_started_at
    (slot_root / ".ralph-exec-owner.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    old_time = 1.0
    os.utime(slot_root, (old_time, old_time))
    os.utime(slot_root / "ws", (old_time, old_time))
    os.utime(slot_root / ".ralph-sandbox-ready", (old_time, old_time))
    os.utime(slot_root / ".ralph-exec-owner.json", (old_time, old_time))

    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = runner.invoke(_app, ["--force"])

    assert result.exit_code == 0, result.output
    assert not slot_root.exists()
    assert "Pruned stale exec cache entries" in result.output


def test_cleanup_does_not_touch_worktrees_directory(tmp_path: Path) -> None:
    """Regression guard: cleanup must never remove a sibling .worktrees/ directory.

    v1 does not use git per-worker checkouts, so cleanup must only operate on
    .agent/workers/ and must leave any other directory untouched.
    """
    worktrees_dir = tmp_path / ".worktrees" / "unit-a"
    worktrees_dir.mkdir(parents=True)

    with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
        result = runner.invoke(_app, ["--force"])

    assert result.exit_code == 0, result.output
    assert worktrees_dir.exists(), (
        ".worktrees/unit-a must not be touched by cleanup — it is not a supported v1 concept"
    )
    assert "No stale worker namespaces found" in result.output


def test_cleanup_outside_git_repo_exits_1(tmp_path: Path) -> None:
    """Running cleanup in a non-git directory must exit with code 1 and an error message."""

    def _raise_not_in_git() -> None:
        raise RuntimeError("not a git repository")

    with patch("ralph.cli.commands.cleanup.find_repo_root", side_effect=_raise_not_in_git):
        result = runner.invoke(_app, [])

    assert result.exit_code == 1, f"Expected exit 1 for non-git dir, got {result.exit_code}"
    assert "not in a git repository" in result.output.lower() or "error" in result.output.lower()


class TestCleanupNeverInvokesGit:
    """Regression guardrails: cleanup must never shell out to git."""

    def test_cleanup_does_not_shell_out_to_git(self, tmp_path: Path, monkeypatch: object) -> None:
        """cleanup --force must remove worker dirs without invoking any git process.

        Monkeypatches all subprocess entry points and asserts none were called.
        """

        invocations: list[tuple[object, ...]] = []

        def _record(*args: object, **kwargs: object) -> object:
            invocations.append(args)
            raise AssertionError(
                f"cleanup must not invoke subprocess: args={args!r} kwargs={kwargs!r}"
            )

        mp = pytest.MonkeyPatch()
        mp.setattr("ralph.git.subprocess_runner.run_git", _record, raising=False)
        mp.setattr(subprocess, "run", _record)
        mp.setattr(subprocess, "Popen", _record)

        workers_dir = tmp_path / ".agent" / "workers"
        (workers_dir / "unit-a").mkdir(parents=True)
        (workers_dir / "unit-b").mkdir(parents=True)

        try:
            with patch("ralph.cli.commands.cleanup.find_repo_root", return_value=tmp_path):
                result = runner.invoke(_app, ["--force"])
        finally:
            mp.undo()

        assert result.exit_code == 0, f"cleanup exited {result.exit_code}: {result.output}"
        assert not (workers_dir / "unit-a").exists(), "unit-a must have been removed"
        assert not (workers_dir / "unit-b").exists(), "unit-b must have been removed"
        assert invocations == [], (
            f"cleanup must not invoke any subprocess, but these were recorded: {invocations!r}"
        )

    def test_cleanup_source_does_not_reference_worktree_or_subprocess_git(self) -> None:
        """Static source check: cleanup.py must not contain 'worktree' or subprocess git calls."""
        source = inspect.getsource(ralph.cli.commands.cleanup)
        assert "worktree" not in source, (
            "cleanup.py must not reference 'worktree' — "
            "v1 cleanup only removes .agent/workers/<unit_id>/ namespaces."
        )
        # Must not shell out via subprocess — cleanup uses only shutil.rmtree and Path
        assert "subprocess" not in source, (
            "cleanup.py must not import or call subprocess — "
            "cleanup must use only filesystem operations (shutil/Path), never subprocess."
        )
        assert "git worktree" not in source, (
            "cleanup.py must not contain 'git worktree' — "
            "cleanup is not allowed to invoke git worktree commands."
        )
