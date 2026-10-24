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

from ._activity_methods import (
    KNOWN_TOOL_CALL_VERBS,
    parse_tool_call_from_description,
)
from ._circumstantial_evidence import CircumstantialEvidence
from ._evidence_tier import (
    CHANNEL_DEFERS_BY_DEFAULT,
    CHANNEL_TIERS,
    ChannelEvidenceSummary,
    ChannelName,
    EvidenceSummary,
    EvidenceTier,
)
from ._post_exit_verdict import PostExitVerdict
from ._post_exit_watchdog import PostExitWatchdog
from ._stuck_classifier import ClassifyStuckInputs, StuckKind, classify_stuck
from ._subagent_identity import (
    MAX_SUBAGENT_REGISTRY_ENTRIES,
    SubagentIdentity,
    SubagentPidRegistry,
)
from ._watch_loop_base import WatchLoopBase
from ._workspace_change_kind import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    WorkspaceChangeKind,
)
from .corroboration_snapshot import (
    CorroborationSnapshot,
    WaitingCorroborator,
)
from .idle_watchdog import EXPECTED_FIRE_REASONS, IdleWatchdog
from .timeout_policy import TimeoutPolicy
from .waiting_status_event import WaitingStatusEvent, WaitingStatusListener
from .waiting_status_kind import WaitingStatusKind
from .watchdog_fire_reason import WatchdogFireReason
from .watchdog_verdict import WatchdogVerdict

__all__ = [
    "CHANNEL_DEFERS_BY_DEFAULT",
    "CHANNEL_TIERS",
    "DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS",
    "EXPECTED_FIRE_REASONS",
    "KNOWN_TOOL_CALL_VERBS",
    "MAX_SUBAGENT_REGISTRY_ENTRIES",
    "AliveBy",
    "ChannelEvidenceSummary",
    "ChannelName",
    "CircumstantialEvidence",
    "ClassifyStuckInputs",
    "CorroborationSnapshot",
    "EvidenceSummary",
    "EvidenceTier",
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
    "WatchLoopBase",
    "WatchdogFireReason",
    "WatchdogVerdict",
    "WorkspaceChangeKind",
    "classify_stuck",
    "parse_tool_call_from_description",
]
