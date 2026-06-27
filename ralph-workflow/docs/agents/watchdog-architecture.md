# Watchdog Architecture

The watchdog subsystem is the single centralized source of truth for
in-stream and post-exit fire decisions. This document is the
architectural reference; the AST contract test in
`tests/agents/idle_watchdog/test_watchdog_recovery_contract.py` and
the drift audit `ralph.testing.audit_watchdog_drift` are the
enforcement.

> **Per-AC traceability map** — for a one-section-per-R1-R8 mapping
> between each acceptance criterion (verbatim from
> `.agent/CURRENT_PROMPT.md`), the implementing module(s), and the
> dedicated pin test(s), see
> [`watchdog-spec.md`](watchdog-spec.md). That document is the
> canonical source for "which file owns which AC" and is kept in
> sync with the codebase via the consolidated pin test
> `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::test_r8`.
> The R5 section of that document names the explicit three-field
> public contract (`last_subagent_progress_description` /
> `last_subagent_progress_at` / `current_subagent_tool_call`) and
> the per-transport parametrize at
> `tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py`.

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
consolidation removes the dead legacy watchdog module that previously
sat at the ralph-workflow root and moves `post_exit_watchdog.py` and
`post_exit_verdict.py` into the subpackage. The drift audit enforces
this single-owner invariant.

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

**Rule 2 (exponential backoff to next agent) — child-alive vs
child-dead differentiation**: the recovery classifier's
`is_unavailable` predicate now treats `NO_PROGRESS_QUIET` as
Rule 1 (same-agent retry) ONLY when the watchdog's
`IdleWatchdogKilledError.child_alive=True` (defense-in-depth —
normally dead code because the gate refinement in
`IdleWatchdog._is_no_progress_quiet` prevents NO_PROGRESS_QUIET
from firing at all when alive_by is set). The conservative
policy: `child_alive=None` (legacy default — no signal at all)
and `child_alive=False` (truly dead child) both route to
`is_unavailable=True` with `unavailability_reason=STALE_CHILD_QUIET`
(Rule 2: exponential backoff). The 2-element
`_WATCHDOG_UNAVAILABILITY_REASONS` frozenset constant in
`ralph/recovery/failure_classifier.py` is the canonical set
`{"no_output_at_start", "children_persist_too_long"}`; the
`no_progress_quiet` reason is added by the conditional
`child_alive in (False, None)` branch (both map to Rule 2). The
constant is pinned at import time via `if/raise RuntimeError`
(immune to `python -O`).

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

The never-exit invariant is now also enforced at import time by
`_assert_never_exit_invariant` in `ralph/recovery/controller.py`
(uses `if/raise RuntimeError`, immune to `python -O`). The check
walks the AST of `RecoveryController._handle_retry_progression`
and asserts the all-agents-unavailable branch contains a
`Return` statement at a LOWER body index than the
`_enter_phase_failed` call. A future PR that introduces a third
state that exits the pipeline when all agents are on cooldown
will fail at import time with a `RuntimeError` naming both
invariants and pointing to
`tests/recovery/test_two_state_invariant.py` for the test-level
pin.

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

The gate's noop-classify_quiet rationale remains valid for the
WAITING_ON_CHILD chicken-and-egg case, but the watchdog's
`_is_no_progress_quiet` pre-gate check now ALSO defers the fire
when the corroborator reports any `alive_by` signal — so a live
child with stale-progress evidence does not trigger a dumb-kill.
The cumulative `CHILDREN_PERSIST_TOO_LONG` ceiling (default 600s)
remains the upper bound for long stalls with a live child. This
is the "know WHY something is stuck" complement to the
typed-evidence path through the failure classifier. When the
corroborator returns no `alive_by` signal at all
(`corroboration.alive_by is None`), the fire path is unchanged —
the watchdog cannot confirm liveness and fires at the 120s
ceiling.

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

## Edge Case Coverage Matrix

Every `WatchdogFireReason` is gated on a `TimeoutPolicy` field,
owned by exactly one watchdog file, and pinned by at least one
black-box test.  The matrix below is the contract for the
fire-condition family: a future PR that adds a new fire reason
MUST extend the matrix (the `idle_watchdog._EXPECTED_FIRE_REASONS`
import-time lock enforces the enum-side of the contract; the
matrix here enforces the test-side).

| Fire reason | TimeoutPolicy gating | Verdict owner | Test path | Audit verdict (this PR) |
| --- | --- | --- | --- | --- |
| `SESSION_CEILING_EXCEEDED` | `max_session_seconds` | `IdleWatchdog.evaluate` | `tests/agents/idle_watchdog/test_activity_aware.py`, `tests/test_live_tool_unavailable_recovery.py` | preserved |
| `CHILDREN_PERSIST_TOO_LONG` | `max_waiting_on_child_seconds` | `IdleWatchdog._handle_waiting_branch` | `tests/agents/idle_watchdog/test_os_descendant_only_escalation.py` | preserved |
| `NO_OUTPUT_DEADLINE` | `idle_timeout_seconds` | `IdleWatchdog._handle_active_branch` | `tests/agents/idle_watchdog/test_smart_verdict_dumb_kills.py` | audited |
| `NO_OUTPUT_AT_START` | `no_output_at_start_seconds` | `IdleWatchdog._evaluate_no_output_at_start` | `tests/agents/idle_watchdog/test_no_output_at_start.py` | **extended** (deferral test pinned) |
| `NO_PROGRESS_QUIET` | `no_progress_quiet_seconds`, `no_progress_quiet_minimum_invocation_seconds` | `IdleWatchdog._evaluate_no_progress_quiet` | `tests/agents/idle_watchdog/test_no_progress_quiet_watchdog.py` | preserved |
| `STALLED_AFTER_TOOL_RESULT` | `post_tool_result_progression_seconds` | `IdleWatchdog._post_tool_result_stalled` | `tests/test_live_post_tool_result_wedge.py` | preserved |
| `REPEATED_ERROR_LOOP` | `repeated_error_consecutive_threshold`, `repeated_error_window_count`, `repeated_error_window_seconds` | `IdleWatchdog.evaluate` (consults `RepetitionTracker.tripped`) | `tests/test_repetition_tracker.py` | preserved |
| `REPEATED_IDENTICAL_TOOL_CALL` | same as `REPEATED_ERROR_LOOP` | `IdleWatchdog.evaluate` (consults `RepetitionTracker.tripped_tool_dimension`) | `tests/test_repetition_tracker.py`, `tests/agents/idle_watchdog/test_watchdog_recovery_contract.py` | **extended** (new in this PR) |
| `PROCESS_EXIT_HANG` | `process_exit_wait_seconds` | `PostExitWatchdog` | `tests/agents/test_post_exit_watchdog.py` | preserved |
| `DESCENDANT_HANG` | `descendant_wait_timeout_seconds` | `PostExitWatchdog` | `tests/agents/test_post_exit_watchdog.py` | preserved |
| `DEFERRED_BY_STUCK_CLASSIFIER` | n/a (diagnostic label) | `IdleWatchdog._gate_fire` | `tests/agents/idle_watchdog/test_stuck_classifier.py`, `tests/agents/idle_watchdog/test_watchdog_recovery_contract.py` | preserved |

The five precedence-listed families in
`ralph/agents/idle_watchdog/timeout_policy.py:46-64` are each
mapped to their existing test paths above.

### Per-fire-reason breadcrumbs pattern

Each fire reason emitted by `IdleWatchdog` MUST be accompanied by
a `WaitingStatusEvent` breadcrumbs line that names the channel
summary at the moment of fire.  The `last_fire_reason` property
on `IdleWatchdog` is the single source of truth for the fire
reason and is set as the last fire decision (after the
smart-verdict gate).  The diagnostic-only
`DEFERRED_BY_STUCK_CLASSIFIER` label and the new
`REPEATED_IDENTICAL_TOOL_CALL` label are surfaced through the
same property so a post-mortem reader sees the WHY of a would-be
fire alongside the canonical FIRE reason.

### Resume-after-kill contract table

The watchdog-kill → next-attempt flow is pinned end-to-end.  Each
row maps a fire reason to the typed exception's
`session_resume_safe` flag, the recovery action the recovery
controller computes, the next-attempt session id, and the
`AgentRetryIntent` the pipeline runner builds.

| Reason | `session_resume_safe` | `recovery_action` | next-attempt session_id |
| --- | --- | --- | --- |
| `NO_OUTPUT_AT_START` | `True` | `resume` | prior |
| `NO_OUTPUT_DEADLINE` | `True` | `resume` | prior |
| `NO_PROGRESS_QUIET` | depends on `child_alive` (see `_WATCHDOG_UNAVAILABILITY_REASONS`) | `resume` (Rule 1) / unavailable (Rule 2) | prior or fresh |
| `STALLED_AFTER_TOOL_RESULT` | `True` | `resume` | prior |
| `REPEATED_ERROR_LOOP` | `True` | `resume` | prior |
| `REPEATED_IDENTICAL_TOOL_CALL` | `True` | `resume` | prior |
| `PROCESS_EXIT_HANG` | `False` | `fresh` | None |
| `DESCENDANT_HANG` | `False` | `fresh` | None |
| `SESSION_CEILING_EXCEEDED` | `False` | `fresh` | None |
| `CHILDREN_PERSIST_TOO_LONG` | `False` | `fresh` | None |

The contract test in
`tests/agents/idle_watchdog/test_resume_after_kill_contract.py`
pins each leg end-to-end with FakeClock + the existing
`recovery_action_for_failure_reason` /
`resolve_resume_session_id` /
`agent_retry_intent_for_failure` helpers.

### Per-parser visibility table

The `ParserTemplateBase.emit_subagent_activity` hook (and the
parallel standalone implementation on `ClaudeInteractiveParser`)
feeds `invoke_subagent_sink` from the activity stream.  The
matrix below maps each parser to the `AgentOutputLine.type` set
that flows to the subagent sink via this PR's wiring.

| Parser | Emittable types | Sink call format |
| --- | --- | --- |
| `ClaudeParser` | `tool_use`, `tool_result`, `text`, `thinking` (filtered via `_EMITTABLE_TYPES`) | `tool_use:<name>` / `tool_result:<name>` / `text:<first-80>` / `thinking:<first-80>` |
| `OpenCodeParser` | same | same |
| `CodexParser` | same | same |
| `GeminiParser` | same | same |
| `PiParser` | same | same |
| `AgyParser` | same | same |
| `GenericParser` | same | same |
| `ClaudeInteractiveParser` | same | same (standalone impl; not a template subclass) |

The contract test in
`tests/agents/test_subagent_activity_emission.py` pins each
parser's hook signature and the sanitized summary format.
The wiring test in
`tests/agents/test_stream_parsed_agent_activity_invokes_parser_emit_subagent.py`
pins the activity-stream integration.

### `IdleWatchdogKilledError.child_alive` three-valued semantics

The `child_alive` field on `IdleWatchdogKilledError` carries the
corroborator's `alive_by` signal at the moment of fire.  The
field is three-valued so the failure classifier can route
`NO_PROGRESS_QUIET` to the correct recovery rule without
ambiguity:

- `True`  -- the corroborator confirmed a live child
  (`AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS`,
  `CPU_IDLE_WHILE_ALIVE`, `LOG_STALE_WHILE_ALIVE`,
  `FRESH_HEARTBEAT_ONLY`, or `STALE_LABEL_ONLY`).  Normally dead
  code: the gate refinement in
  `IdleWatchdog._is_no_progress_quiet` defers the
  `NO_PROGRESS_QUIET` fire when the corroborator reports any
  `alive_by` signal.
- `False` -- the corroborator returned `alive_by=None` (no live
  signal — i.e. the child is truly dead or missing).  The
  conservative policy routes this to `is_unavailable=True`
  with `unavailability_reason=STALE_CHILD_QUIET` (Rule 2:
  exponential backoff to the next agent).
- `None`  -- the construction site did not set the field
  (legacy default).  The conservative policy preserves the
  original `STALE_CHILD_QUIET` (Rule 2) behavior for
  backward-compat with the existing construction sites that do
  not set the field.

## Drift prevention

The `ralph.testing.audit_watchdog_drift` AST audit is wired into
`make verify` (step 10 of 12) and forbids:

1. The legacy root watchdog sentinel (the 1389-line module that
   previously sat at the ralph-workflow root) re-introduced at the
   ralph-workflow root (`legacy_root_watchdog` violation).  The
   filename is derived at audit-import time from two private string
   fragments so the literal forbidden token never appears as a
   contiguous substring in source.
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

## Subagent identity contract (R1)

The watchdog defers on the **filtered subagent count**, never on the
broader descendant count. A process is a real subagent iff:

1. It is a live descendant of the supervised agent PID, AND
2. It is REGISTERED in the shared `SubagentPidRegistry` by the
   transport's authoritative `SubagentPidSource`.

The canonical owner of the identity contract is
`ralph/agents/idle_watchdog/_subagent_identity.py` (single source of
truth for `SubagentIdentity` and `SubagentPidRegistry`). The audit
`ralph.testing.audit_watchdog_drift.subagent_counting_outside_owner`
enforces the single-owner invariant so a future PR cannot introduce a
parallel identity type without updating this owner.

The filtered count is exposed via
`ProcessMonitor.spawned_subagent_count()` (preferred) and
`live_subagent_count()` (legacy alias). Both return the count of
processes classified as `ProcessRole.SPAWNED_SUBAGENT` in the
monitor's most recent scan. The readers (`_process_reader._corroborate`
and `_pty_line_reader._corroborate`) MUST use the filtered seam for
`scoped_child_active` so a shell helper like `npm test`, `cargo build`,
or `find /` (which IS a descendant but is NOT registered as a real
subagent) does not block the watchdog's hard ceilings.

The broader `handle.descendant_snapshot()` count MUST NEVER be used for
the deferral decision. The audit
`ralph.testing.audit_activity_aware_watchdog.subagent_counting_seam`
flags any reader that uses `descendant_snapshot` without also using
the filtered seam as a regression.

## Hard ceilings

The watchdog's hard ceilings fire in this precedence order
(highest first):

1. **`SESSION_CEILING_EXCEEDED`** — `MAX_SESSION_SECONDS=3300.0` (default
   from `ralph/timeout_defaults.py`). Operator-set wall-clock cap on
   the entire session. Activity cannot reset this ceiling. The only
   reason that bypasses the smart-verdict gate (see
   `ralph.agents.idle_watchdog._gate.gate_fire`).
2. **`CHILDREN_PERSIST_TOO_LONG`** — `MAX_WAITING_ON_CHILD_SECONDS=1800.0`
   cumulative ceiling across the session. Never decays; fires even
   when non-subagent helper processes are present in the descendant
   tree (R3 of the Trustworthy Idle Watchdog spec — the 2365s
   indefinite deferral bug).
3. **`NO_OUTPUT_DEADLINE`** (with drain window) — idle deadline since
   last output. Defers when a non-stdout evidence channel
   (MCP tool call, subagent work, workspace file change) is fresher
   than `activity_evidence_ttl_seconds`.
4. **`PROCESS_EXIT_HANG`** — subprocess closed stdout but did not exit
   within budget (post-exit only, owned by `PostExitWatchdog`).
5. **`DESCENDANT_HANG`** — descendant-wait deadline elapsed with
   persistent `WAITING_ON_CHILD` (post-exit only).

The session ceiling is the hard backstop that prevents the
2365s indefinite deferral observed in the wild (R3). When a non-
subagent helper process (a shell helper like `npm test`) is alive
in the descendant tree, the FILTERED subagent count is 0 and the
session ceiling fires regardless.

## Resume vs restart

Watchdog-driven kills ALWAYS resume the existing session via
`resumable_session_id`. Fresh sessions are reserved for deliberate
phase transitions only. The two paths are function-separate:

- **Resume path** — `resolve_resume_session_id(...)` returns the prior
  session id when `has_prior_session=True` and `recovery_action ==
  "resume"`. Threaded end-to-end via
  `IdleWatchdogKilledError.resumable_session_id` →
  `_convert_idle_stream_timeout_to_agent_error` →
  `AgentInactivityTimeoutError.resumable_session_id` →
  `FailureClassifier.resumable_kill` carve-out →
  `state.last_agent_session_id` →
  `recovery_action_for_failure_reason(...)` →
  `resume_agent_retry_intent(...)`.
- **Fresh path** — `fresh_session_options(opts)` returns
  `InvokeOptions(session_id=None)` for deliberate phase transitions.
  The `prior_session_id` parameter is accepted for forward-
  compatibility but is NEVER written back.

Lock-in regression tests:

- `tests/agents/idle_watchdog/test_resume_after_kill_contract.py`
- `tests/agents/idle_watchdog/test_resume_after_kill_watchdog_boundary.py`
- `tests/agents/idle_watchdog/test_resume_contract_invariant.py`
- `tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py`
- `tests/recovery/test_opencode_resumable_exit_classification.py`

See `ralph/agents/invoke/_session_resume.py` for the end-to-end
threading contract (7 numbered evidence points).

## Trustworthy Idle Watchdog spec coverage (wt-021)

The Trustworthy Idle Watchdog product spec
(`.agent/CURRENT_PROMPT.md`) defines eight acceptance criteria. Each
criterion is pinned by a dedicated black-box test file and the
consolidated spec test
(`tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py`)
asserts one concrete invariant per criterion against its dedicated
pin. The matrix below is the canonical record; the spec test pins
every row.

1. **R1 — Child-process monitors count only real subagents.** A
   process is a real subagent iff it is a live descendant of the
   supervised agent PID AND it is REGISTERED in the shared
   `SubagentPidRegistry`. The filtered count is exposed via
   `ProcessMonitor.spawned_subagent_count()` (preferred) and
   `live_subagent_count()` (legacy alias). Pin:
   `tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py`.
   Invariant: a monitor that only sees helper PIDs returns 0 from
   BOTH seam names; the alias is faithful (both names return the
   same filtered value). [wt-021 R1 / Trustworthy Idle Watchdog R1]

2. **R2 — No false positives.** The watchdog does NOT kill while
   activity is recent or a real subagent is working. The
   `classify_stuck` function maps `WAITING_ON_CHILD` → `LOADING`,
   `RESUMABLE_CONTINUE` → `TRANSITIONING`, and `is_waiting_state=True`
   → `DUPLICATE_KILL`; the smart-verdict gate returns `CONTINUE`
   for every non-`STUCK` kind. Pins:
   `tests/agents/idle_watchdog/test_silent_after_tool_call_wedge.py`
   (single MCP tool-call + quiet with fresh corroborator does NOT
   fire) + `tests/agents/idle_watchdog/test_stuck_classifier.py`
   (verdict priority). [wt-021 R2 / Trustworthy Idle Watchdog R2]

3. **R3 — No false negatives.** Every genuine hang fires within a
   bounded ceiling, even when a non-subagent helper process looks
   like a lingering child. The hard ceilings are checked against
   the FILTERED subagent count; a helpers-only monitor returns 0
   and the ceiling fires. Pin:
   `tests/agents/idle_watchdog/test_hard_ceiling_with_helpers_alive.py`
   (session / cumulative / idle ceilings all fire with helpers
   alive). [wt-021 R3 / Trustworthy Idle Watchdog R3]

4. **R4 — Watchdog-driven kills resume the existing session.** The
   resume path (`AgentInactivityTimeoutError` /
   `OpenCodeResumableExitError` with a prior session) returns
   `recovery_action='resume'` via
   `recovery_action_for_failure_reason`; the fresh path is
   function-separate via `fresh_session_options`. Pin:
   `tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py`
   (8 evidence points end-to-end) +
   `tests/recovery/test_opencode_resumable_exit_classification.py`.
   [wt-021 R4 / Trustworthy Idle Watchdog R4]

5. **R5 — Real-time subagent visibility for all supported agents.**
   `record_subagent_work(description=line)` populates
   `last_subagent_progress_description` so every supported
   `AgentTransport`'s real extracted progress surfaces through the
   watchdog. Pin:
   `tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py`
   (8 transports × 5 signal shapes; OpenCode additionally routes
   per-child `RegistryBackedSubagentOutputCapture` lines).
   [wt-021 R5 / Trustworthy Idle Watchdog R5]

6. **R6 — Quiet, meaningful output.** `_gate_fire` emissions are
   throttled by a COMBINED coarse per-`fire_reason` map
   (`_last_any_deferred_log_at` keyed on `fire_reason.value`
   alone) PLUS a per-tuple map (`_last_deferred_log_at` keyed on
   `(fire_reason, deferred_kind)`). The coarse throttle caps
   emissions to one DEBUG record per `watchdog_log_throttle_seconds`
   per `fire_reason` REGARDLESS of how the deferred_kind cycles.
   Pin: `tests/agents/idle_watchdog/test_log_spam_throttle.py` (per-
   tuple + coarse single-key + refresh-window cases).
   [wt-021 R6 / Trustworthy Idle Watchdog R6]

7. **R7 — Ambiguous rc=0 exits classified deterministically.**
   `OpenCodeResumableExitError` classifies as
   `FailureCategory.AGENT` BEFORE the broader
   `AgentInvocationError` branch; the exception NEVER falls through
   to `FailureCategory.AMBIGUOUS`. Pin:
   `tests/recovery/test_opencode_resumable_exit_classification.py`
   (every instance, including `session_id=None`, classifies as
   `AGENT`). [wt-021 R7 / Trustworthy Idle Watchdog R7]

8. **R8 — Clean, black-box-testable architecture.** Every watchdog
   test file uses `FakeClock` (`ralph/agents/timeout_clock.py`) + a
   tiny `@dataclass` satisfying the `ProcessMonitor` Protocol. No
   real sleep, no real subprocess, no real filesystem. Enforced
   structurally by the AST-level audit
   `ralph/testing/audit_test_policy.py` (wired into `make verify`).
   Pin: `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py`
   (8 ordinary test methods, one per R1-R8, asserting one concrete
   invariant each). [wt-021 R8 / Trustworthy Idle Watchdog R8]

Consolidated AC summary test:

- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py` —
  single black-box module with a `TestTrustworthyIdleWatchdogSpec`
  class containing 8 ordinary test methods (`test_r1` through
  `test_r8`, no `@pytest.mark.parametrize`). Each method asserts one
  concrete invariant and references its dedicated pin test in the
  docstring. Target wall-clock: <2 seconds for the whole file.
