"""Invariant: pipeline success uses worker-local artifact evidence, not process exit codes."""

from __future__ import annotations

import contextlib
import importlib
import json
import sys

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

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3, kill_followup_timeout_s=0.5, log_events=False
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
async def test_exit_code_7_is_exited_not_failed(tmp_path: object) -> None:
    """ProcessManager records EXITED (not FAILED) even when returncode != 0."""
    pm = ProcessManager(policy=_FAST_POLICY)
    handle = pm.spawn([PYTHON, "-c", f"import sys; sys.exit({_EXPECTED_EXIT_CODE})"])
    handle.wait()

    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.returncode == _EXPECTED_EXIT_CODE
    # FAILED is reserved for spawn-time failures (e.g., binary not found)
    assert handle.record.status != ProcessStatus.FAILED


def _load_coordinator() -> object:
    return importlib.import_module("ralph.pipeline.parallel.coordinator")


def _make_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Unit {unit_id}",
        dependencies=[],
        allowed_directories=[f"src/{unit_id}"],
    )


def _seed_artifact(artifact_dir: object) -> None:
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




def _make_ctx(module: object, same_workspace: object) -> object:
    ctx_type = module.WorkerContext
    return ctx_type(log=None, same_workspace=same_workspace)


@pytest.mark.asyncio
async def test_nonzero_exit_with_artifact_is_treated_as_success(tmp_path: object) -> None:
    """Worker coordinator: exit_code != 0 but artifact present → worker succeeds.

    This is the exit-code-not-trusted invariant: success comes from worker-local
    artifact evidence, not the process exit code.
    """
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

    assert events[-1] is PipelineEvent.ALL_WORKERS_COMPLETE, (
        "Worker with artifact should succeed regardless of exit code"
    )
    assert display.statuses["unit-a"][-1] is WorkerStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_zero_exit_without_artifact_is_treated_as_failure(tmp_path: object) -> None:
    """Worker coordinator: exit_code == 0 but no artifact → worker fails.

    This is the exit-code-not-trusted invariant: only worker-local artifact
    evidence determines success, never the process exit code.
    """
    module = _load_coordinator()
    unit = _make_unit("unit-a")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)
    display = _RecordingDisplay()
    mcp_factory = _RecordingMcpFactory()

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
