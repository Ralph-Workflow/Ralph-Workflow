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
        evidence_summary: Optional human-readable summary of per-channel
            evidence state at fire time, including tier labels and freshness.
        child_alive: Optional bool recording the corroborator's
            ``alive_by`` signal at the moment of the fire.

            - ``True``  -- the corroborator confirmed a live child
              (``AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS``,
              ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
              ``FRESH_HEARTBEAT_ONLY``, or ``STALE_LABEL_ONLY``). Normally
              dead code: the gate refinement in
              ``IdleWatchdog._is_no_progress_quiet`` defers the
              ``NO_PROGRESS_QUIET`` fire when the corroborator reports
              any alive_by signal. This path is defense-in-depth.
            - ``False`` -- the corroborator returned ``alive_by=None``
              (no live signal — i.e. the child is truly dead or
              missing). The conservative policy routes this to
              ``is_unavailable=True`` with
              ``unavailability_reason=STALE_CHILD_QUIET`` (Rule 2:
              exponential backoff to the next agent).
            - ``None``  -- the construction site did not set the
              field (legacy default). The conservative policy
              preserves the original ``STALE_CHILD_QUIET`` (Rule 2)
              behavior for backward-compat with the existing
              construction sites that do not set the field.
    """

    def __init__(
        self,
        reason: str,
        signal: int,
        *,
        evidence_summary: str | None = None,
        child_alive: bool | None = None,
    ) -> None:
        # The message may legitimately contain misleading tokens (e.g. the
        # word "timeout") to stress-test the classifier; the recovery decision
        # consults the typed attributes, never the message.
        message = f"Idle watchdog killed agent: reason={reason!r} signal={signal}"
        super().__init__(message)
        self.reason = reason
        self.signal = signal
        self.evidence_summary = evidence_summary
        self.child_alive = child_alive


__all__ = ["IdleWatchdogKilledError"]
