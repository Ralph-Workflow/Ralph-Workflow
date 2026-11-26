from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo as GitRepo

from ralph.workspace.fs import FsWorkspace
from ralph.workspace.probe import DefaultWorkspaceChangeProbe

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(8)]


def _repo_with_ignored_log(root: Path) -> FsWorkspace:
    (root / ".gitignore").write_text("log/\n", encoding="utf-8")
    (root / "log").mkdir()
    (root / "log" / "test.log").write_text("run 1\n", encoding="utf-8")
    with GitRepo(root) as repo:
        repo.index.add([".gitignore"])
        repo.index.commit("ignore logs")
    return FsWorkspace(root, allowed_roots=[root])


def test_a_test_run_that_only_rewrites_a_gitignored_log_is_not_workspace_progress(
    tmp_git_repo: Path,
) -> None:
    workspace = _repo_with_ignored_log(tmp_git_repo)
    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("development_analysis", workspace)

    (tmp_git_repo / "log" / "test.log").write_text("run 1\nrun 2\n", encoding="utf-8")

    assert probe.has_changed("development_analysis", workspace) is False


def test_a_new_untracked_source_file_is_still_workspace_progress(tmp_git_repo: Path) -> None:
    workspace = _repo_with_ignored_log(tmp_git_repo)
    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("development", workspace)

    (tmp_git_repo / "feature.py").write_text("x = 1\n", encoding="utf-8")

    assert probe.has_changed("development", workspace) is True
