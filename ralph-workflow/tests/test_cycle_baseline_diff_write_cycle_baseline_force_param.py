"""Tests: cycle baseline semantics and cumulative dev-cycle diff."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.pipeline.cycle_baseline import (
    clear_cycle_baseline,
    read_cycle_baseline,
    write_cycle_baseline,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


def _make_commit(repo: Repo, root: Path, filename: str, content: str, message: str) -> str:
    (root / filename).write_text(content, encoding="utf-8")
    repo.index.add([filename])
    repo.index.commit(message)
    return str(repo.head.commit.hexsha)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Generator[tuple[Path, Repo], None, None]:
    repo = Repo.init(tmp_path, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()
    _make_commit(repo, tmp_path, "readme.txt", "initial", "initial commit")
    try:
        yield tmp_path, repo
    finally:
        repo.close()


class TestWriteCycleBaselineForceParam:
    def test_write_baseline_force_false_preserves_existing(
        self, git_repo: tuple[Path, Repo]
    ) -> None:
        root, _ = git_repo
        write_cycle_baseline(root, "sha-first", force=True)
        write_cycle_baseline(root, "sha-second", force=False)
        assert read_cycle_baseline(root) == "sha-first"

    def test_write_baseline_force_true_overwrites(self, git_repo: tuple[Path, Repo]) -> None:
        root, _ = git_repo
        write_cycle_baseline(root, "sha-first", force=True)
        write_cycle_baseline(root, "sha-second", force=True)
        assert read_cycle_baseline(root) == "sha-second"

    def test_write_baseline_no_force_when_absent_writes(self, git_repo: tuple[Path, Repo]) -> None:
        root, _ = git_repo
        assert read_cycle_baseline(root) is None
        write_cycle_baseline(root, "sha-new", force=False)
        assert read_cycle_baseline(root) == "sha-new"

    def test_mid_cycle_write_does_not_overwrite(self, git_repo: tuple[Path, Repo]) -> None:
        root, _ = git_repo
        write_cycle_baseline(root, "cycle-start-sha", force=True)
        write_cycle_baseline(root, "mid-cycle-sha")
        assert read_cycle_baseline(root) == "cycle-start-sha"

    def test_clear_cycle_baseline_removes_file(self, git_repo: tuple[Path, Repo]) -> None:
        root, _ = git_repo
        write_cycle_baseline(root, "sha-abc", force=True)
        assert read_cycle_baseline(root) == "sha-abc"
        clear_cycle_baseline(root)
        assert read_cycle_baseline(root) is None
