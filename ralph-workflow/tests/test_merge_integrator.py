"""Tests for MergeIntegrationEffect handler — merge_integrator module."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

import pytest

from ralph.git.executor import GitExecutor
from ralph.git.subprocess_runner import GitRunResult
from ralph.pipeline.events import PipelineEvent, WorkersMergeConflictEvent
from ralph.pipeline.parallel.merge_integrator import integrate
from ralph.pipeline.worker_state import WorkerState, WorkerStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path


T = TypeVar("T")


class _ImmediateGitExecutor(GitExecutor):
    async def arun(self, op: Callable[[], T]) -> T:
        return op()


def _make_worker_state(
    unit_id: str, status: WorkerStatus, *, worktree_path: str | None = None
) -> WorkerState:
    return WorkerState(unit_id=unit_id, status=status, worktree_path=worktree_path)


def _recording_run_git(*, failing_branches: set[str]) -> tuple[list[list[str]], Any]:
    calls: list[list[str]] = []

    def _fake(args: Sequence[str], **_kwargs: Any) -> GitRunResult:
        cmd = ["git", *args]
        calls.append(cmd)
        branch = args[-1] if args else ""
        if list(args[:2]) == ["merge", "--no-ff"] and branch in failing_branches:
            return GitRunResult(
                args=tuple(cmd),
                returncode=1,
                stdout="CONFLICT (content): Merge conflict in file.txt",
                stderr="",
            )
        return GitRunResult(args=tuple(cmd), returncode=0, stdout="", stderr="")

    return calls, _fake


@pytest.mark.asyncio
async def test_happy_three_way(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Three workers with non-overlapping files all merge cleanly."""
    calls, fake_run_git = _recording_run_git(failing_branches=set())
    monkeypatch.setattr("ralph.pipeline.parallel.merge_integrator.run_git", fake_run_git)

    worker_states = {
        "alpha": _make_worker_state("alpha", WorkerStatus.SUCCEEDED),
        "beta": _make_worker_state("beta", WorkerStatus.SUCCEEDED),
        "gamma": _make_worker_state("gamma", WorkerStatus.SUCCEEDED),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=_ImmediateGitExecutor(),
        repo_root=tmp_path,
    )

    assert result.success is True
    assert result.conflicting_unit_ids == []
    assert result.events == [PipelineEvent.ALL_WORKERS_COMPLETE]
    assert calls == [
        ["git", "merge", "--no-ff", "ralph/unit-alpha"],
        ["git", "merge", "--no-ff", "ralph/unit-beta"],
        ["git", "merge", "--no-ff", "ralph/unit-gamma"],
    ]


@pytest.mark.asyncio
async def test_conflict_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two workers that modify the same file produce a conflict."""
    calls, fake_run_git = _recording_run_git(failing_branches={"ralph/unit-worker2"})
    monkeypatch.setattr("ralph.pipeline.parallel.merge_integrator.run_git", fake_run_git)

    worker_states = {
        "worker1": _make_worker_state("worker1", WorkerStatus.SUCCEEDED),
        "worker2": _make_worker_state("worker2", WorkerStatus.SUCCEEDED),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=_ImmediateGitExecutor(),
        repo_root=tmp_path,
    )

    assert result.success is False
    assert result.conflicting_unit_ids == ["worker2"]
    assert result.events == [WorkersMergeConflictEvent(conflicting_unit_ids=["worker2"])]
    assert calls[-1] == ["git", "merge", "--abort"]


@pytest.mark.asyncio
async def test_conflict_preserves_worktrees(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """On conflict, worktrees are NOT destroyed — caller handles cleanup."""
    calls, fake_run_git = _recording_run_git(failing_branches={"ralph/unit-b"})
    monkeypatch.setattr("ralph.pipeline.parallel.merge_integrator.run_git", fake_run_git)

    worktree_a = tmp_path / "worktree_a"
    worktree_b = tmp_path / "worktree_b"
    worktree_a.mkdir()
    worktree_b.mkdir()
    (worktree_a / "sentinel_a.txt").write_text("should remain")
    (worktree_b / "sentinel_b.txt").write_text("should remain")

    worker_states = {
        "a": _make_worker_state("a", WorkerStatus.SUCCEEDED, worktree_path=str(worktree_a)),
        "b": _make_worker_state("b", WorkerStatus.SUCCEEDED, worktree_path=str(worktree_b)),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=_ImmediateGitExecutor(),
        repo_root=tmp_path,
    )

    assert result.success is False
    assert calls[-1] == ["git", "merge", "--abort"]
    assert worktree_a.exists(), "worktree_a should be preserved on conflict"
    assert worktree_b.exists(), "worktree_b should be preserved on conflict"
    assert (worktree_a / "sentinel_a.txt").exists()
    assert (worktree_b / "sentinel_b.txt").exists()


@pytest.mark.asyncio
async def test_only_succeeded_workers_merged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FAILED, CANCELLED, PENDING, and RUNNING workers are skipped."""
    calls, fake_run_git = _recording_run_git(failing_branches=set())
    monkeypatch.setattr("ralph.pipeline.parallel.merge_integrator.run_git", fake_run_git)

    worker_states = {
        "good": _make_worker_state("good", WorkerStatus.SUCCEEDED),
        "bad": _make_worker_state("bad", WorkerStatus.FAILED),
        "ugly": _make_worker_state("ugly", WorkerStatus.CANCELLED),
        "pending": _make_worker_state("pending", WorkerStatus.PENDING),
        "running": _make_worker_state("running", WorkerStatus.RUNNING),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=_ImmediateGitExecutor(),
        repo_root=tmp_path,
    )

    assert result.success is True
    assert result.events == [PipelineEvent.ALL_WORKERS_COMPLETE]
    assert calls == [["git", "merge", "--no-ff", "ralph/unit-good"]]


@pytest.mark.asyncio
async def test_empty_succeeded_workers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No SUCCEEDED workers → immediate ALL_WORKERS_COMPLETE with no merges."""
    calls, fake_run_git = _recording_run_git(failing_branches=set())
    monkeypatch.setattr("ralph.pipeline.parallel.merge_integrator.run_git", fake_run_git)

    worker_states = {
        "failed1": _make_worker_state("failed1", WorkerStatus.FAILED),
        "failed2": _make_worker_state("failed2", WorkerStatus.CANCELLED),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=_ImmediateGitExecutor(),
        repo_root=tmp_path,
    )

    assert result.success is True
    assert result.conflicting_unit_ids == []
    assert result.events == [PipelineEvent.ALL_WORKERS_COMPLETE]
    assert calls == []


@pytest.mark.asyncio
async def test_workers_merged_in_deterministic_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Workers are merged sorted by unit_id for deterministic ordering."""
    calls, fake_run_git = _recording_run_git(failing_branches=set())
    monkeypatch.setattr("ralph.pipeline.parallel.merge_integrator.run_git", fake_run_git)

    worker_states = {
        "zzz": _make_worker_state("zzz", WorkerStatus.SUCCEEDED),
        "aaa": _make_worker_state("aaa", WorkerStatus.SUCCEEDED),
        "mmm": _make_worker_state("mmm", WorkerStatus.SUCCEEDED),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=_ImmediateGitExecutor(),
        repo_root=tmp_path,
    )

    assert result.success is True
    assert result.events == [PipelineEvent.ALL_WORKERS_COMPLETE]
    assert calls == [
        ["git", "merge", "--no-ff", "ralph/unit-aaa"],
        ["git", "merge", "--no-ff", "ralph/unit-mmm"],
        ["git", "merge", "--no-ff", "ralph/unit-zzz"],
    ]
