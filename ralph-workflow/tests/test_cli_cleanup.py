"""Tests for the ralph cleanup CLI command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import typer
from typer.testing import CliRunner

from ralph.cli.commands.cleanup import cleanup

if TYPE_CHECKING:
    from pathlib import Path

_app = typer.Typer()
_app.command()(cleanup)
runner = CliRunner()


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

    with patch(
        "ralph.cli.commands.cleanup.find_repo_root", side_effect=_raise_not_in_git
    ):
        result = runner.invoke(_app, [])

    assert result.exit_code == 1, f"Expected exit 1 for non-git dir, got {result.exit_code}"
    assert "not in a git repository" in result.output.lower() or "error" in result.output.lower()
