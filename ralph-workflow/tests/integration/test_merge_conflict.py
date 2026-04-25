from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar
from unittest.mock import MagicMock

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_FAILED, PHASE_MERGE_INTEGRATION
from ralph.git.executor import GitExecutor
from ralph.git.subprocess_runner import GitRunResult
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.events import PipelineEvent, WorkerCompletedEvent
from ralph.pipeline.runner import _execute_fan_out_sync
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    import pytest


T = TypeVar("T")


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=f"Work unit {unit_id}")


def _make_policy_bundle() -> MagicMock:
    bundle = MagicMock()
    bundle.pipeline.phases = {
        PHASE_DEVELOPMENT: MagicMock(requires_commit=False, drain="development"),
    }
    bundle.pipeline.parallel_execution.max_parallel_workers = 4
    bundle.agents.agent_drains = {}
    bundle.agents.agent_chains = {}
    return bundle


def _init_repo(repo: Path) -> None:
    repo.mkdir()


class _ImmediateGitExecutor(GitExecutor):
    async def arun(self, op: Callable[[], T]) -> T:
        return op()


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status

    def __enter__(self) -> _FakeDisplay:
        return self

    def __exit__(self, *args: object) -> None:
        del args


def _fake_run_git_factory(
    *,
    failing_branches: set[str],
    failure_returncode: int = 1,
    failure_stdout: str = "CONFLICT (content): Merge conflict in file.txt",
    failure_stderr: str = "",
) -> Callable[[Sequence[str]], GitRunResult]:
    def _fake(args: Sequence[str], **_kwargs: Any) -> GitRunResult:
        cmd = ("git", *args)
        branch = args[-1] if args else ""
        if list(args[:2]) == ["merge", "--no-ff"] and branch in failing_branches:
            return GitRunResult(
                args=cmd,
                returncode=failure_returncode,
                stdout=failure_stdout,
                stderr=failure_stderr,
            )
        return GitRunResult(args=cmd, returncode=0, stdout="", stderr="")

    return _fake


def test_non_conflict_merge_failure_enters_recovery_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)

    units = (_make_work_unit("worker-1"), _make_work_unit("worker-2"))
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=units,
        worker_states={
            "worker-1": WorkerState(unit_id="worker-1", status=WorkerStatus.PENDING),
            "worker-2": WorkerState(unit_id="worker-2", status=WorkerStatus.PENDING),
        },
    )
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=2)
    scope = MagicMock()
    scope.root = repo

    async def _fake_run_fan_out(**kwargs: object) -> list[object]:
        del kwargs
        return [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="worker-1", exit_code=0, commit_sha="sha-1"),
            WorkerCompletedEvent(unit_id="worker-2", exit_code=0, commit_sha="sha-2"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

    monkeypatch.setattr(
        "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
        MagicMock,
    )
    monkeypatch.setattr(
        "ralph.display.parallel_display.ParallelDisplay",
        _FakeDisplay,
    )
    monkeypatch.setattr(
        "ralph.pipeline.parallel.coordinator.run_fan_out",
        _fake_run_fan_out,
    )
    monkeypatch.setattr(
        "ralph.git.executor.GitExecutor",
        _ImmediateGitExecutor,
    )
    monkeypatch.setattr(
        "ralph.pipeline.parallel.merge_integrator.run_git",
        _fake_run_git_factory(
            failing_branches={"ralph/unit-worker-2"},
            failure_returncode=2,
            failure_stdout="",
            failure_stderr="fatal: merge failed unexpectedly",
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.checkpoint.save",
        lambda _state: None,
    )

    final_state = _execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        policy_bundle=_make_policy_bundle(),
        workspace_scope=scope,
    )

    assert final_state.previous_phase == PHASE_MERGE_INTEGRATION
    assert final_state.phase == PHASE_FAILED
    assert final_state.recovery_epoch == 1
    assert final_state.last_error is not None
    assert "Fan-out execution crashed" in final_state.last_error
    assert "git merge failed for branch ralph/unit-worker-2" in final_state.last_error


def test_merge_conflict_fails_phase_and_preserves_worktrees(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)

    worktree_1 = tmp_path / "worker-1"
    worktree_2 = tmp_path / "worker-2"
    worktree_1.mkdir()
    worktree_2.mkdir()
    (worktree_1 / "keep.txt").write_text("keep-1")
    (worktree_2 / "keep.txt").write_text("keep-2")

    units = (_make_work_unit("worker-1"), _make_work_unit("worker-2"))
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=units,
        worker_states={
            "worker-1": WorkerState(
                unit_id="worker-1",
                status=WorkerStatus.PENDING,
                worktree_path=str(worktree_1),
            ),
            "worker-2": WorkerState(
                unit_id="worker-2",
                status=WorkerStatus.PENDING,
                worktree_path=str(worktree_2),
            ),
        },
    )
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=2)
    scope = MagicMock()
    scope.root = repo

    async def _fake_run_fan_out(**kwargs: object) -> list[object]:
        del kwargs
        return [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="worker-1", exit_code=0, commit_sha="sha-1"),
            WorkerCompletedEvent(unit_id="worker-2", exit_code=0, commit_sha="sha-2"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

    monkeypatch.setattr(
        "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
        MagicMock,
    )
    monkeypatch.setattr(
        "ralph.display.parallel_display.ParallelDisplay",
        _FakeDisplay,
    )
    monkeypatch.setattr(
        "ralph.pipeline.parallel.coordinator.run_fan_out",
        _fake_run_fan_out,
    )
    monkeypatch.setattr(
        "ralph.git.executor.GitExecutor",
        _ImmediateGitExecutor,
    )
    monkeypatch.setattr(
        "ralph.pipeline.parallel.merge_integrator.run_git",
        _fake_run_git_factory(failing_branches={"ralph/unit-worker-2"}),
    )
    monkeypatch.setattr(
        "ralph.pipeline.checkpoint.save",
        lambda _state: None,
    )

    final_state = _execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        policy_bundle=_make_policy_bundle(),
        workspace_scope=scope,
    )

    assert final_state.previous_phase == PHASE_MERGE_INTEGRATION
    assert final_state.phase == PHASE_FAILED
    assert final_state.last_error is not None
    assert "Merge conflict in workers:" in final_state.last_error
    assert "worker-2" in final_state.last_error
    assert worktree_1.exists()
    assert worktree_2.exists()
    assert (worktree_1 / "keep.txt").read_text() == "keep-1"
    assert (worktree_2 / "keep.txt").read_text() == "keep-2"
    assert final_state.worker_states["worker-1"].status == WorkerStatus.SUCCEEDED
    assert final_state.worker_states["worker-2"].status == WorkerStatus.SUCCEEDED
    assert final_state.worker_states["worker-1"].worktree_path == str(worktree_1)
    assert final_state.worker_states["worker-2"].worktree_path == str(worktree_2)
