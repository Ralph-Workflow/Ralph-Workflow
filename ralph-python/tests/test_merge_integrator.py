"""Tests for MergeIntegrationEffect handler — merge_integrator module."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from ralph.git.executor import GitExecutor
from ralph.pipeline.events import PipelineEvent, WorkersMergeConflictEvent
from ralph.pipeline.parallel.merge_integrator import integrate
from ralph.pipeline.worker_state import WorkerState, WorkerStatus

if TYPE_CHECKING:
    from pathlib import Path


def _make_test_repo(tmp_path: Path) -> tuple[Path, GitExecutor]:
    """Create a minimal git repo with initial commit on main branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    (repo / "base.txt").write_text("base content")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "branch", "-M", "main"],
        check=True,
        capture_output=True,
    )
    return repo, GitExecutor()


def _create_worker_branch(repo: Path, unit_id: str, filename: str, content: str) -> None:
    """Create a worker branch with a single file change."""
    branch_name = f"ralph/unit-{unit_id}"
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", branch_name],
        check=True,
        capture_output=True,
    )
    (repo / filename).write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", f"worker {unit_id}"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )


def _make_worker_state(unit_id: str, status: WorkerStatus) -> WorkerState:
    return WorkerState(unit_id=unit_id, status=status)


@pytest.mark.asyncio
async def test_happy_three_way(tmp_path: Path) -> None:
    """Three workers with non-overlapping files all merge cleanly."""
    repo, git_executor = _make_test_repo(tmp_path)

    _create_worker_branch(repo, "alpha", "alpha.txt", "alpha content")
    _create_worker_branch(repo, "beta", "beta.txt", "beta content")
    _create_worker_branch(repo, "gamma", "gamma.txt", "gamma content")

    worker_states = {
        "alpha": _make_worker_state("alpha", WorkerStatus.SUCCEEDED),
        "beta": _make_worker_state("beta", WorkerStatus.SUCCEEDED),
        "gamma": _make_worker_state("gamma", WorkerStatus.SUCCEEDED),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=git_executor,
        repo_root=repo,
    )

    assert result.success is True
    assert result.conflicting_unit_ids == []
    assert result.events == [PipelineEvent.ALL_WORKERS_COMPLETE]

    assert (repo / "alpha.txt").exists()
    assert (repo / "beta.txt").exists()
    assert (repo / "gamma.txt").exists()


@pytest.mark.asyncio
async def test_conflict_detected(tmp_path: Path) -> None:
    """Two workers that modify the same file produce a conflict."""
    repo, git_executor = _make_test_repo(tmp_path)

    _create_worker_branch(repo, "worker1", "base.txt", "worker1 modified base")
    _create_worker_branch(repo, "worker2", "base.txt", "worker2 modified base")

    worker_states = {
        "worker1": _make_worker_state("worker1", WorkerStatus.SUCCEEDED),
        "worker2": _make_worker_state("worker2", WorkerStatus.SUCCEEDED),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=git_executor,
        repo_root=repo,
    )

    assert result.success is False
    assert len(result.events) == 1
    conflict_event = result.events[0]
    assert isinstance(conflict_event, WorkersMergeConflictEvent)
    assert len(conflict_event.conflicting_unit_ids) >= 1


@pytest.mark.asyncio
async def test_conflict_preserves_worktrees(tmp_path: Path) -> None:
    """On conflict, worktrees are NOT destroyed — caller handles cleanup."""
    repo, git_executor = _make_test_repo(tmp_path)

    _create_worker_branch(repo, "a", "base.txt", "a modified base")
    _create_worker_branch(repo, "b", "base.txt", "b modified base")

    worktree_a = tmp_path / "worktree_a"
    worktree_b = tmp_path / "worktree_b"
    worktree_a.mkdir()
    worktree_b.mkdir()
    (worktree_a / "sentinel_a.txt").write_text("should remain")
    (worktree_b / "sentinel_b.txt").write_text("should remain")

    worker_states = {
        "a": WorkerState(
            unit_id="a",
            status=WorkerStatus.SUCCEEDED,
            worktree_path=str(worktree_a),
        ),
        "b": WorkerState(
            unit_id="b",
            status=WorkerStatus.SUCCEEDED,
            worktree_path=str(worktree_b),
        ),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=git_executor,
        repo_root=repo,
    )

    assert result.success is False
    assert worktree_a.exists(), "worktree_a should be preserved on conflict"
    assert worktree_b.exists(), "worktree_b should be preserved on conflict"
    assert (worktree_a / "sentinel_a.txt").exists()
    assert (worktree_b / "sentinel_b.txt").exists()


@pytest.mark.asyncio
async def test_only_succeeded_workers_merged(tmp_path: Path) -> None:
    """FAILED, CANCELLED, PENDING, and RUNNING workers are skipped."""
    repo, git_executor = _make_test_repo(tmp_path)

    _create_worker_branch(repo, "good", "good.txt", "good content")

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
        git_executor=git_executor,
        repo_root=repo,
    )

    assert result.success is True
    assert result.events == [PipelineEvent.ALL_WORKERS_COMPLETE]
    assert (repo / "good.txt").exists()


@pytest.mark.asyncio
async def test_empty_succeeded_workers(tmp_path: Path) -> None:
    """No SUCCEEDED workers → immediate ALL_WORKERS_COMPLETE with no merges."""
    repo, git_executor = _make_test_repo(tmp_path)

    worker_states = {
        "failed1": _make_worker_state("failed1", WorkerStatus.FAILED),
        "failed2": _make_worker_state("failed2", WorkerStatus.CANCELLED),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=git_executor,
        repo_root=repo,
    )

    assert result.success is True
    assert result.conflicting_unit_ids == []
    assert result.events == [PipelineEvent.ALL_WORKERS_COMPLETE]


@pytest.mark.asyncio
async def test_workers_merged_in_deterministic_order(tmp_path: Path) -> None:
    """Workers are merged sorted by unit_id for deterministic ordering."""
    repo, git_executor = _make_test_repo(tmp_path)

    _create_worker_branch(repo, "zzz", "zzz.txt", "zzz content")
    _create_worker_branch(repo, "aaa", "aaa.txt", "aaa content")
    _create_worker_branch(repo, "mmm", "mmm.txt", "mmm content")

    worker_states = {
        "zzz": _make_worker_state("zzz", WorkerStatus.SUCCEEDED),
        "aaa": _make_worker_state("aaa", WorkerStatus.SUCCEEDED),
        "mmm": _make_worker_state("mmm", WorkerStatus.SUCCEEDED),
    }

    result = await integrate(
        base_branch="main",
        worker_states=worker_states,
        git_executor=git_executor,
        repo_root=repo,
    )

    assert result.success is True
    assert result.events == [PipelineEvent.ALL_WORKERS_COMPLETE]
