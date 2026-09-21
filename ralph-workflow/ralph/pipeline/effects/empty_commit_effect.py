"""Empty-commit pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmptyCommitEffect:
    """Effect to complete an empty commit phase without agent invocation.

    Emitted by the orchestrator when the worktree has no pending work so the
    commit phase can advance via COMMIT_SUCCESS without creating a commit prompt
    or invoking a commit agent.
    """

    pass
