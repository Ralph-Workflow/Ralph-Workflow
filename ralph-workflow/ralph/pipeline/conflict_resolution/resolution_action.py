"""Action to take after an attempt or round in conflict resolution."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution.attempt_fault import (
    INFRASTRUCTURE_TERMINATION_REASONS,
)

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.session import ResolutionSession


class ResolutionAction(StrEnum):
    """Action to take after an attempt or round in conflict resolution."""

    CONTINUE_AGENT_ROUND = "CONTINUE_AGENT_ROUND"
    ESCALATE_TO_INTERVENTION = "ESCALATE_TO_INTERVENTION"
    ACCEPT_WORKTREE_REPAIR = "ACCEPT_WORKTREE_REPAIR"
    PROCEED_TO_FAST_FORWARD = "PROCEED_TO_FAST_FORWARD"


def select_resolution_action(
    session: ResolutionSession,
    round_index: int = 1,
    cap: int = 0,
    *,
    resolved: bool | None = None,
) -> ResolutionAction:
    """Select the typed resolution action for the current round and session state."""
    del round_index, cap
    if session.terminal_reason in INFRASTRUCTURE_TERMINATION_REASONS or bool(
        session.out_of_reach_paths
    ):
        return ResolutionAction.ESCALATE_TO_INTERVENTION

    is_resolved = (not session.unresolved_paths) if resolved is None else resolved
    if is_resolved:
        return (
            ResolutionAction.ACCEPT_WORKTREE_REPAIR
            if session.terminal_reason is not None
            else ResolutionAction.PROCEED_TO_FAST_FORWARD
        )

    return ResolutionAction.CONTINUE_AGENT_ROUND


__all__ = ["ResolutionAction", "select_resolution_action"]
