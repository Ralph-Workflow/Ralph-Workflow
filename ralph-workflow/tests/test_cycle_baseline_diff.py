"""Tests: cycle baseline semantics and cumulative dev-cycle diff."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from git import Repo

import ralph.pipeline.runner as runner_module
import ralph.prompts.materialize as materialize_module
from ralph.pipeline.cycle_baseline import (
    clear_cycle_baseline,
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


class TestCycleBaselineDiff:
    def test_cumulative_diff_spans_baseline_to_head(self, git_repo: tuple[Path, Repo]) -> None:
        root, repo = git_repo
        baseline_sha = str(repo.head.commit.hexsha)
        write_cycle_baseline(root, baseline_sha, force=True)

        _make_commit(repo, root, "change1.txt", "first change", "first mid-cycle commit")
        _make_commit(repo, root, "change2.txt", "second change", "second mid-cycle commit")


        real_diff = materialize_module._git_diff(root)
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


        real_diff = materialize_module._git_diff(root)
        assert "uncommitted" in real_diff


class TestRunnerCycleStartUsesForceTrue:
    """Prove that the real runner cycle-start path always passes force=True."""

    def test_runner_calls_write_cycle_baseline_with_force_true(
        self, git_repo: tuple[Path, Repo]
    ) -> None:
        root, _ = git_repo
        captured_calls: list[dict[str, object]] = []

        original = write_cycle_baseline

        def recording_write(workspace_root: Path, sha: str, *, force: bool = False) -> None:
            captured_calls.append({"workspace_root": workspace_root, "sha": sha, "force": force})
            original(workspace_root, sha, force=force)


        with patch.object(runner_module, "write_cycle_baseline", recording_write):
            runner_module._write_start_commit_if_absent(root)

        assert captured_calls, "write_cycle_baseline must be called during cycle initialization"
        assert all(c["force"] is True for c in captured_calls), (
            "Cycle-start path must always call write_cycle_baseline with force=True"
        )

    def test_runner_does_not_overwrite_existing_baseline(self, git_repo: tuple[Path, Repo]) -> None:
        root, _ = git_repo
        existing_sha = str(Repo(root).head.commit.hexsha)
        write_cycle_baseline(root, existing_sha, force=True)


        runner_module._write_start_commit_if_absent(root)

        assert read_cycle_baseline(root) == existing_sha, (
            "_write_start_commit_if_absent must not overwrite an already-set baseline"
        )
