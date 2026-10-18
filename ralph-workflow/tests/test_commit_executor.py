"""Tests for commit executor detached HEAD reattachment and git state repair."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from git import Actor, Repo

from ralph.git.commit_result import CommitCreationResult
from ralph.pipeline.commit_executor import (
    execute_commit_effect,
    get_last_commit_error,
)
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent

if TYPE_CHECKING:
    import pytest


def _init_test_git_repo(repo_root: Path, branch: str = "main") -> Repo:
    repo = Repo.init(repo_root)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    actor = Actor("Test User", "test@example.com")
    init_file = repo_root / "initial.txt"
    init_file.write_text("initial content", encoding="utf-8")
    repo.index.add(["initial.txt"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    if repo.active_branch.name != branch:
        repo.git.branch("-M", branch)
    return repo


def _create_commit_message_artifact(repo_root: Path, message: str = "fix: update file") -> Path:
    artifact = repo_root / ".agent" / "artifacts" / "commit_message.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        f"---\ntype: commit\nsubject: {message}\n---\n",
        encoding="utf-8",
    )
    return artifact


def test_detached_head_reattaches_to_branch_before_commit(tmp_path: Path) -> None:
    repo = _init_test_git_repo(tmp_path, "feature-branch")
    initial_sha = repo.head.commit.hexsha

    # Detach HEAD
    repo.git.checkout(initial_sha)
    assert repo.head.is_detached

    # Add uncommitted tracked file changes
    test_file = tmp_path / "initial.txt"
    test_file.write_text("modified content", encoding="utf-8")

    message_file = _create_commit_message_artifact(tmp_path, "feat: update initial file")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        branch="feature-branch",
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature-branch"
    assert repo.head.commit.message.splitlines()[0] == "feat: update initial file"


def test_detached_head_resolves_branch_from_opts(tmp_path: Path) -> None:
    repo = _init_test_git_repo(tmp_path, "feature-opts")
    initial_sha = repo.head.commit.hexsha

    repo.git.checkout(initial_sha)
    assert repo.head.is_detached

    work_file = tmp_path / "initial.txt"
    work_file.write_text("new work", encoding="utf-8")

    message_file = _create_commit_message_artifact(tmp_path, "feat: add work file")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        feature_branch="feature-opts",
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature-opts"


def test_detached_head_resolves_branch_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_test_git_repo(tmp_path, "feature-env")
    initial_sha = repo.head.commit.hexsha

    repo.git.checkout(initial_sha)
    assert repo.head.is_detached

    monkeypatch.setenv("FEATURE_BRANCH", "feature-env")

    work_file = tmp_path / "initial.txt"
    work_file.write_text("env work", encoding="utf-8")

    message_file = _create_commit_message_artifact(tmp_path, "feat: env work")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature-env"


def test_repairable_git_state_stale_index_lock_repaired_and_retried(tmp_path: Path) -> None:
    _init_test_git_repo(tmp_path, "main")

    work_file = tmp_path / "initial.txt"
    work_file.write_text("updated work", encoding="utf-8")

    # Create stale index.lock with dead PID
    git_dir = tmp_path / ".git"
    index_lock = git_dir / "index.lock"
    # Write PID that definitely does not exist (99999999)
    index_lock.write_text("99999999\n", encoding="utf-8")

    message_file = _create_commit_message_artifact(tmp_path, "feat: lock recovery")

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
    )

    assert event == PipelineEvent.COMMIT_SUCCESS
    assert not index_lock.exists()


def test_commit_creation_failure_records_last_error(tmp_path: Path) -> None:
    _init_test_git_repo(tmp_path, "main")

    work_file = tmp_path / "initial.txt"
    work_file.write_text("content", encoding="utf-8")

    message_file = _create_commit_message_artifact(tmp_path, "feat: failing commit")

    mock_create_commit = MagicMock(
        return_value=CommitCreationResult.failed("simulated commit failure")
    )

    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        create_commit_fn=mock_create_commit,
    )

    assert event == PipelineEvent.COMMIT_FAILURE
    last_err = get_last_commit_error()
    assert last_err is not None
    assert "simulated commit failure" in last_err
