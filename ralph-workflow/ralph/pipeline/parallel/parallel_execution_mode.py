# Deprecated: Ralph-orchestrated workers are removed from the execution model;
# parallelism is owned by the developer agent's own sub-agents.
# See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""Supported parallel execution modes.

.. deprecated::
    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""

from enum import StrEnum


class ParallelExecutionMode(StrEnum):
    """Supported parallel execution modes.

    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.

    In v1 only SAME_WORKSPACE is supported. Workers share the single checked-out
    repository root and are isolated only by edit-area path restrictions and
    per-worker artifact namespaces — not by filesystem isolation or separate git checkouts.
    """

    SAME_WORKSPACE = "same_workspace"


__all__ = ["ParallelExecutionMode"]
