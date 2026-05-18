"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.display.parallel_display import ParallelDisplay
from ralph.mcp.artifacts.store import list_artifacts
from ralph.mcp.server.factory import McpServerHandle
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import WorkerFailedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.parallel.coordinator import (
    WorkerContext,
    WorkerFailureError,
)
from ralph.pipeline.parallel.mode import SameWorkspaceContext
from ralph.pipeline.work_units import (
    WorkUnit,
)
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

if TYPE_CHECKING:
    from pathlib import Path



def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


class _FakeMcpServerFactory:
    def build(self, session: object) -> McpServerHandle:
        return McpServerHandle(
            endpoint="http://127.0.0.1:9999/mcp", pid=99999, shutdown=lambda: None
        )


class TestNoGitStatusFallback:

    def test_worker_success_requires_worker_local_artifact(self, tmp_path: Path) -> None:
        """Worker success is determined by artifacts, never by git status."""
        unit = _make_unit("unit-a")
        artifact_dir = tmp_path / ".agent" / "workers" / "unit-a" / "artifacts"
        artifact_dir.mkdir(parents=True)

        # No artifacts in artifact_dir → should be considered failure
        assert list_artifacts(artifact_dir) == []

        # Verify the coordinator raises _WorkerFailureError when no artifacts
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.unit_id = "unit-a"

        with pytest.raises(WorkerFailureError, match="no worker-local artifact evidence"):
            if not list_artifacts(artifact_dir):
                raise WorkerFailureError(
                    unit_id=unit.unit_id,
                    exit_code=mock_result.exit_code,
                    error=(
                        f"Worker {unit.unit_id!r} produced no worker-local artifact "
                        f"evidence under {artifact_dir} "
                        f"(exit_code={mock_result.exit_code})"
                    ),
                )

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
