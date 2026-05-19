"""Agent executor protocol."""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from ralph.agents.executor_error import ExecutorError
from ralph.agents.worker_result import WorkerResult
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus


@runtime_checkable
class AgentExecutor(Protocol):
    """Protocol that every agent executor implementation must satisfy.

    Implementors receive a ``WorkUnit``, stream output via ``on_output``,
    report status transitions via ``on_status``, and return a ``WorkerResult``
    when the unit completes or fails.
    """

    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
    ) -> WorkerResult: ...


__all__ = ["AgentExecutor", "ExecutorError", "WorkerResult"]
