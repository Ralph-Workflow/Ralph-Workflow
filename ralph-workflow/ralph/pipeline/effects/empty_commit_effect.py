"""Empty-commit pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EmptyCommitPhaseRole = Literal["commit", "commit_cleanup"]


@dataclass(frozen=True)
class EmptyCommitEffect:
    """Effect to complete an empty commit-related phase without agent invocation.

    Emitted by the orchestrator when the worktree has no pending work. Commit
    cleanup advances through agent-success semantics, while a commit phase
    advances through commit-success semantics without creating a prompt or
    invoking an agent.
    """

    phase_role: EmptyCommitPhaseRole = "commit"
