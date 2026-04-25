"""Tests for git wrapper helpers."""
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

from pathlib import Path

import pytest
from git import GitCommandError, Repo

from ralph.git import (  # type: ignore[reportAttributeAccessIssue]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    detect_unauthorized_commit,
    end_agent_phase,
    start_agent_phase,
)


def read_hooks_path(repo: Repo) -> str:
    """Return the configured hooksPath value for the repository."""

    return repo.git.config("--local", "--get", "core.hooksPath").strip()


def test_start_agent_phase_sets_hooks_path(tmp_git_repo: Path) -> None:
    """Start phase should update core.hooksPath to Ralph's hooks directory."""

    repo = Repo(tmp_git_repo)
    with pytest.raises(GitCommandError):
        read_hooks_path(repo)

    start_agent_phase(tmp_git_repo)

    expected = str(Path(repo.git_dir) / "ralph" / "hooks")
    assert read_hooks_path(repo) == expected


def test_detect_unauthorized_commit_detects_new_commit(tmp_git_repo: Path) -> None:
    """Unauthorized commit detection should compare to the stored HEAD OID."""

    start_agent_phase(tmp_git_repo)
    assert detect_unauthorized_commit(tmp_git_repo) is False

    repo = Repo(tmp_git_repo)
    (tmp_git_repo / "README.md").write_text("unauthorized")
    repo.index.add(["README.md"])
    repo.index.commit("unauthorized commit")

    assert detect_unauthorized_commit(tmp_git_repo) is True


def test_end_agent_phase_restores_hooks_path(tmp_git_repo: Path) -> None:
    """Ending the agent phase should restore the previous hooksPath setting."""

    repo = Repo(tmp_git_repo)
    start_agent_phase(tmp_git_repo)
    end_agent_phase(tmp_git_repo)

    with pytest.raises(GitCommandError):
        read_hooks_path(repo)
