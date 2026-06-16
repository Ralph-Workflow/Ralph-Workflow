"""Idle watchdog for agent timeout policy enforcement.

IdleWatchdog owns the in-stream idle/deadline logic and exposes a single evaluate()
method. All wall-clock decisions go through the injected Clock so the watchdog is
fully testable without real sleeps (FakeClock) per CLAUDE.md test performance policy.

This module is the counterpart to ralph.agents.post_exit_watchdog.PostExitWatchdog,
which owns post-exit (post-EOF) wall-clock timeouts. Together these two watchdogs
cover every wall-clock timeout fire path in the agent invocation system; no ad-hoc
clock.monotonic()/clock.sleep() loops are allowed in invoke.py.

IdleWatchdog owns fire reasons: SESSION_CEILING_EXCEEDED, NO_OUTPUT_DEADLINE,
and CHILDREN_PERSIST_TOO_LONG. PostExitWatchdog owns: PROCESS_EXIT_HANG and
DESCENDANT_HANG. See ralph.agents.post_exit_watchdog for the post-exit family.
"""

from ralph.process.child_liveness import AliveBy

from ._stuck_classifier import StuckKind, classify_stuck
from .corroboration_snapshot import (
    ChannelEvidenceSummary,
    CorroborationSnapshot,
    WaitingCorroborator,
)
from .idle_watchdog import IdleWatchdog
from .timeout_policy import TimeoutPolicy
from .waiting_status_event import WaitingStatusEvent, WaitingStatusListener
from .waiting_status_kind import WaitingStatusKind
from .watchdog_fire_reason import WatchdogFireReason
from .watchdog_verdict import WatchdogVerdict

__all__ = [
    "AliveBy",
    "ChannelEvidenceSummary",
    "CorroborationSnapshot",
    "IdleWatchdog",
    "StuckKind",
    "TimeoutPolicy",
    "WaitingCorroborator",
    "WaitingStatusEvent",
    "WaitingStatusKind",
    "WaitingStatusListener",
    "WatchdogFireReason",
    "WatchdogVerdict",
    "classify_stuck",
]
