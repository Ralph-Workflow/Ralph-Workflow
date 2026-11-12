from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.recovery.classifier import FailureCategory


@dataclass(frozen=True)
class PhaseFailureEvent:
    phase: str
    reason: str
    recoverable: bool
    retry_in_session: bool = False
    failure_category: FailureCategory | None = None
    skip_same_agent_retries: bool = False
    workspace_changed: bool = False
