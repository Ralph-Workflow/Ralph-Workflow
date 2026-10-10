# Deprecated: Ralph-orchestrated workers are removed from the execution model;
# parallelism is owned by the developer agent's own sub-agents.
# See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""Per-worker log file metadata.

.. deprecated::
    Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents. See
    docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class WorkerLog:
    """Paths and identifiers for per-worker log files.

    .. deprecated::
        Deprecated: Ralph-orchestrated workers are removed from the execution model;
        parallelism is owned by the developer agent's own sub-agents. See
        docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
    """

    log_dir: Path
    run_id: str
