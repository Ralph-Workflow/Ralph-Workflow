"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import WorkerFailedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.parallel.coordinator import WorkerContext
from ralph.pipeline.parallel.mode import SameWorkspaceContext
from ralph.pipeline.work_units import (
    WorkUnit,
)
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

if TYPE_CHECKING:
    from pathlib import Path
from tests.test_same_workspace_parallel_no_git_status_fallback_helper__fakemcpserverfactory import (
    _FakeMcpServerFactory,
)


def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


class TestNoGitStatusFallback:
    def test_coordinator_worker_exit0_no_artifact_produces_worker_failed_event(
        self, tmp_path: Path
    ) -> None:
        """Coordinator emits WorkerFailedEvent when exit_code=0 but no artifacts written."""

        unit = _make_unit("unit-a", ["src/a"])
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)
        worker_ctx = WorkerContext(same_workspace=ctx)

        class _SilentDisplay(ParallelDisplay):
            def __init__(self) -> None:
                pass

            def emit(self, unit_id: str, line: str) -> None:
                pass

            def set_status(self, unit_id: str, status: object) -> None:
                pass

        effect = FanOutEffect(work_units=(unit,), max_workers=1)
        executor = FakeAgentExecutor(
            {"unit-a": FakeRun(outputs=["done"], exit_code=0, duration_ms=1)}
        )

        events = asyncio.run(
            coordinator.run_fan_out(
                effect=effect,
                executor=executor,
                display=_SilentDisplay(),
                ctx=worker_ctx,
            )
        )

        failed = [ev for ev in events if isinstance(ev, WorkerFailedEvent)]
        assert len(failed) == 1
        assert failed[0].unit_id == "unit-a"


def _make_same_workspace_context(
    tmp_path: Path,
    *,
    executor_command: tuple[str, ...] | None = None,
) -> SameWorkspaceContext:
    return SameWorkspaceContext(
        repo_root=tmp_path,
        mcp_factory=_FakeMcpServerFactory(),
        executor_command=executor_command,
    )
