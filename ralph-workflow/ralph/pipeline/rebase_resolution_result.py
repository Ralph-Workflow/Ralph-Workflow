from __future__ import annotations

from typing import NamedTuple

from ralph.git.resolution_failure_cause import ResolutionFailureCause

__all__ = ["RebaseResolutionResult"]


class RebaseResolutionResult(NamedTuple):
    succeeded: bool
    reason: str | None
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
