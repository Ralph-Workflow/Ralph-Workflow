"""InactivityTimeoutOpts — optional parameters for AgentInactivityTimeoutError."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog import WatchdogFireReason
    from ralph.agents.invoke._failure_origin import FailureOrigin


@dataclass(frozen=True)
class InactivityTimeoutOpts:
    """Optional parameters for AgentInactivityTimeoutError."""

    reason: WatchdogFireReason | None = None
    session_resume_safe: bool = False
    resumable_session_id: str | None = None
    diagnostic: dict[str, str | int | float | bool | list[object]] | None = None
    runtime_event: FailureOrigin | None = None


__all__ = ["InactivityTimeoutOpts"]
