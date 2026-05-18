"""Rebase state model for pipeline state."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel

_FROZEN = ConfigDict(frozen=True)


class RebaseState(RalphBaseModel):
    """State for git rebase operations."""

    model_config = _FROZEN

    pending: bool = False
    in_progress: bool = False
    completed: bool = False
