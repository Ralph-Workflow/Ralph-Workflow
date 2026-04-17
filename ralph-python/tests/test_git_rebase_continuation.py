"""Tests for the rebase continuation helpers."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.git.rebase import rebase_continuation as continuation_module
from ralph.git.rebase.rebase_continuation import (
    ConflictRemainingError,
    NoRebaseInProgressError,
    continue_rebase_at,
    rebase_in_progress_at,
    verify_rebase_completed_at,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_continue_rebase_finishes_conflicted_rebase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Continuing after resolving conflicts completes the rebase."""

    state = {"in_progress": True}
    fake_repo = SimpleNamespace(
        head=SimpleNamespace(is_detached=False),
        commit=lambda _branch: object(),
    )

    monkeypatch.setattr(continuation_module, "_open_repo", lambda _repo_root: fake_repo)
    monkeypatch.setattr(
        continuation_module,
        "_rebase_in_progress_impl",
        lambda _repo: state["in_progress"],
    )
    monkeypatch.setattr(continuation_module, "_has_index_conflicts", lambda _repo: False)
    monkeypatch.setattr(continuation_module, "_head_is_descendant", lambda *_args: True)

    def fake_run(*_args, **_kwargs) -> subprocess.CompletedProcess[str]:
        state["in_progress"] = False
        return subprocess.CompletedProcess(["git", "rebase", "--continue"], 0, "", "")

    monkeypatch.setattr(continuation_module.subprocess, "run", fake_run)

    assert rebase_in_progress_at(tmp_path)
    assert not verify_rebase_completed_at(tmp_path, "main")

    continue_rebase_at(tmp_path)

    assert not rebase_in_progress_at(tmp_path)
    assert verify_rebase_completed_at(tmp_path, "main")


def test_continue_rebase_requires_clean_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The continuation helper refuses to run until conflicts are resolved."""

    monkeypatch.setattr(
        continuation_module,
        "_open_repo",
        lambda _repo_root: SimpleNamespace(head=SimpleNamespace(is_detached=False)),
    )
    monkeypatch.setattr(continuation_module, "_rebase_in_progress_impl", lambda _repo: True)
    monkeypatch.setattr(continuation_module, "_has_index_conflicts", lambda _repo: True)

    with pytest.raises(ConflictRemainingError):
        continue_rebase_at(tmp_path)

    assert rebase_in_progress_at(tmp_path)


def test_continue_rebase_requires_active_rebase(tmp_git_repo: Path) -> None:
    """Continuing without a rebase in progress is an error."""

    with pytest.raises(NoRebaseInProgressError):
        continue_rebase_at(tmp_git_repo)


def _setup_conflicted_rebase(repo_root: Path, feature_branch: str = "feature") -> str:
    repo = Repo(repo_root)
    base_branch = repo.active_branch.name
    conflict_file = repo_root / "conflict.txt"

    conflict_file.write_text("base\n")
    repo.index.add(["conflict.txt"])
    repo.index.commit("add conflict file")

    repo.git.checkout("-b", feature_branch)
    conflict_file.write_text("feature\n")
    repo.index.add(["conflict.txt"])
    repo.index.commit("feature change")

    repo.git.checkout(base_branch)
    conflict_file.write_text("main\n")
    repo.index.add(["conflict.txt"])
    repo.index.commit("main change")

    repo.git.checkout(feature_branch)
    result = subprocess.run(
        ["git", "rebase", base_branch],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0, "Expected the rebase command to conflict"

    return base_branch


def _resolve_conflict(repo_root: Path) -> None:
    conflict_file = repo_root / "conflict.txt"
    conflict_file.write_text("resolved\n")
    subprocess.run(
        ["git", "add", "conflict.txt"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
