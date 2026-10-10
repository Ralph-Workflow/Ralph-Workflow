# Deprecated: Ralph-orchestrated workers are removed from the execution model;
# parallelism is owned by the developer agent's own sub-agents.
# See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""Worker failure error for parallel coordinator.

.. deprecated::
    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""

from __future__ import annotations


class WorkerFailureError(Exception):
    """Raised internally when a parallel worker fails.

    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
    """

    def __init__(self, unit_id: str, exit_code: int, error: str) -> None:
        super().__init__(error)
        self.unit_id = unit_id
        self.exit_code = exit_code
        self.error = error
