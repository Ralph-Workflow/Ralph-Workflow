"""Idle watchdog for agent timeout policy enforcement.

IdleWatchdog owns the in-stream idle/deadline logic and exposes a single evaluate()
method. All wall-clock decisions go through the injected Clock so the watchdog is
fully testable without real sleeps (FakeClock) per CLAUDE.md test performance policy.

This module is the canonical home for the watchdog subsystem. It exposes two
canonical owner classes:

  - :class:`IdleWatchdog` (in-stream) — in ``.idle_watchdog`` — the sole owner
    of in-stream fire decisions.
  - :class:`PostExitWatchdog` (post-exit) — in ``._post_exit_watchdog`` — the
    sole owner of post-EOF fire decisions. Re-exported here so callers can
    ``from ralph.agents.idle_watchdog import PostExitWatchdog``.

Together these two watchdogs cover every wall-clock timeout fire path in the
agent invocation system; no ad-hoc clock.monotonic()/clock.sleep() loops are
allowed in invoke.py. The drift audit
(``ralph.testing.audit_watchdog_drift``) enforces this single-owner invariant.

IdleWatchdog owns fire reasons: SESSION_CEILING_EXCEEDED, NO_OUTPUT_DEADLINE,
and CHILDREN_PERSIST_TOO_LONG. PostExitWatchdog owns: PROCESS_EXIT_HANG and
DESCENDANT_HANG. See ``._post_exit_watchdog`` for the post-exit family.
"""

from ralph.process.child_liveness import AliveBy

from ._post_exit_verdict import PostExitVerdict
from ._post_exit_watchdog import PostExitWatchdog
from ._stuck_classifier import StuckKind, classify_stuck
from ._subagent_identity import SubagentIdentity, SubagentPidRegistry
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
    "PostExitVerdict",
    "PostExitWatchdog",
    "StuckKind",
    "SubagentIdentity",
    "SubagentPidRegistry",
    "TimeoutPolicy",
    "WaitingCorroborator",
    "WaitingStatusEvent",
    "WaitingStatusKind",
    "WaitingStatusListener",
    "WatchdogFireReason",
    "WatchdogVerdict",
    "classify_stuck",
]
