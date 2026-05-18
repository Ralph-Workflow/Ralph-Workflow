"""PolicyOutcome — normalized policy outcome payload."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.protocol._policy_outcome_status import PolicyOutcomeStatus


@dataclass(frozen=True)
class PolicyOutcome:
    """Normalized policy outcome payload."""

    status: PolicyOutcomeStatus
    reason: str | None = None
    restriction: str | None = None


__all__ = ["PolicyOutcome"]
