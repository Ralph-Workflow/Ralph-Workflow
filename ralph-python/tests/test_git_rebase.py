"""Behavioral tests for git rebase helpers."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Sequence
from typing import Mapping

import pytest
from git import GitCommandError, Repo

from ralph.git.rebase.rebase import (
    ProcessExecutor,
    ProcessResult,
    RebaseConflicts,
    RebaseFailed,
    RebaseNoOp,
    RebaseOperationError,
    RebaseSuccess,
    abort_rebase,
    continue_rebase,
    get_conflicted_files,
    rebase_onto,
)


class FakeProcessExecutor(ProcessExecutor):
    def __init__(self, responses: Mapping[tuple[str, tuple[str, ...]], ProcessResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def execute(
        self,
        command: str,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        key = (command, tuple(args))
        self.calls.append(key)
        return self.responses.get(key, ProcessResult(returncode=0, stdout="", stderr=""))


def _mk_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> ProcessResult:
    return ProcessResult(returncode=returncode, stdout=stdout, stderr=stderr)


def _create_rebase_state(repo_root: Path) -> None:
    git_dir = Path(repo_root, ".git")
    (git_dir / "rebase-apply").mkdir(parents=True, exist_ok=True)


def test_abort_rebase_requires_rebase_state(tmp_git_repo: Path) -> None:
    with pytest.raises(RebaseOperationError, match="rebase in progress"):
        abort_rebase(repo_root=tmp_git_repo)


def test_abort_rebase_invokes_git_when_rebase_in_progress(tmp_git_repo: Path) -> None:
    _create_rebase_state(tmp_git_repo)
    responses = {
        ("git", ("rebase", "--abort")): _mk_result(),
    }
    executor = FakeProcessExecutor(responses)

    abort_rebase(repo_root=tmp_git_repo, executor=executor)

    assert executor.calls == [("git", ("rebase", "--abort"))]


def test_continue_rebase_requires_conflicts_resolved(monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path) -> None:
    _create_rebase_state(tmp_git_repo)
    monkeypatch.setattr(
        "ralph.git.rebase.rebase.get_conflicted_files",
        lambda repo_root, executor=None: ["README.md"],
    )

    executor = FakeProcessExecutor({})

    with pytest.raises(RebaseOperationError, match="Conflicts remain"):
        continue_rebase(repo_root=tmp_git_repo, executor=executor)


def test_continue_rebase_executes_cli_when_ready(monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path) -> None:
    _create_rebase_state(tmp_git_repo)
    monkeypatch.setattr(
        "ralph.git.rebase.rebase.get_conflicted_files",
        lambda repo_root, executor=None: [],
    )
    responses = {
        ("git", ("rebase", "--continue")): _mk_result(),
    }
    executor = FakeProcessExecutor(responses)

    continue_rebase(repo_root=tmp_git_repo, executor=executor)

    assert executor.calls == [("git", ("rebase", "--continue"))]


def test_rebase_onto_returns_noop_when_branch_up_to_date(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    current = repo.active_branch.name
    branch_name = "feature-noop"
    repo.git.checkout("-b", branch_name)
    responses = {
        ("git", ("merge-base", "--is-ancestor", branch_name, "HEAD")): _mk_result(returncode=0),
    }
    executor = FakeProcessExecutor(responses)

    result = rebase_onto(upstream_branch=branch_name, repo_root=tmp_git_repo, executor=executor)

    assert isinstance(result, RebaseNoOp)
    assert "up-to-date" in result.reason
    assert executor.calls == [
        ("git", ("merge-base", "--is-ancestor", branch_name, "HEAD")),
    ]


def test_rebase_onto_detects_conflicts(monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    current = repo.active_branch.name
    base_branch = current
    repo.git.checkout("-b", "feature-conflict")
    responses = {
        ("git", ("merge-base", "--is-ancestor", base_branch, "HEAD")): _mk_result(returncode=1),
        ("git", ("rebase", base_branch)): _mk_result(
            returncode=1,
            stderr="CONFLICT (content): Merge conflict in README.md",
        ),
    }
    executor = FakeProcessExecutor(responses)
    monkeypatch.setattr(
        "ralph.git.rebase.rebase.get_conflicted_files",
        lambda repo_root, executor=None: ["README.md"],
    )

    result = rebase_onto(upstream_branch=base_branch, repo_root=tmp_git_repo, executor=executor)

    assert isinstance(result, RebaseConflicts)
    assert result.files == ["README.md"]


def test_get_conflicted_files_reports_conflicts(tmp_git_repo: Path) -> None:
    repo = Repo(tmp_git_repo)
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    (tmp_git_repo / "README.md").write_text("feature content")
    repo.index.add(["README.md"])
    repo.index.commit("feature update")
    repo.git.checkout(base)
    (tmp_git_repo / "README.md").write_text("base content")
    repo.index.add(["README.md"])
    repo.index.commit("base update")
    repo.git.checkout("feature")
    with pytest.raises(GitCommandError):
        repo.git.merge(base)

    try:
        files = get_conflicted_files(repo_root=tmp_git_repo)
        assert "README.md" in files
    finally:
        repo.git.merge("--abort")
