"""Shared numeric defaults for agent timeout policy and child-liveness configuration.

These constants are the single source of truth for all timeout and child-liveness
numeric defaults. They are imported by ``ralph.agents.idle_watchdog.TimeoutPolicy``
(dataclass field defaults), ``ralph.agents.invoke`` (child-liveness TTL module-level
constants), and ``ralph.config.models.GeneralConfig`` (field defaults), as well as
``ralph.mcp.websearch.backends.brave`` / ``searxng``
(``WEBSEARCH_BACKEND_TIMEOUT_SECONDS``) and ``ralph.mcp.websearch.backends.ddgs`` /
``exa`` / ``tavily`` (``WEBSEARCH_SDK_TIMEOUT_SECONDS``, routed through
``ralph.mcp.websearch._bounded_sdk_call.with_timeout``).

Changing a constant here automatically propagates to all consumers so they
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

#: Default per-channel activity evidence TTL. Governs how long after a
#: non-stdout event (MCP tool call, subagent progress, workspace file
#: change) the corresponding channel still counts as live activity for
#: the NO_OUTPUT_DEADLINE verdict. While ANY non-stdout channel is
#: fresher than this TTL, the watchdog defers the NO_OUTPUT_DEADLINE
#: fire and returns CONTINUE so a productive session that emits little
#: stdout is not killed as idle. The default of 30s is well under the
#: 300s idle-timeout default and the 600s no-progress ceiling, so a
#: silent subagent (or silent MCP path) is detected at the regular
#: idle deadline.
#: Set to 0.0 to disable the activity-aware verdict and restore the
#: legacy stdout-only NO_OUTPUT_DEADLINE behavior.
AGENT_IDLE_ACTIVITY_EVIDENCE_TTL_SECONDS: float = 30.0

#: Default per-kind workspace file-change weights. Each value is
#: BINARY: weight==0.0 means the change is dropped (does not defer
#: the NO_OUTPUT_DEADLINE verdict); weight==1.0 means the change
#: counts as full activity. Intermediate weights are rejected by
#: the validator today and reserved for a future fractional-TTL
#: feature.
#:
#: The default policy is conservative: only source-code changes
#: count. Operators who relied on log-file activity to defer the
#: verdict can opt in by overriding this dict (see
#: ``GeneralConfig.agent_workspace_change_weights`` and the
#: ``[general] agent_workspace_change_weights = {...}`` key in
#: ``ralph-workflow.toml``).
DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS: dict[str, float] = {
    "source": 1.0,
    "log": 0.0,
    "cache": 0.0,
    "artifact": 0.0,
    "other": 0.0,
}

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

#: Hard upper bound on the post-final-frame SSE drain grace (the time the
#: server waits for the final frame's write to complete before closing the
#: connection). A slow client cannot outrun the dispatch cap by holding
#: the receive buffer open past the dispatch — the server gives the final
#: frame at most this many milliseconds to drain. Tuned for LAN clients.
SSE_DRAIN_CEILING_MS: int = 5_000

#: Hard upper bound on the SIGTERM-then-SIGKILL escalation grace. When a
#: child must be terminated, the server first sends SIGTERM and waits at
#: most this long before escalating to SIGKILL. Tuned for fast subprocess
#: shutdown.
KILL_ESCALATION_CEILING_MS: int = 5_000

#: Default per-call HTTP timeout for built-in websearch backends (Brave, SearXNG).
#: Sourced by ralph.mcp.websearch.backends.brave and ralph.mcp.websearch.backends.searxng
#: to replace the previously hard-coded _TIMEOUT_SECONDS = 10.0 literals. One source of
#: truth so a 10s wedge cannot drift into a backend. Overridable via the
#: ``[web_search]`` block of mcp.toml (WebSearchConfig.web_search_default_timeout_seconds).
#: Must be > 0; the import-time invariant below rejects non-positive values.
WEBSEARCH_BACKEND_TIMEOUT_SECONDS: float = 10.0

#: Default per-call timeout for third-party-SDK-backed websearch backends (DDGS, Exa,
#: Tavily). The SDKs wrap their own HTTP client; a hung SDK can otherwise block the
#: dispatch worker for the full client timeout (330s), so the call is routed through
#: ralph.mcp.websearch._bounded_sdk_call.with_timeout. Slightly more generous than the
#: HTTP backend default because the SDKs add their own connection layer. Must be
#: >= WEBSEARCH_BACKEND_TIMEOUT_SECONDS; the import-time invariant below rejects
#: values that violate that ordering.
WEBSEARCH_SDK_TIMEOUT_SECONDS: float = 30.0

#: Default poll interval for subagent output capture. The watchdog polls
#: observable subagent log streams at this cadence and ingests only new lines
#: since the last poll.
SUBAGENT_OUTPUT_POLL_INTERVAL_SECONDS: float = 1.0

#: Default enabled state for the process monitor. When false, the watchdog
#: does not scan the process tree and subagent liveness is inferred only from
#: progress signals already received by the MCP server.
PROCESS_MONITOR_ENABLED: bool = True

#: Default enabled state for subagent output capture. When false, the watchdog
#: does not poll subagent log streams; subagent output is not treated as
#: first-party evidence.
SUBAGENT_OUTPUT_CAPTURE_ENABLED: bool = True

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

#: Short ceiling on cumulative WAITING_ON_CHILD time when the only evidence of a
#: running child is its OS-process-tree existence (alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS
#: with no scoped child evidence and no first-party evidence channels active). Fires
#: CHILDREN_PERSIST_TOO_LONG in ~300s instead of waiting for the 600s no-progress
#: ceiling. The 300s default tolerates the typical 95th-percentile sub-step latency
#: (file reads, MCP startup, model load, multi-step tool calls) so the ceiling does
#: not fire while the agent is genuinely making forward progress; a wedged-but-alive
#: opencode subprocess with zero observable progress signals is still detected well
#: before the 600s no-progress ceiling. The previous 120s default produced the
#: 'dumb-kill' regression documented in wt-012 where the watchdog fired at
#: cumulative=159s, idle_elapsed=120s while the agent was reading
#: ``.agent/CURRENT_PROMPT.md`` (a legitimate sub-step well below 120s of work).
#: The smart-verdict gate in the watchdog (StuckClassifier) further protects against
#: premature fires by deferring the verdict while any first-party channel is fresh
#: or while the agent is in a waiting state. Set to ``None`` to disable the override
#: and fall back to the no-progress ceiling.
OS_DESCENDANT_ONLY_CEILING_SECONDS: float | None = 300.0

#: Earlier SUSPECTED_FROZEN threshold when alive_by is OS_DESCENDANT_ONLY_STALE_PROGRESS.
#: The watchdog fires the suspect event at min(suspect_waiting_on_child_seconds,
#: OS_DESCENDANT_ONLY_SUSPECT_SECONDS) so the operator sees escalation at ~60s
#: instead of waiting for the standard 600s suspicion threshold. Set to ``None``
#: to disable and use the standard suspect threshold.
OS_DESCENDANT_ONLY_SUSPECT_SECONDS: float | None = 60.0

#: A known descendant PID with 0 user+system CPU time over this rolling window is
#: reported by the read-loop corroborator as alive_by=CPU_IDLE_WHILE_ALIVE. The
#: override short-circuits the OS-descendant-only ceiling and falls back to the
#: no-progress ceiling (180.0 default). The 60s default tolerates up to 60s of
#: sub-step quiescence (I/O wait, GC pause, network call) which is within the
#: typical 95th-percentile sub-step latency. Set to ``None`` to disable the CPU
#: probe and rely solely on the OS-descendant-only ceiling.
CPU_IDLE_SECONDS: float | None = 60.0

#: The per-run .agent/raw/{safe_id}.log file is reported as alive_by=LOG_STALE_WHILE_ALIVE
#: when its size has not grown for this many seconds. The override short-circuits
#: the OS-descendant-only ceiling and falls back to the no-progress ceiling. The 30s
#: default is aggressive but appropriate for detecting a wedged subprocess that is
#: not writing any output. Set to ``None`` to disable the log-growth probe;
#: the probe gracefully no-ops when the raw log file is absent.
LOG_GROWTH_SECONDS: float | None = 30.0

#: Default fast no-progress ceiling: shorter WAITING ceiling when child is alive but
#: not making forward progress (heartbeat-only, stale-label, or OS-descendant-only)
#: and stdout has been idle. None disables it.
NO_PROGRESS_QUIET_SECONDS: float | None = 120.0

#: Default dumb-kill floor: NO_PROGRESS_QUIET cannot fire within the first N seconds
#: of an agent run. This prevents the watchdog from killing a recently-launched
#: agent that is doing real thinking work (planning, exploration, dispatching
#: subagents) but has not yet produced first-party activity evidence. The 120.0s
#: default matches the OS_DESCENDANT_ONLY_CEILING default. The
#: SESSION_CEILING_EXCEEDED reason is unaffected (operator-set hard cap).
#: Set to ``None`` to disable the floor (not recommended).
NO_PROGRESS_QUIET_MINIMUM_INVOCATION_SECONDS: float | None = 120.0

#: Default fast-fire window for the new NO_OUTPUT_AT_START watchdog reason. When
#: the agent has been alive for this many seconds with zero recorded activity
#: (no stdout, no tool call, no file change, no subagent output) the watchdog
#: fires NO_OUTPUT_AT_START instead of waiting for the 600s cumulative
#: no-progress ceiling. Set to None to opt out.
# 30s is well under the 60s 95th-percentile first-token latency for opencode and
# Claude Code while still short enough to fall over to the next agent before the
# cumulative ceiling is reached.
NO_OUTPUT_AT_START_SECONDS: float | None = 30.0

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

# ---------------------------------------------------------------------------
# Import-time invariants
# ---------------------------------------------------------------------------
# Use ``if``/``raise RuntimeError`` (NOT bare ``assert``) so the guards survive
# ``python -O`` and prevent a future regression from silently emptying the
# constant set or violating the SDK >= HTTP ordering. These checks run on
# import, are not stripped by ``-O``, and pin the contract that operators
# rely on.

if not (
    WEBSEARCH_BACKEND_TIMEOUT_SECONDS > 0.0
    and WEBSEARCH_SDK_TIMEOUT_SECONDS > 0.0
):
    raise RuntimeError(
        'websearch timeout constants must be positive; '
        f'got WEBSEARCH_BACKEND_TIMEOUT_SECONDS={WEBSEARCH_BACKEND_TIMEOUT_SECONDS!r} '
        f'and WEBSEARCH_SDK_TIMEOUT_SECONDS={WEBSEARCH_SDK_TIMEOUT_SECONDS!r}'
    )
if not WEBSEARCH_SDK_TIMEOUT_SECONDS >= WEBSEARCH_BACKEND_TIMEOUT_SECONDS:
    raise RuntimeError(
        'WEBSEARCH_SDK_TIMEOUT_SECONDS must be >= WEBSEARCH_BACKEND_TIMEOUT_SECONDS; '
        f'got SDK={WEBSEARCH_SDK_TIMEOUT_SECONDS!r} < HTTP={WEBSEARCH_BACKEND_TIMEOUT_SECONDS!r}'
    )
