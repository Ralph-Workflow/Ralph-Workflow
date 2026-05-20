"""CommitCleanup artifact model for commit hardening."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.mcp.artifacts._commit_cleanup_action import CommitCleanupAction
from ralph.pydantic_compat import RalphBaseModel


class CommitCleanup(RalphBaseModel):
    """Validated schema for a commit cleanup artifact payload."""

    model_config = ConfigDict(extra="forbid")

    analysis_complete: bool
    actions: list[CommitCleanupAction]
    reason: str | None = None
