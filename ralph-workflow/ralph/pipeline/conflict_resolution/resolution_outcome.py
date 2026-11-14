from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.resolution_outcome import RetryLaterOutcome

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
        ResolutionTerminationReason,
    )


@dataclass(frozen=True)
class ResolutionOutcome(RetryLaterOutcome):
    succeeded: bool = False
    duration_seconds: float = 0.0
    last_activity_kind: str | None = None
    last_activity_at: float | None = None
    exhaustion_message: str | None = None
    _reason: ResolutionTerminationReason | str | None = None

    def __init__(
        self,
        succeeded: bool = False,
        reason: ResolutionTerminationReason | str | None = None,
        duration_seconds: float = 0.0,
        last_activity_kind: str | None = None,
        last_activity_at: float | None = None,
        unresolved_paths: tuple[str, ...] = (),
        failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE,
        exhaustion_message: str | None = None,
        on_change: str | None = None,
        until: float | None = None,
        terminal_reason: object | None = None,
    ) -> None:
        effective_on_change = (
            on_change
            if on_change is not None
            else (str(reason) if reason else "configuration changed")
        )
        object.__setattr__(self, "until", until)
        object.__setattr__(self, "on_change", effective_on_change)
        object.__setattr__(self, "terminal_reason", terminal_reason)
        object.__setattr__(self, "unresolved_paths", unresolved_paths)
        object.__setattr__(self, "failure_cause", failure_cause)
        object.__setattr__(self, "succeeded", succeeded)
        object.__setattr__(self, "duration_seconds", duration_seconds)
        object.__setattr__(self, "last_activity_kind", last_activity_kind)
        object.__setattr__(self, "last_activity_at", last_activity_at)
        object.__setattr__(self, "exhaustion_message", exhaustion_message)
        object.__setattr__(self, "_reason", reason)

    @property
    def reason(self) -> ResolutionTerminationReason | str | None:
        return self._reason

    def __bool__(self) -> bool:
        return self.succeeded
