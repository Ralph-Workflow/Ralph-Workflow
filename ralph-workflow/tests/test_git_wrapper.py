"""Tests for git wrapper helpers."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from git import GitCommandError, Repo

from ralph.git import (
    detect_unauthorized_commit,
    end_agent_phase,
    start_agent_phase,
)


def read_hooks_path(repo: Repo) -> str:
    """Return the configured hooksPath value for the repository."""

    return cast("str", repo.git.config("--local", "--get", "core.hooksPath")).strip()


def test_start_agent_phase_sets_hooks_path(tmp_git_repo: Path) -> None:
    """Start phase should update core.hooksPath to Ralph's hooks directory."""

    with Repo(tmp_git_repo) as repo:
        with pytest.raises(GitCommandError):
            read_hooks_path(repo)

        start_agent_phase(tmp_git_repo)

        expected = str(Path(repo.git_dir) / "ralph" / "hooks")
        assert read_hooks_path(repo) == expected


@pytest.mark.timeout_seconds(5)
def test_detect_unauthorized_commit_detects_new_commit(tmp_git_repo: Path) -> None:
    """Unauthorized commit detection should compare to the stored HEAD OID."""

    start_agent_phase(tmp_git_repo)
    assert detect_unauthorized_commit(tmp_git_repo) is False

    with Repo(tmp_git_repo) as repo:
        (tmp_git_repo / "README.md").write_text("unauthorized")
        repo.index.add(["README.md"])
        repo.index.commit("unauthorized commit")
        assert detect_unauthorized_commit(tmp_git_repo) is True


@pytest.mark.timeout_seconds(5)
def test_end_agent_phase_restores_hooks_path(tmp_git_repo: Path) -> None:
    """Ending the agent phase should restore the previous hooksPath setting."""

    with Repo(tmp_git_repo) as repo:
        start_agent_phase(tmp_git_repo)
        end_agent_phase(tmp_git_repo)

        with pytest.raises(GitCommandError):
            read_hooks_path(repo)
