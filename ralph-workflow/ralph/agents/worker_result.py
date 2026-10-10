# Deprecated: Ralph-orchestrated workers are removed from the execution model;
# parallelism is owned by the developer agent's own sub-agents.
# See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""Typed result returned by an agent executor.

.. deprecated::
    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WorkerResult"]


@dataclass(frozen=True)
class WorkerResult:
    """Immutable result returned by an executor after a work unit finishes.

    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.

    ``exit_code`` mirrors the subprocess exit status; 0 indicates success.
    ``final_message`` is the last status line emitted by the agent.
    ``duration_ms`` is the wall-clock elapsed time for the unit.
    """

    unit_id: str
    exit_code: int
    final_message: str
    duration_ms: int
