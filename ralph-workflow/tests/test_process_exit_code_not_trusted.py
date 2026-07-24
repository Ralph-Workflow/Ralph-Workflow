"""Worker process exit failures must not be masked by artifact evidence."""

from __future__ import annotations

import contextlib
import importlib
import sys
from typing import TYPE_CHECKING, cast

import pytest

from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import PipelineEvent, WorkerFailedEvent
from ralph.pipeline.parallel.mode import SameWorkspaceContext
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
from tests.test_process_exit_code_not_trusted_helper__recordingmcpfactory import (
    _RecordingMcpFactory,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from pathlib import Path
    from typing import Protocol

    class _WorkerContextCtor(Protocol):
        def __call__(self, *, log: object | None = ..., same_workspace: object) -> object: ...

    class _CoordinatorModule(Protocol):
        WorkerContext: _WorkerContextCtor

        def run_fan_out(
            self,
            *,
            effect: FanOutEffect,
            executor: FakeAgentExecutor,
            display: _RecordingDisplay,
            ctx: object,
        ) -> Awaitable[list[object]]: ...


_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
    enable_zombie_reaper=False,
)

PYTHON = sys.executable
_EXPECTED_EXIT_CODE = 7


@pytest.fixture(autouse=True)
def _reset_pm() -> object:
    reset_process_manager()
    yield
    with contextlib.suppress(Exception):
        get_process_manager().shutdown_all(grace_period_s=0)
    reset_process_manager()


@pytest.mark.asyncio
async def test_exit_code_7_is_exited_not_failed(tmp_path: Path) -> None:
    """ProcessManager records EXITED (not FAILED) even when returncode != 0."""
    pm = ProcessManager(policy=_FAST_POLICY)
    handle = pm.spawn([PYTHON, "-c", f"import sys; sys.exit({_EXPECTED_EXIT_CODE})"])
    handle.wait(timeout=5.0)

    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.returncode == _EXPECTED_EXIT_CODE
    # FAILED is reserved for spawn-time failures (e.g., binary not found)
    assert handle.record.status != ProcessStatus.FAILED


def _load_coordinator() -> _CoordinatorModule:
    return cast(
        "_CoordinatorModule",
        importlib.import_module("ralph.pipeline.parallel.coordinator"),
    )


def _make_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Unit {unit_id}",
        dependencies=[],
        allowed_directories=[f"src/{unit_id}"],
    )


def _seed_artifact(artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "plan.md").write_text(
        "---\ntype: plan\n---\n## Summary\ndone\n",
        encoding="utf-8",
    )


class _RecordingDisplay:
    def __init__(self) -> None:
        self.statuses: dict[str, list[WorkerStatus]] = {}

    def emit(self, unit_id: str | None, line: str) -> None:
        pass

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        self.statuses.setdefault(unit_id, []).append(status)


def _make_ctx(module: _CoordinatorModule, same_workspace: object) -> object:
    ctx_type = module.WorkerContext
    return ctx_type(log=None, same_workspace=same_workspace)


@pytest.mark.asyncio
async def test_parallel_coordinator_regression_nonzero_exit_with_artifact_fails(
    tmp_path: Path,
) -> None:
    """Regression: a failed agent process cannot advance a fan-out wave."""
    module = _load_coordinator()
    unit = _make_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    display = _RecordingDisplay()
    mcp_factory = _RecordingMcpFactory()

    worker_namespace = tmp_path / ".agent" / "workers" / "unit-a"
    _seed_artifact(worker_namespace / "artifacts")

    same_workspace = SameWorkspaceContext(
        repo_root=tmp_path,
        mcp_factory=mcp_factory,
        worker_namespace_root=tmp_path / ".agent" / "workers",
    )

    events = await module.run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor(
            {"unit-a": FakeRun(outputs=["done"], exit_code=1, duration_ms=1)}
        ),
        display=display,
        ctx=_make_ctx(module, same_workspace),
    )

    assert PipelineEvent.ALL_WORKERS_COMPLETE not in events
    assert display.statuses["unit-a"][-1] is WorkerStatus.FAILED
    assert any(
        isinstance(event, WorkerFailedEvent) and event.unit_id == "unit-a" for event in events
    )


@pytest.mark.asyncio
async def test_zero_exit_without_artifact_is_treated_as_failure(tmp_path: Path) -> None:
    """Worker coordinator: exit_code == 0 but no artifact still fails."""
    module = _load_coordinator()
    unit = _make_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    display = _RecordingDisplay()
    mcp_factory = _RecordingMcpFactory()
    _seed_artifact(tmp_path / ".agent" / "artifacts")

    same_workspace = SameWorkspaceContext(
        repo_root=tmp_path,
        mcp_factory=mcp_factory,
        worker_namespace_root=tmp_path / ".agent" / "workers",
    )

    events = await module.run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor(
            {"unit-a": FakeRun(outputs=["done"], exit_code=0, duration_ms=1)}
        ),
        display=display,
        ctx=_make_ctx(module, same_workspace),
    )

    assert PipelineEvent.ALL_WORKERS_COMPLETE not in events, (
        "Worker with no artifact should fail regardless of exit code"
    )
    assert display.statuses["unit-a"][-1] is WorkerStatus.FAILED
    assert any(
        isinstance(event, WorkerFailedEvent) and event.unit_id == "unit-a" for event in events
    )
