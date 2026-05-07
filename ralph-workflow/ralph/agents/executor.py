"""Protocol and result types for the agent executor abstraction.

Defines ``AgentExecutor`` (the ``Protocol`` every executor must satisfy),
``WorkerResult`` (the typed return value after a work unit completes), and
``ExecutorError`` (the base exception for unrecoverable executor failures).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus


@dataclass(frozen=True)
class WorkerResult:
    unit_id: str
    exit_code: int
    final_message: str
    duration_ms: int


class ExecutorError(Exception):
    pass


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
