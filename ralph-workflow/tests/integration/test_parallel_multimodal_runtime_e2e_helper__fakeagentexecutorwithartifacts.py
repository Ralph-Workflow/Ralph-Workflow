from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun
from tests.plan_fixtures import MINIMAL_PLAN_MARKDOWN

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.executor import WorkerResult
    from ralph.pipeline.work_units import WorkUnit
    from ralph.pipeline.worker_state import WorkerStatus


class _FakeAgentExecutorWithArtifacts(FakeAgentExecutor):
    """FakeAgentExecutor that creates artifacts in the worker namespace.

    This simulates what a real agent would do when it completes - create
    output artifacts in the worker's artifacts/ and handoffs/ directories.
    """

    def __init__(self, runs: dict[str, FakeRun], tmp_path: Path) -> None:
        super().__init__(runs)
        self._tmp_path = tmp_path

    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
    ) -> WorkerResult:
        worker_ns = self._tmp_path / ".agent" / "workers" / unit.unit_id

        artifacts_dir = worker_ns / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "plan.md").write_text(
            MINIMAL_PLAN_MARKDOWN,
            encoding="utf-8",
        )

        handoffs_dir = worker_ns / "handoffs"
        handoffs_dir.mkdir(parents=True, exist_ok=True)
        (handoffs_dir / "DEVELOPMENT_RESULT.md").write_text(
            f"# Development Result for {unit.unit_id}\n\nCompleted successfully.\n"
        )

        return await super().run(unit, on_output=on_output, on_status=on_status)
