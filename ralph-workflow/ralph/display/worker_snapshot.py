# Deprecated: Ralph-orchestrated workers are removed from the execution model;
# parallelism is owned by the developer agent's own sub-agents.
# See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""Immutable projection of a single worker's execution state.

.. deprecated::
    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    """Immutable projection of a single worker's execution state.

    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
    """

    unit_id: str
    description: str
    status: str
    status_semantic: str
    started_at: datetime | None
    finished_at: datetime | None
    elapsed_s: float
    exit_code: int | None
    error_message: str | None
    dropped_lines: int = 0
