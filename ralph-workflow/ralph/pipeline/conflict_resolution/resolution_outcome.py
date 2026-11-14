from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
        ResolutionTerminationReason,
    )


@dataclass(frozen=True)
class ResolutionOutcome:
    succeeded: bool
    reason: ResolutionTerminationReason | str | None
    duration_seconds: float
    last_activity_kind: str | None
    last_activity_at: float | None
    unresolved_paths: tuple[str, ...]
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
    exhaustion_message: str | None = None

    def __bool__(self) -> bool:
        return self.succeeded
