"""Dirty-path snapshots name paths exactly as they are on disk.

Non-``-z`` porcelain C-quotes any path with a space, a quote or a
non-ASCII byte (``"my notes.md"``, ``"caf\\303\\251.txt"``) and joins a
rename into one ``old -> new`` string. Callers compare the snapshot
against real repo-relative paths (the skill installer's user-dirty guard
does exactly that), so a quoted name never matched and a user's edit to
such a file was invisible to every guard built on the snapshot.

These tests drive real ``git status`` against a temporary repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.git.scoped_auto_commit import list_dirty_paths, snapshot_dirty_paths_strict

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def test_snapshots_report_unquoted_paths(tmp_git_repo: Path) -> None:
    """Spaces, non-ASCII names and renames come back as real paths."""
    (tmp_git_repo / "moved from.txt").write_text("rename me\n", encoding="utf-8")
    with Repo(tmp_git_repo) as repo:
        repo.index.add(["moved from.txt"])
        repo.index.commit("add rename source")
        repo.git.mv("moved from.txt", "moved to.txt")
    (tmp_git_repo / "my notes.md").write_text("x\n", encoding="utf-8")
    (tmp_git_repo / "café.txt").write_text("x\n", encoding="utf-8")

    expected = frozenset({"my notes.md", "café.txt", "moved from.txt", "moved to.txt"})
    assert list_dirty_paths(tmp_git_repo) == expected
    assert snapshot_dirty_paths_strict(tmp_git_repo) == expected
