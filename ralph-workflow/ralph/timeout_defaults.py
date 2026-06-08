"""Shared numeric defaults for agent timeout policy and child-liveness configuration.

These constants are the single source of truth for all timeout and child-liveness
numeric defaults. They are imported by ``ralph.agents.idle_watchdog.TimeoutPolicy``
(dataclass field defaults), ``ralph.agents.invoke`` (child-liveness TTL module-level
constants), and ``ralph.config.models.GeneralConfig`` (field defaults).

Changing a constant here automatically propagates to all three layers so they
cannot drift independently.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Idle / session timeout defaults
# ---------------------------------------------------------------------------

#: Default idle timeout: maximum seconds without agent output before firing.
IDLE_TIMEOUT_SECONDS: float = 300.0

#: Default drain window duration before firing NO_OUTPUT_DEADLINE.
DRAIN_WINDOW_SECONDS: float = 0.5

#: Default post-tool-result progression budget. When set, the idle
#: watchdog fires STALLED_AFTER_TOOL_RESULT if no follow-up
#: STREAM_DELTA/OUTPUT_LINE activity arrives within this many seconds
#: of a tool result. The default of 120s is generous enough to cover
#: the typical 60s 95th-percentile tool-result-to-output-line latency
#: in production while still detecting the post-tool-result wedge in
#: ~120s rather than waiting for the 300s idle-timeout default.
#: Set to ``None`` to opt out and preserve the legacy 300s
#: NO_OUTPUT_DEADLINE-only behavior.
POST_TOOL_RESULT_PROGRESSION_SECONDS: float | None = 120.0

#: Default hard ceiling on cumulative WAITING_ON_CHILD time.
MAX_WAITING_ON_CHILD_SECONDS: float = 1800.0

#: Default absolute session wall-clock ceiling (hard force-cut). None disables it.
#: Set to 55 minutes so a runaway single invocation cannot run unbounded (the
#: incident that motivated this ran ~5 hours). The soft wrap-up nag fires earlier
#: (see ``SESSION_SOFT_WRAPUP_SECONDS``), leaving a margin under the nominal 1h
#: budget. Per-invocation and config-overridable; recovery continues after a cut.
MAX_SESSION_SECONDS: float | None = 3300.0

#: Soft wrap-up threshold: once a single invocation has run this long, MCP tool
#: results carry a "finish up / declare_complete soon" banner so the agent winds
#: down before the hard ``MAX_SESSION_SECONDS`` force-cut. None disables the nag.
SESSION_SOFT_WRAPUP_SECONDS: float | None = 3000.0

#: Repeated-error circuit breaker: fire after this many consecutive identical
#: error fingerprints with no intervening forward progress. None disables the rule.
REPEATED_ERROR_CONSECUTIVE_THRESHOLD: int | None = 5

#: Repeated-error circuit breaker: fire after this many occurrences of one error
#: fingerprint within ``REPEATED_ERROR_WINDOW_SECONDS`` (catches loops that
#: interleave cosmetic output). None disables the rule.
REPEATED_ERROR_WINDOW_COUNT: int | None = 8

#: Rolling window for ``REPEATED_ERROR_WINDOW_COUNT``. None disables the window rule.
REPEATED_ERROR_WINDOW_SECONDS: float | None = 600.0

#: Default bound for git subprocesses invoked via ``ralph.git.subprocess_runner.run_git``
#: when a caller does not specify an explicit timeout. Git is run in non-interactive
#: (batch) mode so a network/credential prompt fails fast rather than hanging; this
#: ceiling is the backstop for a slow-but-non-blocking op (e.g. status over large
#: vendor submodules). The process tree is killed on expiry.
GIT_SUBPROCESS_TIMEOUT_SECONDS: float = 120.0

#: Default per-call timeout for the exec MCP tool family (exec/unsafe_exec). Set
#: above the 60s combined ``make verify`` budget so an agent running verification
#: (or a slow git op) through exec does not time out on every call. This is the one
#: source of truth: both the exec handler default and the advertised tool-schema
#: default derive from it, so the hint shown to the agent cannot drift from the
#: behavior. Per-call ``timeout_ms`` overrides it; the process tree is killed on
#: expiry, so the server stays bounded regardless.
EXEC_DEFAULT_TIMEOUT_MS: int = 90_000

#: Hard upper bound on a per-call exec ``timeout_ms`` (and on the suggested retry
#: timeout). An agent may raise ``timeout_ms`` to recover from a timeout, but never
#: above this — the MCP client request timeout is derived to exceed it, so a
#: legitimately long exec can never outrun the client (which would re-raise the
#: ``-32001 Request timed out`` storm). 5 minutes is generous for any single command.
EXEC_MAX_TIMEOUT_MS: int = 300_000

#: Default poll interval for the read loop.
IDLE_POLL_INTERVAL_SECONDS: float = 0.05

#: Default grace window after parent exits normally.
PARENT_EXIT_GRACE_SECONDS: float = 5.0

#: Default ceiling for descendant-wait after parent exits.
DESCENDANT_WAIT_TIMEOUT_SECONDS: float = 30.0

#: Default poll interval for descendant-wait / process-exit-wait loops.
DESCENDANT_WAIT_POLL_SECONDS: float = 0.5

#: Default ceiling for waiting on subprocess exit after stdout closes.
PROCESS_EXIT_WAIT_SECONDS: float = 30.0

#: Default cadence for WAITING_ON_CHILD periodic status events.
WAITING_STATUS_INTERVAL_SECONDS: float = 30.0

#: Default suspicion threshold: cumulative WAITING time before SUSPECTED_FROZEN event.
#: None disables suspicion.
SUSPECT_WAITING_ON_CHILD_SECONDS: float | None = 600.0

#: Default no-progress ceiling: shorter WAITING ceiling when child is alive but not
#: making forward progress (heartbeat-only, stale-label, or OS-descendant-only).
#: None disables the no-progress ceiling.
MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS: float | None = 600.0

# ---------------------------------------------------------------------------
# Child-liveness TTL defaults
# ---------------------------------------------------------------------------

#: Maximum seconds since last child progress signal before treated as not-progressing.
CHILD_PROGRESS_TTL_SECONDS: float = 45.0

#: Maximum seconds since last child heartbeat before heartbeat is stale.
CHILD_HEARTBEAT_TTL_SECONDS: float = 15.0

#: Grace period during which a child label persists after evidence goes stale.
CHILD_STALE_LABEL_TTL_SECONDS: float = 10.0

#: Reconciliation window after stdout EOF for late terminal acks.
CHILD_EXIT_RECONCILE_SECONDS: float = 5.0
