"""PolicyFlag — policy flags that may modify prompt rendering."""

from __future__ import annotations

from enum import StrEnum


class PolicyFlag(StrEnum):
    """Policy flags that may modify prompt rendering."""

    NO_EDIT = "no_edit"
    ALLOW_SHELL = "allow_shell"
    ALLOW_GIT_READ = "allow_git_read"
    ALLOW_GIT_WRITE = "allow_git_write"
    # Deprecated: Ralph-orchestrated workers are removed from the execution model;
    # parallelism is owned by the developer agent's own sub-agents.
    # See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'.
    ALLOW_PARALLEL_WORKERS = "allow_parallel_workers"
    """Deprecated: Ralph-orchestrated workers are removed from the execution model;
    parallelism is owned by the developer agent's own sub-agents.
    See docs/sphinx/concepts.md §'Deprecated: Ralph Workflow-orchestrated workers'."""
    ALLOW_NETWORK = "allow_network"
    ALLOW_ENV_READ = "allow_env_read"


__all__ = ["PolicyFlag"]
