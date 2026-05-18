from __future__ import annotations

import importlib
import importlib.util
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.executor import WorkerResult
from ralph.mcp.server.factory import McpServerHandle
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import (
    Event,
    WorkerFailedEvent,
)
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.parallel.mode import SameWorkspaceContext

RunFanOut = Callable[..., Awaitable[list[Event]]]
TOTAL_CAP_UNITS = 5
MAX_CAP_WORKERS = 2
EXPECTED_WORKER_SCOPE_COUNT = 2


def _load_run_fan_out() -> RunFanOut:
    spec = importlib.util.find_spec("ralph.pipeline.parallel.coordinator")
    assert spec is not None, "ralph.pipeline.parallel.coordinator must exist"

    module = importlib.import_module("ralph.pipeline.parallel.coordinator")
    run_fan_out = getattr(module, "run_fan_out", None)
    assert callable(run_fan_out), "run_fan_out must be defined"
    return cast("RunFanOut", run_fan_out)


def make_unit(unit_id: str, deps: list[str] | None = None) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Unit {unit_id}",
        dependencies=list(deps or []),
        allowed_directories=[f"src/{unit_id}"],
    )


def make_worker_context(
    *,
    log_dir: Path | None = None,
    run_id: str = "default",
    same_workspace: SameWorkspaceContext | None = None,
) -> object:
    module = importlib.import_module("ralph.pipeline.parallel.coordinator")
    ctx_type = module.WorkerContext
    log = None
    if log_dir is not None:
        log_type = module.WorkerLog
        log = log_type(log_dir=log_dir, run_id=run_id)
    return ctx_type(log=log, same_workspace=same_workspace)


def _seed_artifact(repo_root: Path, unit_id: str) -> None:
    artifact_dir = repo_root / ".agent" / "workers" / unit_id / "artifacts"
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


class RecordingDisplay:
    def __init__(self) -> None:
        self.outputs: dict[str, list[str]] = defaultdict(list)
        self.statuses: dict[str, list[WorkerStatus]] = defaultdict(list)
        self._running_units: set[str] = set()
        self.peak_running = 0

    def emit(self, unit_id: str | None, line: str) -> None:
        if unit_id is None:
            return
        self.outputs[unit_id].append(line)

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        self.statuses[unit_id].append(status)
        if status is WorkerStatus.RUNNING:
            self._running_units.add(unit_id)
        elif status in {
            WorkerStatus.SUCCEEDED,
            WorkerStatus.FAILED,
            WorkerStatus.CANCELLED,
        }:
            self._running_units.discard(unit_id)
        self.peak_running = max(self.peak_running, len(self._running_units))


@dataclass
class _RecordedHandle:
    handle: McpServerHandle
    shutdown_calls: int = 0


class _RecordingMcpFactory:
    def __init__(self) -> None:
        self.sessions: list[object] = []
        self.handles: list[_RecordedHandle] = []

    def build(self, session: object) -> McpServerHandle:
        self.sessions.append(session)
        recorded = _RecordedHandle(
            handle=McpServerHandle(
                endpoint=f"http://127.0.0.1:{10_000 + len(self.handles)}/mcp",
                pid=1000 + len(self.handles),
                shutdown=lambda: None,
            )
        )

        def _shutdown(record: _RecordedHandle = recorded) -> None:
            record.shutdown_calls += 1

        recorded.handle = McpServerHandle(
            endpoint=recorded.handle.endpoint,
            pid=recorded.handle.pid,
            shutdown=_shutdown,
        )
        self.handles.append(recorded)
        return recorded.handle


class _LoggingExecutor:
    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
    ) -> WorkerResult:
        on_status(WorkerStatus.RUNNING)
        logger.info("worker-log-message")
        on_output("done")
        on_status(WorkerStatus.SUCCEEDED)
        return WorkerResult(
            unit_id=unit.unit_id,
            exit_code=0,
            final_message="done",
            duration_ms=1,
        )


class TestPreflightRejection:
    """Coordinator-level preflight rejects unsafe plans before any worker launches."""

    async def test_overlapping_edit_areas_rejected(self, tmp_path: Path) -> None:
        """Overlapping allowed_directories are rejected; no executor calls occur."""
        run_fan_out = _load_run_fan_out()
        unit_a = WorkUnit(
            unit_id="unit-a",
            description="Unit A",
            allowed_directories=["src"],
        )
        unit_b = WorkUnit(
            unit_id="unit-b",
            description="Unit B",
            allowed_directories=["src/sub"],  # 'src' is a prefix of 'src/sub' → overlap
        )
        effect = FanOutEffect(work_units=(unit_a, unit_b), max_workers=2)
        executor = FakeAgentExecutor(
            {
                "unit-a": FakeRun(outputs=[], exit_code=0, duration_ms=1),
                "unit-b": FakeRun(outputs=[], exit_code=0, duration_ms=1),
            }
        )
        display = RecordingDisplay()

        events = await run_fan_out(
            effect=effect,
            executor=executor,
            display=display,
        )

        assert any(
            isinstance(e, WorkerFailedEvent) and e.unit_id == "__preflight__" for e in events
        ), f"Expected __preflight__ failure event, got: {events}"
        preflight_event = next(
            e for e in events if isinstance(e, WorkerFailedEvent) and e.unit_id == "__preflight__"
        )
        assert "parallel preflight rejected plan:" in preflight_event.error
        assert executor.calls == [], "No executor.run() calls should occur on preflight rejection"

    async def test_missing_allowed_directories_rejected(self, tmp_path: Path) -> None:
        """Work unit with empty allowed_directories is rejected; no executor calls occur."""
        run_fan_out = _load_run_fan_out()
        unit_no_dirs = WorkUnit(
            unit_id="unit-nodirs",
            description="Unit without edit areas",
            allowed_directories=[],  # missing required edit area
        )
        effect = FanOutEffect(work_units=(unit_no_dirs,), max_workers=1)
        executor = FakeAgentExecutor(
            {
                "unit-nodirs": FakeRun(outputs=[], exit_code=0, duration_ms=1),
            }
        )
        display = RecordingDisplay()

        events = await run_fan_out(
            effect=effect,
            executor=executor,
            display=display,
        )

        assert any(
            isinstance(e, WorkerFailedEvent) and e.unit_id == "__preflight__" for e in events
        ), f"Expected __preflight__ failure event, got: {events}"
        assert executor.calls == [], "No executor.run() calls should occur on preflight rejection"

    async def test_reserved_path_rejected(self, tmp_path: Path) -> None:
        """Work unit declaring .agent as edit area is rejected; no executor calls occur."""
        run_fan_out = _load_run_fan_out()
        unit_reserved = WorkUnit(
            unit_id="unit-reserved",
            description="Unit with reserved path",
            allowed_directories=[".agent"],  # reserved path
        )
        effect = FanOutEffect(work_units=(unit_reserved,), max_workers=1)
        executor = FakeAgentExecutor(
            {
                "unit-reserved": FakeRun(outputs=[], exit_code=0, duration_ms=1),
            }
        )
        display = RecordingDisplay()

        events = await run_fan_out(
            effect=effect,
            executor=executor,
            display=display,
        )

        assert any(
            isinstance(e, WorkerFailedEvent) and e.unit_id == "__preflight__" for e in events
        ), f"Expected __preflight__ failure event, got: {events}"
        preflight_event = next(
            e for e in events if isinstance(e, WorkerFailedEvent) and e.unit_id == "__preflight__"
        )
        assert "parallel preflight rejected plan:" in preflight_event.error
        assert executor.calls == [], "No executor.run() calls should occur on preflight rejection"
