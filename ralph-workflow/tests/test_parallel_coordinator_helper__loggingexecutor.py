from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.executor import WorkerResult
from ralph.pipeline.worker_state import WorkerStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.work_units import WorkUnit


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
