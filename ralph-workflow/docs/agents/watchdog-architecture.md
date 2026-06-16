# Watchdog Architecture

The watchdog subsystem is the single centralized source of truth for
in-stream and post-exit fire decisions. This document is the
architectural reference; the AST contract test in
`tests/agents/idle_watchdog/test_watchdog_recovery_contract.py` and
the drift audit `ralph.testing.audit_watchdog_drift` are the
enforcement.

## Overview

Two watchdog classes own every wall-clock fire decision:

- **`IdleWatchdog`** (in-stream) — at
  `ralph/agents/idle_watchdog/idle_watchdog.py` — the sole owner of
  in-stream fire decisions (NO_OUTPUT_DEADLINE,
  CHILDREN_PERSIST_TOO_LONG, SESSION_CEILING_EXCEEDED,
  NO_PROGRESS_QUIET, STALLED_AFTER_TOOL_RESULT, REPEATED_ERROR_LOOP).
- **`PostExitWatchdog`** (post-exit) — at
  `ralph/agents/idle_watchdog/_post_exit_watchdog.py` — the sole
  owner of post-EOF fire decisions (PROCESS_EXIT_HANG, DESCENDANT_HANG).

All watchdog code lives under `ralph/agents/idle_watchdog/`. The
consolidation removes the dead legacy `old_watchdog.py` (was at the
ralph-workflow root) and moves `post_exit_watchdog.py` and
`post_exit_verdict.py` into the subpackage. The drift audit
enforces this single-owner invariant.

## Two main rules for retry

The recovery controller's failure handling has EXACTLY two outcomes
for AGENT-category failures. There is no third state.

**Rule 1 (same-agent retry)** — when an AGENT-category failure is
NOT marked unavailable, the recovery controller increments the
chain's `retries` counter and re-invokes the same agent. The
failure is classified as `failure.counts_against_budget=True` and
`failure.is_unavailable=False`; the controller's
`_handle_retry_progression` enters the
`should_retry_in_chain` branch at `controller.py:555-562` and
returns `_apply_chain_retry(state, phase, chain, ...)`. The pipeline
does not advance to the next agent and does not mark the current
agent on cooldown.

**Rule 2 (exponential backoff to next agent)** — when an
AGENT-category failure IS marked unavailable (e.g.
`IdleWatchdogKilledError(reason='no_progress_quiet', signal=15)`),
the recovery controller marks the agent on cooldown via
`AgentUnavailabilityTracker.mark_unavailable` (per-reason
`ReasonBackoffPolicy` exponential backoff capped at
`max_backoff_ms`) and advances `chain.current_index` to the next
available agent in the chain (cyclic, `wrap=True` re-arming). The
controller's `_handle_retry_progression` enters the
`next_available_index is not None` branch at
`controller.py:563-603`.

The pipeline NEVER exits because of unavailability. When all agents
in the chain are on cooldown, the controller enters the wait state
(see below).

## Never-exit invariant

The recovery controller's `_handle_retry_progression` has the
all-agents-unavailable branch at `controller.py:606-637` that sets
`state.is_waiting_state=True` and
`state.last_retry_delay_ms=<earliest_cooldown>` and does NOT call
`_enter_phase_failed`. The run loop (`ralph/pipeline/run_loop.py`)
sleeps on `last_retry_delay_ms` and re-enters the same phase. The
`wrap=True` re-arming in `_next_available_agent_index`
(`controller.py:653-674`) reconsiders earlier agents whose cooldown
has expired.

NO agent is permanently skipped. Every agent is recoverable via
cooldown expiry. The user's enshrinement: "if ALL agents are
currently on exponential backoff, JUST WAIT UNTIL IT IS AVAILABLE,
THEN RETRY with the agent that comes off cooldown."

The pipeline may exit ONLY via the BUDGET-EXHAUSTED path — when an
agent's budget is exhausted AND no other agent is available. This
is the only path to `_enter_phase_failed` (`controller.py:641-643`).

## Smart-verdict gate

Every non-absolute `IdleWatchdog` fire candidate is routed through
`_gate_fire` (`ralph/agents/idle_watchdog/idle_watchdog.py:471-499`),
which consults the pure `classify_stuck` function (in
`ralph/agents/idle_watchdog/_stuck_classifier.py`). The classifier
returns one of six `StuckKind` values; the gate only allows FIRE
when the classifier returns `STUCK`.

The ONLY reason that bypasses the gate is
`SESSION_CEILING_EXCEEDED` (`idle_watchdog.py:494-495`) — the
operator-set session wall-clock cap, which is not a stuck-detection
signal.

The gate's contract: a non-absolute fire is deferred to
`CONTINUE` with `last_fire_reason=DEFERRED_BY_STUCK_CLASSIFIER`
when the classifier returns any kind other than `STUCK`. The
run loop's existing `_check_fire` reconsiders on the next evaluate
call. A productive session that does not look productive is not
killed.

## Why the gate uses a noop classify_quiet

The gate's StuckClassifier (`_classify_stuck_now`,
`idle_watchdog.py:411-471`) does NOT consult the live
`classify_quiet` callable because doing so would always return
`LOADING` during `WAITING_ON_CHILD` and deadlock the gate (the
watchdog entered `WAITING_ON_CHILD` BECAUSE `classify_quiet`
returned `WAITING_ON_CHILD`; consulting the same callable from
the gate would always defer the ceiling fire).

The `subagent_liveness` side-channel (the real process-monitor
live signal) is the canonical signal for "live child"; it is
consulted BEFORE the `classify_quiet` branches in the classifier
(`_stuck_classifier.py:227-229`). The classifier checks the
side-channel via `_subagent_liveness_fresh` which respects
`can_defer=True` — the gate defers only for real liveness signals
from a process monitor that has confirmed at least one live
subagent, not for the corroborator's stale-child signals
(`OS_DESCENDANT_ONLY_STALE_PROGRESS`,
`CPU_IDLE_WHILE_ALIVE`, `LOG_STALE_WHILE_ALIVE`) which set
`can_defer=False`.

## Two watchdog owners

The in-stream watchdog (`IdleWatchdog`) and the post-exit watchdog
(`PostExitWatchdog`) are the ONLY two canonical owners of
`WatchdogFireReason` construction. The
`ralph.testing.audit_watchdog_drift` AST audit enforces this
invariant: any production file outside the two canonical owners
that contains a `WatchdogFireReason(...)` call is flagged as a
`fire_reason_outside_canonical_owner` violation. Bare attribute
access (e.g. `if reason == WatchdogFireReason.X:`) is a reference,
not construction, and is allowed everywhere.

The watchdog is also the sole owner of `class IdleWatchdog` and
`class PostExitWatchdog` definitions. The audit flags any other
production file with a top-level class of either name as a
`duplicate_idle_watchdog` or `duplicate_post_exit_watchdog`
violation.

The `IdleWatchdogKilledError` typed exception module
(`ralph/agents/idle_watchdog_kill.py`) is intentionally KEPT at
the top level — it is consumed by the failure classifier and the
two invoke readers, and moving it would break the
`tests/test_property_matrix_consistency.py:159` pin.

## Drift prevention

The `ralph.testing.audit_watchdog_drift` AST audit is wired into
`make verify` (step 9) and forbids:

1. `old_watchdog.py` at the ralph-workflow root
   (`legacy_root_watchdog` violation).
2. Duplicate `IdleWatchdog` class definitions outside
   `ralph/agents/idle_watchdog/idle_watchdog.py`
   (`duplicate_idle_watchdog` violation).
3. Duplicate `PostExitWatchdog` class definitions outside
   `ralph/agents/idle_watchdog/_post_exit_watchdog.py`
   (`duplicate_post_exit_watchdog` violation).
4. `WatchdogFireReason` construction outside the two canonical
   owners (`fire_reason_outside_canonical_owner` violation).

The audit is a pure AST walker — no `time.sleep`, no `subprocess`,
no real I/O. The test `tests/test_audit_watchdog_drift.py` locks
the audit's forbidden-construct detection against synthetic bad
trees under `tmp_path`.

## Test contracts

The black-box tests that pin the watchdog invariants:

- `tests/agents/idle_watchdog/test_smart_verdict_dumb_kills.py` —
  the two canonical dumb-kill incidents.
- `tests/agents/idle_watchdog/test_dumb_kill_scenarios.py` — the
  extended dumb-kill scenarios (CURRENT_PROMPT reading with
  subagent_progress, OS_DESCENDANT_ONLY child, mcp_tool_call +
  live subagent, repeated-evaluate-with-progress, recovery-controller
  -never-advances-to-failed).
- `tests/agents/idle_watchdog/test_watchdog_recovery_contract.py` —
  the four-invariant contract test (no `sys.exit`, `teardown_subtree`
  gating, canonical owner, cooldown ownership).
- `tests/agents/idle_watchdog/test_stuck_classifier.py` — the pure
  `classify_stuck` function's six-kind priority order.
- `tests/recovery/test_two_main_retry_rules.py` — the two main
  rules (same-agent retry vs exponential backoff to next agent)
  and the never-exit invariant.
- `tests/recovery/test_controller_all_agents_unavailable_never_exits.py`
  — the all-agents-unavailable wait branch and `wrap=True`
  re-arming.
- `tests/test_audit_watchdog_drift.py` — the drift audit's
  forbidden-construct detection.

## See also

- `docs/agents/timeout-policy.md` for the timeout tunables.
- `CHANGELOG.md` for the consolidation entry under
  `## [Unreleased]` / `### Changed`.
- `ralph/recovery/controller.py` for the two-rule implementation.
- `ralph/agents/idle_watchdog/idle_watchdog.py` for the smart-verdict
  gate.
