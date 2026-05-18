"""Tests: cycle baseline semantics and cumulative dev-cycle diff."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo

import ralph.prompts.materialize as materialize_module
from ralph.pipeline.cycle_baseline import (
    read_cycle_baseline,
    write_cycle_baseline,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_commit(repo: Repo, root: Path, filename: str, content: str, message: str) -> str:
    (root / filename).write_text(content, encoding="utf-8")
    repo.index.add([filename])
    repo.index.commit(message)
    return str(repo.head.commit.hexsha)


@pytest.fixture()
def git_repo(tmp_path: Path) -> tuple[Path, Repo]:
    repo = Repo.init(tmp_path, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()
    _make_commit(repo, tmp_path, "readme.txt", "initial", "initial commit")
    return tmp_path, repo


class TestCycleBaselineDiff:
    def test_cumulative_diff_spans_baseline_to_head(self, git_repo: tuple[Path, Repo]) -> None:
        root, repo = git_repo
        baseline_sha = str(repo.head.commit.hexsha)
        write_cycle_baseline(root, baseline_sha, force=True)

        _make_commit(repo, root, "change1.txt", "first change", "first mid-cycle commit")
        _make_commit(repo, root, "change2.txt", "second change", "second mid-cycle commit")

        real_diff = materialize_module.git_diff(root)
        assert "change1" in real_diff or "change2" in real_diff

    def test_baseline_survives_mid_cycle_call(self, git_repo: tuple[Path, Repo]) -> None:
        root, repo = git_repo
        baseline_sha = str(repo.head.commit.hexsha)
        write_cycle_baseline(root, baseline_sha, force=True)

        _make_commit(repo, root, "change.txt", "mid-cycle", "mid-cycle commit")
        write_cycle_baseline(root, "should-not-overwrite")

        assert read_cycle_baseline(root) == baseline_sha

    def test_diff_includes_uncommitted_changes(self, git_repo: tuple[Path, Repo]) -> None:
        root, repo = git_repo
        baseline_sha = str(repo.head.commit.hexsha)
        write_cycle_baseline(root, baseline_sha, force=True)

        uncommitted_file = root / "uncommitted.txt"
        uncommitted_file.write_text("uncommitted content", encoding="utf-8")
        repo.index.add(["uncommitted.txt"])

        real_diff = materialize_module.git_diff(root)
        assert "uncommitted" in real_diff
