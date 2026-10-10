# Deprecated: Ralph-orchestrated workers are removed from the execution model;
# parallelism is owned by the developer agent's own sub-agents.
# See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""Worker started event for the pipeline.

.. deprecated::
    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerStartedEvent:
    """Emitted when a parallel worker begins execution.

    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
    """

    unit_id: str
