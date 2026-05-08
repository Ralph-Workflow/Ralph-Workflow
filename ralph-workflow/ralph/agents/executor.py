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
    """Immutable result returned by an executor after a work unit finishes.

    ``exit_code`` mirrors the subprocess exit status; 0 indicates success.
    ``final_message`` is the last status line emitted by the agent.
    ``duration_ms`` is the wall-clock elapsed time for the unit.
    """

    unit_id: str
    exit_code: int
    final_message: str
    duration_ms: int


class ExecutorError(Exception):
    """Raised when an executor encounters an unrecoverable failure."""


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
