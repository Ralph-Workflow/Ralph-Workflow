from __future__ import annotations

import importlib
import importlib.util
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast

from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import (
    Event,
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
)
from ralph.pipeline.work_units import WorkUnit
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.parallel.mode import SameWorkspaceContext
from tests.test_parallel_coordinator_helper_recordingdisplay import RecordingDisplay

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


async def test_nonzero_worker_exit_is_failure_even_when_artifacts_would_exist() -> None:
    """Analysis regression: exit 1 must not become a completed worker event."""
    run_fan_out = _load_run_fan_out()
    unit = make_unit("unit-a")
    executor = FakeAgentExecutor(
        {unit.unit_id: FakeRun(outputs=["agent failed"], exit_code=1, duration_ms=1)}
    )

    events = await run_fan_out(
        effect=FanOutEffect(work_units=(unit,), max_workers=1),
        executor=executor,
        display=RecordingDisplay(),
    )

    failures = [event for event in events if isinstance(event, WorkerFailedEvent)]
    assert len(failures) == 1
    assert failures[0].exit_code == 1
    assert "agent failed" in failures[0].error
    assert not any(isinstance(event, WorkerCompletedEvent) for event in events)
    assert PipelineEvent.ALL_WORKERS_COMPLETE not in events


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
