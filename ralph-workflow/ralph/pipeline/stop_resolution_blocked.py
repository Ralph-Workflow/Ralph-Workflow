from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.key_unavailable import KeyUnavailable
    from ralph.pipeline.resolution_attempt_key import ResolutionAttemptKey


@dataclass(frozen=True)
class StopResolutionBlocked:
    reason: str
    key: ResolutionAttemptKey | KeyUnavailable
