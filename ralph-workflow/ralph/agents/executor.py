# Deprecated: Ralph-orchestrated workers are removed from the execution model;
# parallelism is owned by the developer agent's own sub-agents.
# See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""Agent executor protocol.

.. deprecated::
    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from ralph.agents.executor_error import ExecutorError
from ralph.agents.worker_result import WorkerResult
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus


@runtime_checkable
class AgentExecutor(Protocol):
    """Protocol that every agent executor implementation must satisfy.

    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.

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
