from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.resolution_attempt_key import ResolutionAttemptKey


@dataclass(frozen=True)
class ResolutionGateDecision:
    key: ResolutionAttemptKey
    blocked: bool
    reason: str | None
    observed_at_ms: int
