"""Typed exception for an idle-watchdog kill of the agent process.

When the idle watchdog fires, it terminates the agent with a SIGTERM
(exit signal 15) and tags the exception with the watchdog's fire-reason
(idle, stalled, no_output, etc.). The recovery controller classifies the
failure from these typed attributes — not from substring-matching the
agent's stderr (the failure class that relabeled a SIGTERM as a
connectivity blip because the agent's stderr happened to contain the
word "timeout").

Use this exception type whenever the watchdog fires, so the classifier
sees ``isinstance(exc, IdleWatchdogKilledError)`` and consults
``exc.signal`` and ``exc.reason`` directly.
"""

from __future__ import annotations


class IdleWatchdogKilledError(Exception):
    """The idle watchdog killed the agent.

    Attributes:
        reason: The watchdog's authoritative fire-reason (e.g. ``"idle"``,
            ``"stalled"``, ``"no_output"``). NEVER derived from a text match.
        signal: The OS signal the watchdog used to terminate the agent
            (typically ``signal.SIGTERM`` == 15). Typed, not parsed from text.
    """

    def __init__(self, reason: str, signal: int) -> None:
        # The message may legitimately contain misleading tokens (e.g. the
        # word "timeout") to stress-test the classifier; the recovery decision
        # consults the typed attributes, never the message.
        message = f"Idle watchdog killed agent: reason={reason!r} signal={signal}"
        super().__init__(message)
        self.reason = reason
        self.signal = signal


__all__ = ["IdleWatchdogKilledError"]
