"""Invariant test: pipeline success is determined by empirical evidence, never exit codes."""

from __future__ import annotations

import contextlib
import importlib
import json
import sys

import pytest

from ralph.mcp.server.factory import McpServerHandle
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.events import PipelineEvent, WorkerFailedEvent
from ralph.pipeline.parallel.coordinator import _has_empirical_evidence
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    get_process_manager,
    reset_process_manager,
)
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3, kill_followup_timeout_s=0.5, log_events=False
)

PYTHON = sys.executable
_EXPECTED_EXIT_CODE = 7


@pytest.fixture(autouse=True)
def _reset_pm():
    reset_process_manager()
    yield
    with contextlib.suppress(Exception):
        get_process_manager().shutdown_all(grace_period_s=0)
    reset_process_manager()


@pytest.mark.asyncio
async def test_exit_code_7_is_exited_not_failed(tmp_path) -> None:
    """ProcessManager records EXITED (not FAILED) even when returncode != 0."""
    pm = ProcessManager(policy=_FAST_POLICY)
    handle = pm.spawn([PYTHON, "-c", f"import sys; sys.exit({_EXPECTED_EXIT_CODE})"])
    handle.wait()

    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.returncode == _EXPECTED_EXIT_CODE
    # FAILED is reserved for spawn-time failures (e.g., binary not found)
    assert handle.record.status != ProcessStatus.FAILED


@pytest.mark.asyncio
async def test_empirical_evidence_ignores_exit_code(tmp_path) -> None:
    """_has_empirical_evidence returns False on empty dir and True when artifact present."""
    # No artifacts, no git changes → no empirical evidence
    no_evidence = await _has_empirical_evidence(tmp_path)
    assert no_evidence is False

    # Drop an artifact file → evidence is present regardless of exit code
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "dummy.json").write_text(json.dumps({"type": "plan"}), encoding="utf-8")

    has_evidence = await _has_empirical_evidence(tmp_path)
    assert has_evidence is True


def _load_coordinator():
    return importlib.import_module("ralph.pipeline.parallel.coordinator")


def _make_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=f"Unit {unit_id}", dependencies=[])


def _seed_artifact(worktree_path) -> None:
    artifact_dir = worktree_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "plan.json").write_text(
        json.dumps(
            {
                "name": "plan",
                "type": "plan",
                "content": {"summary": "done"},
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "metadata": {},
            }
        )
    )


class _RecordingDisplay:
    def __init__(self) -> None:
        self.statuses: dict[str, list[WorkerStatus]] = {}

    def emit(self, unit_id: str | None, line: str) -> None:
        pass

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        self.statuses.setdefault(unit_id, []).append(status)


class _RecordingMcpFactory:
    def build(self, session: object) -> McpServerHandle:
        return McpServerHandle(
            endpoint="http://127.0.0.1:19999/mcp",
            pid=9999,
            shutdown=lambda: None,
        )


class _RecordingWorktreeManager:
    def __init__(self, repo_root) -> None:
        self.repo_root = repo_root
        self.destroy_calls: list[str] = []

    def create(self, unit_id: str, base_branch: str):
        worktree_path = self.repo_root / ".worktrees" / unit_id
        worktree_path.mkdir(parents=True, exist_ok=True)
        return worktree_path

    def destroy(self, unit_id: str) -> None:
        self.destroy_calls.append(unit_id)


def _make_ctx(module, isolation):
    ctx_type = module._WorkerContext
    return ctx_type(log=None, isolation=isolation)


@pytest.mark.asyncio
async def test_nonzero_exit_with_artifact_is_treated_as_success(tmp_path) -> None:
    """Worker coordinator: exit_code != 0 but artifact present → worker succeeds.

    This is the exit-code-not-trusted invariant exercised end-to-end through
    the actual _run_worker / run_fan_out decision path.
    """
    module = _load_coordinator()
    unit = _make_unit("unit-a")
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    display = _RecordingDisplay()
    worktree_manager = _RecordingWorktreeManager(tmp_path)
    mcp_factory = _RecordingMcpFactory()

    _seed_artifact(tmp_path / ".worktrees" / "unit-a")

    isolation = module._IsolationDeps(  # type: ignore[attr-defined]
        worktree_manager=worktree_manager,
        mcp_factory=mcp_factory,
        repo_root=tmp_path,
    )

    events = await module.run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor(
            {"unit-a": FakeRun(outputs=["done"], exit_code=1, duration_ms=1)}
        ),
        display=display,
        ctx=_make_ctx(module, isolation),
    )

    assert events[-1] is PipelineEvent.ALL_WORKERS_COMPLETE, (
        "Worker with artifact should succeed regardless of exit code"
    )
    assert display.statuses["unit-a"][-1] is WorkerStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_zero_exit_without_artifact_or_git_changes_is_treated_as_failure(tmp_path) -> None:
    """Worker coordinator: exit_code == 0 but no artifact and no git changes → worker fails.

    This is the exit-code-not-trusted invariant exercised end-to-end through
    the actual _run_worker / run_fan_out decision path.
    """
    module = _load_coordinator()
    unit = _make_unit("unit-a")
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    display = _RecordingDisplay()
    worktree_manager = _RecordingWorktreeManager(tmp_path)
    mcp_factory = _RecordingMcpFactory()

    isolation = module._IsolationDeps(  # type: ignore[attr-defined]
        worktree_manager=worktree_manager,
        mcp_factory=mcp_factory,
        repo_root=tmp_path,
    )

    events = await module.run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor(
            {"unit-a": FakeRun(outputs=["done"], exit_code=0, duration_ms=1)}
        ),
        display=display,
        ctx=_make_ctx(module, isolation),
    )

    assert PipelineEvent.ALL_WORKERS_COMPLETE not in events, (
        "Worker with no artifact and no git changes should fail regardless of exit code"
    )
    assert display.statuses["unit-a"][-1] is WorkerStatus.FAILED
    assert any(
        isinstance(event, WorkerFailedEvent) and event.unit_id == "unit-a"
        for event in events
    )
