from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo
from loguru import logger

from ralph.git.commit_review import untrack_engine_internal_files

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(5),
]


def test_commit_review_regression_intentional_non_internal_symlink_has_no_refusal_log(
    tmp_git_repo: Path,
) -> None:
    agents_path = tmp_git_repo / "AGENTS.md"
    claude_path = tmp_git_repo / "CLAUDE.md"
    agents_path.write_text("instructions\n")
    claude_path.symlink_to("AGENTS.md")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["AGENTS.md", "CLAUDE.md"])
        repo.index.commit("track intentional instruction symlink")
    finally:
        repo.close()

    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        untracked = untrack_engine_internal_files(tmp_git_repo, lambda _path: False)
    finally:
        logger.remove(sink_id)

    assert untracked == []
    assert claude_path.is_symlink()
    assert not any(
        "Refusing to git rm --cached symlink under tracked engine-internal path: CLAUDE.md"
        in message
        for message in messages
    )

    repo = Repo(tmp_git_repo)
    try:
        assert "CLAUDE.md" in repo.git.ls_files("--cached").splitlines()
    finally:
        repo.close()
