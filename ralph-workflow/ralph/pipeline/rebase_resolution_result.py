from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
        ResolutionTerminationReason,
    )

__all__ = ["RebaseResolutionResult"]


class RebaseResolutionResult(tuple[bool, str | None, ResolutionFailureCause]):
    terminal_reason: ResolutionTerminationReason | None

    def __new__(
        cls,
        succeeded: bool,
        reason: str | None,
        failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE,
        terminal_reason: ResolutionTerminationReason | None = None,
    ) -> RebaseResolutionResult:
        obj = super().__new__(cls, (succeeded, reason, failure_cause))
        obj.terminal_reason = terminal_reason
        return obj

    @property
    def succeeded(self) -> bool:
        return bool(self[0])

    @property
    def reason(self) -> str | None:
        val = self[1]
        return str(val) if isinstance(val, str) else None

    @property
    def failure_cause(self) -> ResolutionFailureCause:
        return self[2]
