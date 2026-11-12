from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.invoke._failure_origin import FailureOrigin


class IdleWatchdogKilledError(Exception):
    def __init__(
        self,
        reason: str,
        signal: int,
        *,
        evidence_summary: str | None = None,
        child_alive: bool | None = None,
        resumable_session_id: str | None = None,
        issuer: str | None = None,
        runtime_event: FailureOrigin | None = None,
    ) -> None:
        message = f"Idle watchdog killed agent: reason={reason!r} signal={signal}"
        super().__init__(message)
        self.reason = reason
        self.signal = signal
        self.evidence_summary = evidence_summary
        self.child_alive = child_alive
        self.resumable_session_id = resumable_session_id
        self.failure_origin = "watchdog_observation"
        self.issuer = issuer or reason
        self.runtime_event = runtime_event


__all__ = ["IdleWatchdogKilledError"]
