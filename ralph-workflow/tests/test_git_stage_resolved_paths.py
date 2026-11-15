from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.git.merge import stage_paths
from tests.test_pending_merge_commit_recovery import _commit, _git

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def test_a_conflict_resolved_by_deleting_a_file_is_staged(tmp_git_repo: Path) -> None:
    _commit(tmp_git_repo, "kept.py", "kept\n")
    _commit(tmp_git_repo, "removed.py", "removed\n")
    (tmp_git_repo / "kept.py").write_text("resolved\n", encoding="utf-8")
    (tmp_git_repo / "removed.py").unlink()

    assert stage_paths(tmp_git_repo, ["kept.py", "removed.py"]) is True

    staged = _git(tmp_git_repo, "diff", "--cached", "--name-status").stdout.split("\n")
    assert "M\tkept.py" in staged
    assert "D\tremoved.py" in staged


def test_a_resolved_path_already_gone_from_the_index_does_not_block_staging(
    tmp_git_repo: Path,
) -> None:
    _commit(tmp_git_repo, "kept.py", "kept\n")
    (tmp_git_repo / "kept.py").write_text("resolved\n", encoding="utf-8")

    assert stage_paths(tmp_git_repo, ["kept.py", "never_tracked.py"]) is True

    staged = _git(tmp_git_repo, "diff", "--cached", "--name-status").stdout.split("\n")
    assert "M\tkept.py" in staged
