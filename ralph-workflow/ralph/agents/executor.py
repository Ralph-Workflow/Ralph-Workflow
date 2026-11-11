
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from ralph.agents.executor_error import ExecutorError
from ralph.agents.worker_result import WorkerResult
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus


@runtime_checkable
class AgentExecutor(Protocol):

    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
    ) -> WorkerResult: ...


__all__ = ["AgentExecutor", "ExecutorError", "WorkerResult"]
