from __future__ import annotations

from pathlib import Path

import pytest
from git import GitCommandError, Repo

from ralph.git import (
    detect_unauthorized_commit,
    end_agent_phase,
    start_agent_phase,
)
from tests._support.typed_accessors import must_str

pytestmark = pytest.mark.timeout_seconds(5)


def read_hooks_path(repo: Repo) -> str:

    return must_str(repo.git.config("--local", "--get", "core.hooksPath")).strip()


def test_start_agent_phase_sets_hooks_path(tmp_git_repo: Path) -> None:

    with Repo(tmp_git_repo) as repo:
        with pytest.raises(GitCommandError):
            read_hooks_path(repo)

        start_agent_phase(tmp_git_repo)

        expected = str(Path(repo.git_dir) / "ralph" / "hooks")
        assert read_hooks_path(repo) == expected


@pytest.mark.timeout_seconds(5)
def test_detect_unauthorized_commit_detects_new_commit(tmp_git_repo: Path) -> None:

    start_agent_phase(tmp_git_repo)
    assert detect_unauthorized_commit(tmp_git_repo) is False

    with Repo(tmp_git_repo) as repo:
        (tmp_git_repo / "README.md").write_text("unauthorized")
        repo.index.add(["README.md"])
        repo.index.commit("unauthorized commit")
        assert detect_unauthorized_commit(tmp_git_repo) is True


@pytest.mark.timeout_seconds(5)
def test_end_agent_phase_restores_hooks_path(tmp_git_repo: Path) -> None:

    start_agent_phase(tmp_git_repo)
    end_agent_phase(tmp_git_repo)

    with Repo(tmp_git_repo) as repo, pytest.raises(GitCommandError):
        read_hooks_path(repo)
