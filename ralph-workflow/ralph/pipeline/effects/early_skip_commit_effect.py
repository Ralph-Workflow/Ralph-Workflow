"""Early-skip-commit pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EarlySkipCommitEffect:
    """Effect to skip a commit phase before prompt materialization or agent invocation.

    Emitted by the orchestrator when the worktree has no pending work so the
    commit phase can advance via COMMIT_SKIPPED without creating a commit prompt
    or invoking a commit agent.
    """

    pass
