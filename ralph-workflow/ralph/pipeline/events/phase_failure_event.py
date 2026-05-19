"""Phase failure event for the pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.recovery.classifier import FailureCategory


@dataclass(frozen=True)
class PhaseFailureEvent:
    """Event emitted when a phase handler encounters a failure condition.

    This event carries a recoverable flag that determines how the reducer
    processes the failure:
    - recoverable=True: routes through _handle_agent_failure retry/fallback logic
    - recoverable=False: routes directly to the terminal failure phase

    Attributes:
        phase: Name of the phase that generated this event.
        reason: Human-readable description of what caused the failure.
        recoverable: Whether this failure should trigger retry/fallback (True)
            or act as a terminal decision (False).
        retry_in_session: When True and the agent's transport supports session
            resume, the recovery path should preserve the active session ID so
            the next retry continues in the same agent session rather than
            starting from scratch. Only meaningful when recoverable=True.
        failure_category: Optional pre-classified failure category for known
            phase-level failures such as artifact/proof validation errors. When
            present, recovery must honor this category directly instead of
            re-classifying the string reason heuristically.
    """

    phase: str
    reason: str
    recoverable: bool
    retry_in_session: bool = False
    failure_category: FailureCategory | None = None
