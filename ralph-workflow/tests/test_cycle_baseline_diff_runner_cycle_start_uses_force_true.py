"""Tests: cycle baseline semantics and cumulative dev-cycle diff."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from git import Repo

import ralph.pipeline.runner as runner_module
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
            runner_module.write_start_commit_if_absent(root)

        assert captured_calls, "write_cycle_baseline must be called during cycle initialization"
        assert all(c["force"] is True for c in captured_calls), (
            "Cycle-start path must always call write_cycle_baseline with force=True"
        )

    def test_runner_does_not_overwrite_existing_baseline(self, git_repo: tuple[Path, Repo]) -> None:
        root, _ = git_repo
        existing_sha = str(Repo(root).head.commit.hexsha)
        write_cycle_baseline(root, existing_sha, force=True)

        runner_module.write_start_commit_if_absent(root)

        assert read_cycle_baseline(root) == existing_sha, (
            "_write_start_commit_if_absent must not overwrite an already-set baseline"
        )

    def test_write_start_commit_if_absent_does_not_propagate_oserror(
        self, git_repo: tuple[Path, Repo]
    ) -> None:
        """write_start_commit_if_absent must handle write errors gracefully.

        Disk-full (ENOSPC) is environmental; the cycle baseline is best-effort.
        """
        import errno

        root, _ = git_repo

        def failing_write(workspace_root: Path, sha: str, *, force: bool = False) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        with patch.object(runner_module, "write_cycle_baseline", failing_write):
            runner_module.write_start_commit_if_absent(root)

        assert read_cycle_baseline(root) is None
