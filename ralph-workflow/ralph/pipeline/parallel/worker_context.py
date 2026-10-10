# Deprecated: Ralph-orchestrated workers are removed from the execution model;
# parallelism is owned by the developer agent's own sub-agents.
# See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""Optional runtime context injected into each parallel worker.

.. deprecated::
    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.display.activity_router import ActivityRouter
    from ralph.pipeline.parallel.mode import SameWorkspaceContext
    from ralph.pipeline.parallel.worker_log import WorkerLog


@dataclass(frozen=True)
class WorkerContext:
    """Optional runtime context injected into each parallel worker.

    .. deprecated::
        Deprecated: Ralph-orchestrated workers are removed from the execution model;
        parallelism is owned by the developer agent's own sub-agents. See
        docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
    """

    log: WorkerLog | None = None
    same_workspace: SameWorkspaceContext | None = None
    activity_router: ActivityRouter | None = None
