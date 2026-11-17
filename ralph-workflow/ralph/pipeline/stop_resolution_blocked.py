from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause

if TYPE_CHECKING:
    from ralph.pipeline.key_unavailable import KeyUnavailable
    from ralph.pipeline.resolution_attempt_key import ResolutionAttemptKey


__all__ = (
    "DeferredStopResolution",
    "StopResolutionBlocked",
)


@dataclass(frozen=True)
class StopResolutionBlocked:
    reason: str
    key: ResolutionAttemptKey | KeyUnavailable


@dataclass(frozen=True)
class DeferredStopResolution(StopResolutionBlocked):
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
