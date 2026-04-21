"""Tests for per-unit git worktree lifecycle management."""

import subprocess
from pathlib import Path

import pytest

from ralph.git.worktree_manager import WorktreeExistsError, WorktreeManager
from ralph.process.manager import ProcessStatus, get_process_manager, reset_process_manager


def _fake_run_git_factory(repo_root: Path) -> callable:
    worktree_path = repo_root / ".worktrees" / "alpha"

    def fake_run_git(
        args: list[str],
        *,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["worktree", "add", "-b"]:
            worktree_path.mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(
                args=["git", *args], returncode=0, stdout="", stderr=""
            )

        if args[:3] == ["worktree", "list", "--porcelain"]:
            stdout = f"worktree {worktree_path}\n" if worktree_path.exists() else ""
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=0,
                stdout=stdout if capture_output else "",
                stderr="",
            )

        if args[:3] == ["worktree", "remove", "--force"]:
            if worktree_path.exists():
                worktree_path.rmdir()
            return subprocess.CompletedProcess(
                args=["git", *args], returncode=0, stdout="", stderr=""
            )

        raise AssertionError(f"Unexpected git args: {args}")

    return fake_run_git


def test_create_and_list_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = WorktreeManager(repo_root=tmp_path)
    fake_run_git = _fake_run_git_factory(tmp_path)
    monkeypatch.setattr(manager, "_run_git", fake_run_git)

    worktree_path = manager.create(unit_id="alpha", base_branch="main")

    assert worktree_path == tmp_path / ".worktrees" / "alpha"
    assert worktree_path.is_dir()
    assert manager.list() == ["alpha"]
    assert manager._branch_name("alpha") == "ralph/unit-alpha"


def test_create_raises_when_worktree_path_exists(tmp_path: Path) -> None:
    manager = WorktreeManager(repo_root=tmp_path)
    existing_path = tmp_path / ".worktrees" / "alpha"
    existing_path.mkdir(parents=True)

    with pytest.raises(WorktreeExistsError, match="alpha"):
        manager.create(unit_id="alpha", base_branch="main")


def test_destroy_removes_worktree_and_list_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = WorktreeManager(repo_root=tmp_path)
    monkeypatch.setattr(manager, "_run_git", _fake_run_git_factory(tmp_path))
    manager.create(unit_id="alpha", base_branch="main")

    manager.destroy(unit_id="alpha")

    assert not (tmp_path / ".worktrees" / "alpha").exists()
    assert manager.list() == []


def test_worktree_operations_emit_process_manager_events(tmp_git_repo: Path) -> None:
    """Real worktree add+remove produces SPAWNED->RUNNING->EXITED events per spawn."""
    reset_process_manager()
    events = []
    unsubscribe = get_process_manager().register_listener(events.append)

    try:
        manager = WorktreeManager(repo_root=tmp_git_repo)
        worktree_path = manager.create(unit_id="pm-test", base_branch="main")
        manager.destroy(unit_id="pm-test")
    finally:
        unsubscribe()
        reset_process_manager()

    assert not worktree_path.exists()

    worktree_labels = [
        e for e in events
        if e.record.label and e.record.label.startswith("git-worktree:")
    ]
    assert worktree_labels, "Expected events with label prefix 'git-worktree:'"

    exited = [e for e in worktree_labels if e.new_status == ProcessStatus.EXITED]
    assert exited, "Expected at least one EXITED event for a git-worktree spawn"
