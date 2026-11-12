# Trustworthy Idle Watchdog — Spec Traceability Map

This document is the **traceability map** between each acceptance
criterion (R1–R8) of the Trustworthy Idle Watchdog spec and the
implementing module + pin test that enforces it. It is intentionally
tutorial-free: no code, no implementation walkthrough — only the
per-AC linkage. Drift between this document and the codebase is caught
by the consolidated pin test
`tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r8`
(whose `RALPH_PIN_TEST_PATHS` references every dedicated pin test file
listed below); if a file is renamed or moved, `test_r8` fails and the
doc must be updated to match.

For architectural context, see the watchdog subsystem
internals (`ralph/watchdogs/`) and the source-of-truth modules
referenced in this page. For the underpinning cross-transport
subagent visibility contract, see the R5 section below and the
per-transport parametrize at
`tests/agents/test_idle_watchdog.py`.

---

## R1 — Child-process monitors count only real subagents

> Child-process / per-agent monitors count only real subagents; host
> and internal helper spawns are provably excluded.

### Implementing modules

- `ralph/agents/idle_watchdog/_subagent_identity.py:32` —
  `class SubagentIdentity`; the per-transport source tag is the seam
  the filter consults.
- `ralph/agents/idle_watchdog/_subagent_identity.py:62` —
  `class SubagentPidRegistry`; FIFO-capped at
  `MAX_SUBAGENT_REGISTRY_ENTRIES` (1024, defined at
  `ralph/agents/idle_watchdog/_subagent_identity.py:25`), shared across
  transports, filtered by `source`.
- `ralph/process/monitor/_process_monitor.py:10` —
  `class ProcessRole(StrEnum)`; the role enum every per-agent
  monitor tags its observations with.
- `ralph/process/monitor/_process_monitor.py:30` —
  `class ProcessMonitor(Protocol)`; canonical seam every per-agent
  monitor implements.
- `ralph/process/monitor/_process_monitor.py:31` —
  `ProcessMonitor.live_subagent_count`; canonical filtered count.
- `ralph/process/monitor/_process_monitor.py:33` —
  `ProcessMonitor.spawned_subagent_count`; canonical filtered count
  (preferred alias; returns the same filtered value as
  `live_subagent_count`).
- `ralph/process/monitor/_default_monitor.py:99` —
  `DefaultProcessMonitor.live_subagent_count`; production
  implementation that excludes helpers.
- `ralph/process/monitor/_default_monitor.py:102` —
  `DefaultProcessMonitor.spawned_subagent_count`; production
  filtered count.

### Pin tests

- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
  — production SubagentPidRegistry wiring end-to-end pin; exercises
  the `AgentRegistry.build_subagent_pid_registry(transport)` →
  `BaseExecutionStrategy(subagent_pid_source=...)` →
  `classify_quiet` injection path AND the parser-side registry
  storage for every `AgentTransport` member (10 transports:
  OpenCode, Claude, Claude-interactive, Codex, Nanocoder, Generic,
  Agy, Pi, Cursor, Kimi). Locks the production wiring and catches any future
  refactor that bypasses the registry seam.
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r1`

---

## R2 — No false positives (don't kill healthy or waiting work)

> No watchdog kill occurs while activity is recent or a real subagent
> is working.

### Implementing modules

- `ralph/agents/idle_watchdog/_gate.py:15` — `def classify_stuck_now`;
  the StuckClassifier input combiner consumed by the smart-verdict
  gate.
- `ralph/agents/idle_watchdog/_gate.py:39` — `def gate_fire`; the
  gate that returns `WatchdogVerdict.CONTINUE` for every non-STUCK
  classification.
- `ralph/agents/idle_watchdog/_stuck_classifier.py:110` —
  `def classify_stuck`; pure-function mapping from
  `AgentExecutionState` + channel evidence + corroboration to
  `StuckKind`.
- `ralph/agents/idle_watchdog/_active_branch.py:158` —
  `def evaluate_inner`; the central verdict path that consults every
  escape hatch.
- `ralph/agents/idle_watchdog/_fire_evaluators.py:90` —
  `def evaluate_no_progress_quiet`; the strictly-stuck and
  no-progress-quiet ceiling enforcement.
- `ralph/agents/idle_watchdog/_fire_evaluators.py:221` —
  `def evaluate_strictly_stuck`; the orthogonal strictly-stuck
  ceiling for stuck-but-alive subagents.
- `ralph/agents/idle_watchdog/_fire_evaluators.py:297` —
  `def evaluate_no_output_at_start`; the premature first-output
  guard with multiple deferral gates (subagent activity, recent
  tool result, first-party channel freshness).

### Pin tests

- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r2`

---

## R3 — No false negatives (always catch real hangs)

> Every genuine hang fires within a bounded ceiling, even when a
> non-subagent process looks like a lingering child.

### Implementing modules

- `ralph/agents/idle_watchdog/_active_branch.py:158` —
  `def evaluate_inner`; the session-ceiling + repeated-error
  breaker + strictly-stuck ceiling + no-progress ceiling
  pre-checks.
- `ralph/agents/idle_watchdog/_active_branch.py:190-193` —
  inline `SESSION_CEILING_EXCEEDED` check; the absolute
  `max_session_seconds` bypass triggered when
  `session_elapsed >= self._config.max_session_seconds`.
- `ralph/agents/idle_watchdog/_active_branch.py:189-198` —
  session-ceiling early-exit block (the hard wall bypassing the
  smart-verdict gate for `SESSION_CEILING_EXCEEDED` only).
- `ralph/agents/idle_watchdog/_gate.py:39` — `def gate_fire`;
  bypass path for `WatchdogFireReason.SESSION_CEILING_EXCEEDED`
  (line 47) — the only reason that bypasses the gate.
- `ralph/agents/idle_watchdog/_waiting_branch.py:20` —
  `def effective_waiting_ceiling`; the bounded waiting ceiling
  computed from corroboration (`max_waiting_on_child_seconds=1800`,
  `stuck_job_sub_ceiling_seconds=600`).
- `ralph/agents/idle_watchdog/_waiting_branch.py:221-262` —
  cumulative waiting ceiling block (hard-enforced per R3);
  fires `CHILDREN_PERSIST_TOO_LONG` UNCONDITIONALLY when
  `candidate_total >= effective_ceiling`, without consulting
  `self._gate_fire`. The sub-ceiling block at lines 161-218
  consults `_gate_fire` because that branch is the smart
  sub-ceiling bounded by `stuck_job_sub_ceiling_seconds`
  (default 600s); the cumulative ceiling is the absolute
  backstop. Per PROMPT R3: "There must be a hard, bounded ceiling
  after which a true hang fires regardless of deferral reasons."
- `ralph/agents/idle_watchdog/_fire_evaluators.py:90` —
  `def evaluate_no_progress_quiet`; the dumb-kill ceiling +
  heartbeat-only fire path.
- `ralph/agents/idle_watchdog/_fire_evaluators.py:221` —
  `def evaluate_strictly_stuck`; the orthogonal strictly-stuck
  ceiling for stuck-but-alive subagents.
- `ralph/agents/idle_watchdog/timeout_policy.py:58` —
  `TimeoutPolicy.max_session_seconds`; the configurable wall-clock
  cap consumed by the session-ceiling check above.

### Pin tests

- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
  — the R3 pin for the cumulative ceiling hard enforcement.
  Exercises two scenarios via `_classify_stuck_now`
  override: (1) `SILENT_SUBAGENT` (the indefinite-deferral
  scenario) and (2) `LOADING` (a productive liveness signal).
  Both cases MUST fire `CHILDREN_PERSIST_TOO_LONG` at the ceiling.
  Uses `FakeClock` + Protocol-typed `@dataclass` `ProcessMonitor`
  fake (NO real subprocess), in scope for the canonical R8 audit
  target.
- `tests/agents/test_idle_watchdog.py`
  — heartbeat-only ceiling pin for stuck jobs that emit heartbeats
  but no real work (`AliveBy.FRESH_HEARTBEAT_ONLY`). Locks the
  `no_progress_quiet_heartbeat_ceiling_seconds` (default 240s)
  branch: (1) fires `NO_PROGRESS_QUIET` when the corroborator
  reports `FRESH_HEARTBEAT_ONLY` AND `invocation_elapsed_seconds`
  >= the ceiling; (2) does NOT trip before its threshold;
  (3) fires BEFORE the dumb-kill ceiling when
  `heartbeat_ceiling < no_progress_quiet_seconds`; (4) `FRESH_PROGRESS`
  (real progress, not just heartbeat) continues to defer
  indefinitely (the R2 guarantee); (5) `None` disables the
  heartbeat-only ceiling. Locks the heartbeat-only ceiling
  enforcement.
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r3`

---

## R4 — Resume on watchdog kill, never restart

> Watchdog-driven kills resume the existing session; new sessions
> occur only on deliberate phase transitions.

### Implementing modules

- `ralph/agents/idle_watchdog/idle_watchdog.py:143` —
  `class IdleWatchdog`; the watchdog that publishes the kill reason
  the resume path reads.
- `ralph/agents/invoke/_process_reader.py:296` —
  `_RESUMABLE_FIRE_REASONS`; the fire-reason set that selects the
  resume path (`_is_resumable_fire_reason` at
  `ralph/agents/invoke/_process_reader.py:308`).
- `ralph/agents/idle_watchdog/_activity_methods.py:109` —
  `def diagnostic_snapshot`; carries `resumable_session_id: str | None`
  hardcoded to `None` (the watchdog itself never populates the
  field; it is a stable key reserved for the outer watchdog-kill
  readers that merge the snapshot into a larger post-mortem
  payload).
- `ralph/agents/invoke/_process_reader.py:855` —
  `merged_diag["resumable_session_id"] = captured_session_id`; the
  watchdog-kill path for subprocess transports populates this key
  on the OUTER `merged_diag` payload (NOT inside the inner
  `watchdog_snapshot` dict). The id is sourced from the visible-TUI
  `self._captured_session_id` populated from the agent session.
- `ralph/agents/invoke/_pty_line_reader.py:951` —
  `merged_diag["resumable_session_id"] = captured_session_id`; the
  watchdog-kill path for PTY transports populates this key on the
  OUTER `merged_diag` payload with the same semantics as the
  subprocess path.
- `ralph/agents/idle_watchdog_kill.py:9` —
  `class IdleWatchdogKilledError(Exception)`; the typed exception
  that carries the resumable session id through the
  failure-classifier pipeline. This is the CANONICAL carrier of
  the id for the failure-classifier pipeline; the `merged_diag`
  population above is the canonical carrier for log-only consumers
  that walk the post-mortem dict directly without inspecting the
  exception chain.
- `ralph/agents/invoke/_open_code_resumable_exit_error.py:6` —
  `class OpenCodeResumableExitError(AgentInvocationError)`; the
  typed rc=0 exit exception classified by R7 below.
- `ralph/agents/invoke/_session_resume.py:58` —
  `def recovery_action_for_failure_reason`; the function that maps
  a failure reason to `"resume"` vs `"fresh"`.

> **Contract note (resumable_session_id location):**
> `resumable_session_id` lives on THREE surfaces: (1) the outer
> `merged_diag` payload (set by `_process_reader.py:855` and
> `_pty_line_reader.py:951` at the moment of the watchdog kill), (2)
> the typed `IdleWatchdogKilledError.resumable_session_id` attribute
> (used by the failure classifier via `exc.__cause__`), and (3) the
> inner `IdleWatchdog.diagnostic_snapshot()` dict key
> `resumable_session_id` — hardcoded to `None`; the watchdog itself
> does NOT populate the field. Consumers MUST consult the outer
> `merged_diag` payload OR walk the exception chain
> (`failure.resumable_session_id`) — NOT the inner
> `diagnostic_snapshot()` key.

### Pin tests

- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r4`

---

## R5 — Real-time subagent visibility for all supported agents

> Real-time subagent activity is observable for all supported agents
> — current tool call, progress, and last activity surfaced as
> structured fields.

### EXPLICIT THREE-FIELD PUBLIC CONTRACT

The R5 surface exposes three structured fields on BOTH the
`IdleWatchdog.diagnostic_snapshot()` dict and the
`WaitingStatusEvent` dataclass. Every supported transport flows all
three fields through to operators in real time.

| Field                          | Surface (`diagnostic_snapshot`)  | Surface (`WaitingStatusEvent`)  | Type            | Semantics                                                         |
|--------------------------------|----------------------------------|---------------------------------|-----------------|-------------------------------------------------------------------|
| **PROGRESS**                   | `last_subagent_progress_description` | `subagent_activity`         | `str \| None`   | Free-form description text set by `record_subagent_work(description=...)`. |
| **LAST ACTIVITY**              | `last_subagent_progress_at`     | `last_subagent_progress_at`     | `float \| None` | Monotonic timestamp of the most recent subagent observation. |
| **CURRENT TOOL CALL**          | `current_subagent_tool_call`     | `current_subagent_tool_call`    | `str \| None`   | Parsed `verb:` prefix from `PROGRESS` when the description starts with a known tool-call verb; `None` otherwise. Parsed by `parse_tool_call_from_description` in `ralph/agents/idle_watchdog/_activity_methods.py`. |

The known tool-call verbs are: `tool_use`, `tool_result`, `mcp_tool`,
`subagent`, `bash`, `read`, `write`, `edit`, `glob`, `grep`,
`webfetch`, `websearch`. The canonical production format from the
NDJSON parser layer is `tool_use:<name>` (colon, no space) per
`ralph/agents/parsers/claude_interactive.py` line 51.

PER-TRANSPORT COVERAGE REQUIRED: every `AgentTransport` member
(OpenCode, Claude, Claude-interactive, Codex, Nanocoder, Generic, Agy,
Pi, Cursor, Kimi) flows all three R5 fields through both surfaces via the
parametrized per-transport pin test at
`tests/agents/test_idle_watchdog.py`.

### Implementing modules

- `ralph/agents/idle_watchdog/_activity_methods.py:36` —
  `def parse_tool_call_from_description`; the R5 CURRENT TOOL CALL
  parser (pure function).
- `ralph/agents/idle_watchdog/_activity_methods.py:109` —
  `def diagnostic_snapshot`; exposes all three R5 fields on the
  watchdog public surface.
- `ralph/agents/idle_watchdog/waiting_status_event.py:12` —
  `class WaitingStatusEvent`; the frozen dataclass carrying all
  three R5 fields on every emitted event.
- `ralph/agents/idle_watchdog/_active_branch.py:79` —
  `def emit`; the dispatcher that populates all three R5 fields on
  every emitted `WaitingStatusEvent`.
- `ralph/agents/idle_watchdog/_waiting_branch.py:384` —
  `def handle_waiting_branch`; the SUBAGENT_PROGRESS emit site that
  flows all three fields to operators.
- `ralph/agents/idle_watchdog/_activity_methods.py:205` —
  `def record_subagent_work`; the canonical producer of the R5
  surface (PROGRESS + LAST ACTIVITY).
- `ralph/agents/idle_watchdog/_activity_methods.py:54` —
  `def record_invocation_start`; the per-invocation reset that
  clears all three R5 fields to `None`.

### Pin tests

- `tests/agents/test_idle_watchdog.py`
  — per-transport parametrize over `list(AgentTransport)`;
  parametrized test methods assert all three R5 fields on BOTH
  surfaces (the watchdog `diagnostic_snapshot()` dict and the
  `WaitingStatusEvent` dataclass) for every supported transport.
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/process/monitor/test_dispatch_all_transports.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r5`
  — the consolidated surface test; exercises all three R5 fields on
  the watchdog public surface AND on emitted `WaitingStatusEvent`
  instances for a single-watcher scenario.

---

## R6 — Quiet, meaningful output (kill the spam)

> No duplicate/high-frequency log spam in any state; waiting status
> is a single clear, periodic, human-readable message.

### Implementing modules

- `ralph/agents/idle_watchdog/_gate.py:77` —
  `def maybe_log_deferred`; the per-tuple
  `(fire_reason, deferred_kind)` throttle keyed on
  `watchdog_log_throttle_seconds`.
- `ralph/agents/idle_watchdog/_gate.py:93` —
  `def maybe_log_any_deferred`; the coarse per-`fire_reason`
  throttle that caps emissions to one DEBUG record per throttle
  window regardless of `deferred_kind` cycles.
- `ralph/agents/idle_watchdog/_active_branch.py:332` —
  `def maybe_log_evidence_deferral`; the per-channel
  evidence-deferral throttle that mirrors the per-tuple map for
  the activity-aware path.
- `ralph/agents/idle_watchdog/_waiting_branch.py:384` —
  `def handle_waiting_branch`; the structured-status emit path
  (ENTERED, PROGRESS, SUSPECTED_FROZEN, HARD_STOP). Per-tick debug
  spam is not emitted.
- `ralph/agents/idle_watchdog/timeout_policy.py:51` —
  `class TimeoutPolicy`; carries `watchdog_log_throttle_seconds`
  (line 96), `watchdog_subagent_progress_interval_seconds` (line
  97), `waiting_status_interval_seconds` (line 64) — the cadence
  knobs.
- `ralph/agents/idle_watchdog/_activity_methods.py:54` —
  `def record_invocation_start`; the per-invocation reset that
  clears every throttle map so a fresh run starts with empty maps.

### Pin tests

- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
  — the R6 public-surface proof that drives the watchdog via
  `watchdog.evaluate(classify_quiet=...)` only (NO `setattr` on
  `_classify_stuck_now`, NO direct call to `_gate_fire`, NO read
  of `_last_*_log_at`). Captures `WaitingStatusEvent` instances via
  the `IdleWatchdog.__init__(listener=...)` constructor seam at
  `ralph/agents/idle_watchdog/idle_watchdog.py:201` (the main
  waiting-status listener, stored on `_listener` at line 209 and
  invoked by `_active_branch.emit` at
  `ralph/agents/idle_watchdog/_active_branch.py:118-120` for every
  emitted event regardless of payload) and counts both
  PROGRESS-kind emissions and loguru StringIO records filtered on
  `component='idle_watchdog'`. The 1000-call cycle MUST emit
  `<= 2` PROGRESS events (the R6 spam-invariant). Locks the R6
  throttle contract via public-surface observables only.

  **Note on the listener API (R6 pin test):** the watchdog exposes
  TWO distinct listener slots, NOT aliases of one another:
  (a) `IdleWatchdog.__init__(listener=...)` — the main
  waiting-status listener declared at
  `ralph/agents/idle_watchdog/idle_watchdog.py:201` and stored on
  `_listener` (set at line 209); `_active_branch.emit` invokes it
  for every `WaitingStatusEvent` it builds.
  (b) `register_default_subagent_activity_listener(listener)`
  declared at `ralph/agents/idle_watchdog/idle_watchdog.py:432` — a
  SEPARATE convenience listener stored on
  `_default_subagent_activity_listener` (set at line 436);
  `_active_branch.emit` invokes it ONLY when the built
  `WaitingStatusEvent.subagent_activity` field is non-`None`
  (see `_active_branch.py:124-127`). The two slots are independent
  — registering one does not replace the other — and they receive
  disjoint event subsets. The R6 public-surface pin test exercises
  (a) only; tests that exercise (b) are listed in the R5
  cross-transport visibility pins (R5 owns the subagent-activity
  surface).
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/test_idle_watchdog.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r6`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r6_heartbeat`

### Heartbeat UX

R6 accepts either a single updating line or a low-frequency
heartbeat; the watchdog emits the **low-frequency heartbeat**. While
the watchdog is in `WAITING_ON_CHILD` deferral, it emits a single
periodic loguru INFO record per `evaluate()` call whose cadence gate
passes, with a human-readable template naming:

  * **what is happening** — the agent is WAITING on a subagent,
  * **the live subagent count** from the FILTERED process monitor
    (R1 — the audit-enforced seam `spawned_subagent_count()`; the
    broader `descendant_snapshot()` count is NEVER used here),
  * **elapsed seconds** (rounded), and
  * **the hard ceiling seconds** (rounded).

The exact loguru INFO message template, emitted at
`ralph/agents/idle_watchdog/_waiting_branch.py:328-333`, is:

```
idle watchdog: agent waiting on subagent ({} alive) for {}s - hard ceiling at {}s
```

The cadence knob is
`TimeoutPolicy.waiting_status_interval_seconds`
(`ralph/agents/idle_watchdog/timeout_policy.py:64`), defaulting to
the constant `WAITING_STATUS_INTERVAL_SECONDS` (30s). The heartbeat
fires on every `evaluate()` call whose gate
`now - _last_waiting_status_at >= waiting_status_interval_seconds`
is satisfied, so the operator sees one updating line per cadence
window.

The WAITING entry log emitted at
`ralph/agents/idle_watchdog/_waiting_branch.py:90-94` is a
SEPARATE loguru INFO record from the heartbeat:

```
idle watchdog: entering WAITING_ON_CHILD deferral idle_elapsed={}s cumulative={}s
```

The entry log fires ONCE on the first `evaluate()` call when
`WAITING_ON_CHILD` is entered; the heartbeat fires periodically
afterwards at the cadence knob's interval. The two records are
distinguished by disjoint substrings: 'entering WAITING_ON_CHILD
deferral' (entry log) vs 'agent waiting on subagent' (heartbeat).
The consolidated `test_r6_heartbeat` post-filters captured INFO
records to the heartbeat substring before asserting cadence and
message fields, so the post-filter cleanly excludes the entry log.

Verify cited line numbers after touching the cited files.

---

## AC-7 — Status Bar `STALLED` is exclusively watchdog-sourced

> The watchdog is the single source of truth for the status bar's
> `STALLED` label: every stall-state transition is published as a
> `WaitingStatusEvent`, the subscriber forwards it to the Status Bar
> host, and the bar reads the watchdog-sourced attention only. The
> watchdog judges stalls from `TimeoutPolicy` thresholds and
> first-party evidence channels; the bar derives no stall state of
> its own.

### Single-source-of-truth invariant

The watchdog publishes the stall-state transitions on its existing
`WaitingStatusListener` channel
(`IdleWatchdog._emit` → reader `_on_waiting_event` →
`waiting_listener` → `waiting_dispatch.dispatch_waiting_event` →
`PipelineSubscriber.record_waiting_status`). The status bar renders
`STALLED` only from that watchdog-sourced state. No display-side
gap derivation exists: `StatusBarModel` exposes no
`last_activity_monotonic` field, the status-bar module defines no
`_STALL_THRESHOLD_SECONDS` constant, and
`ParallelDisplay._emit_activity_event` performs no producer write
(zero dead code).

### `WaitingStatusKind` members

`ralph/agents/idle_watchdog/waiting_status_kind.py` defines eight
kinds (`ENTERED`, `PROGRESS`, `SUSPECTED_FROZEN`, `EXITED`,
`HARD_STOP`, `SUBAGENT_PROGRESS`, `STALLED`, `STALL_RESUMED`); two of
them bracket the stall lifecycle:

| Kind | Fires when |
|------|------------|
| `STALLED = "stalled"` | The watchdog considers the run stalled. One event per transition INTO a stall. |
| `STALL_RESUMED = "stall_resumed"` | The watchdog considers the run not stalled. One event per transition OUT of a stall. |

The transition is gated by `IdleWatchdog._set_stall(active, ...)`
which compares against the runtime `_stall_active` flag and emits
exactly once per transition. Repeated ticks with the same `active`
value are no-ops (no per-tick spam).

### `STALLED` trigger sites

All six production sites are inside the watchdog's single
evaluation path; each flips the runtime flag and emits `STALLED`
via `_set_stall`:

- `ralph/agents/idle_watchdog/_waiting_branch.py:297` — alongside
  the `SUSPECTED_FROZEN` emission (the suspect-threshold cross).
- `ralph/agents/idle_watchdog/_waiting_branch.py:200` — alongside
  the `HARD_STOP` emission for the stuck-job sub-ceiling branch.
- `ralph/agents/idle_watchdog/_waiting_branch.py:247` — alongside
  the `HARD_STOP` emission for the cumulative ceiling branch.
- `ralph/agents/idle_watchdog/_gate.py:52` — at the `STUCK`
  classifier verdict (a `FIRE` for a non-absolute reason).
- `ralph/agents/idle_watchdog/_gate.py:60` — at the
  `SILENT_SUBAGENT` gate-deferral site (the gate allows the FIRE,
  so the stall signal must ride on the FIRE).
- `ralph/agents/idle_watchdog/_gate.py:48` — at the
  `SESSION_CEILING_EXCEEDED` bypass path. The bypass
  skips the StuckClassifier (the cap is not a stuck-detection
  signal) but still transitions the runtime stall flag before
  returning FIRE so the Status Bar's `STALLED` slot lights up
  identically to a STUCK classifier verdict or a SILENT_SUBAGENT
  fire.

`SESSION_CEILING_EXCEEDED` IS a `STALLED` trigger: the
operator-set cap is an absolute ceiling, but the watchdog is the
sole owner of the `STALLED` label and a session that hit the cap
is a stalled run from the operator's perspective (the cap fired
because the run was alive but un-killable by every other rule).
The bypass path at `ralph/agents/idle_watchdog/_gate.py:47-48`
transitions the runtime stall flag via
`_set_stall(active=True, ...)` before returning FIRE, so the
Status Bar's `STALLED` slot lights up identically to a STUCK
classifier verdict or a SILENT_SUBAGENT fire. The SESSION_CEILING
bypass still bypasses the StuckClassifier (the cap is not a
stuck-detection signal), it just preserves the stall-state
transition that the Status Bar subscribes to.

### `STALL_RESUMED` trigger sites

Five production sites clear the runtime flag and emit `STALL_RESUMED`
via `_set_stall`:

- `ralph/agents/idle_watchdog/idle_watchdog.py:646` — inside
  `_reset_idle_baseline()` (called by `record_activity` and the
  post-tool-result path).
- `ralph/agents/idle_watchdog/idle_watchdog.py:787` — inside
  `_accumulate_waiting_run()` on the `EXITED` transition (the wait
  is over, any prior `SUSPECTED_FROZEN` / `HARD_STOP`-driven stall
  is stale).
- `ralph/agents/idle_watchdog/_activity_methods.py:97` — inside
  `record_invocation_start()`; a fresh invocation clears a stall left
  behind by the previous one.
- `ralph/agents/idle_watchdog/_activity_methods.py:102-105` — inside
  `record_invocation_end()`; teardown clears a stall before its listener
  binding is removed by either reader.
- `ralph/agents/idle_watchdog/_gate.py:63` — in `gate_fire()`, when the
  StuckClassifier does not fire and the runtime flag is still set.

`_set_stall(active=False, ...)` is idempotent: a redundant call
emits no event. The flag stays in lockstep with the watchdog's
own assessment (a single source of truth), and the
`is_stalled` property exposes the runtime state for tests and
diagnostics.

### Plumb to the Status Bar host

`ralph/display/subscriber.py` accepts an optional constructor
parameter `watchdog_attention_sink: Callable[[str | None], None] | None`.
Every `WaitingStatusEvent` carries `stall_active`, the watchdog's
assessment at emission time. `record_waiting_status` mirrors that flag to
the sink on every event (`"stalled"` when true, otherwise `None`), so any
live watchdog event re-synchronizes the host without a display-side latch.
The sink call is wrapped defensively (a misbehaving host cannot break the
snapshot path).

`ralph/display/parallel_display.py` exposes a thread-safe
`watchdog_attention: str | None` field with a `watchdog_attention`
property and a `set_watchdog_attention(value)` setter. The
`PipelineSubscriber(...)` construction site at
`ralph/display/parallel_display.py:499` wires
`watchdog_attention_sink=self.set_watchdog_attention`.

### Status Bar host substitution

`ralph/display/status_bar.py` implements the host substitution in
`_model_with_live_attention`. On each Live tick, when
`model.attention is None` and the host display reports a watchdog
attention, the host returns
`dataclasses.replace(model, attention=live)`. Pushed operator
states (`waiting` / `retrying` / `terminated`) ALWAYS win: a host
that reports `stalled` while the runner pushed `waiting` does not
overwrite the pushed state. The defensive `getattr` read keeps
stub hosts (no `watchdog_attention` slot) degrading to
the pushed model rather than raising inside the render callback.

`_resolve_attention_state` reads `model.attention` (the pushed
state OR the host-substituted watchdog value) and, when that is
`None`, falls back to `model.operation_display_state`; it indexes the
resolved value against `ATTENTION_PRESENTATION` and derives no stall
state from a time gap. Unknown values are ignored defensively so a
future addition to the named state set cannot poison the slot.

### Display formatting

`ralph/display/subscriber.py::_format_waiting_status_line` renders
explicit branches for the two kinds — `STALLED` renders
`Agent stalled (idle_elapsed=Ns, watchdog assessment)` and
`STALL_RESUMED` renders
`Agent resumed after stall (idle_elapsed=Ns)`. Both branches
NEVER fall through to the `hit hard ceiling` template reserved
for `HARD_STOP` (the kind dispatch is locked by
`audit_lint_bypass.py`, which treats every inline `# noqa` as a violation).

### Stall-label lifetime

A `STALLED` label belongs to the invocation whose watchdog raised it. The
subscriber mirrors every `WaitingStatusEvent.stall_active` assessment; it does
not latch transition kinds. Both reader teardown paths call
`record_invocation_end()` before removing their waiting listener, so the final
false assessment clears an active label. A healthy invocation remains silent.

No consumer may copy `watchdog_attention` into a pushed
`StatusBarModel.attention`: the Status Bar substitutes the host value on each
Live tick only when the pushed operator attention is `None`. Conflict-resolution
and policy-remediation status pushes follow that rule.

### Run cleanup

`ralph/pipeline/run_loop.py::_cleanup_pipeline` calls
`active_display.set_watchdog_attention(None)` alongside
`unsubscribe_bus()` / `unsubscribe_display()` so a stale
`stalled` value never outlives the run. The pushed `terminated`
state wins during shutdown rendering, and clearing the watchdog
attention here prevents a leaked state across runs.

### Pin tests (black-box capture)

- `tests/agents/test_idle_watchdog.py` — the
  watchdog-sourced transition contract: `_set_stall` emits
  `STALLED` exactly once on entry into a stall and `STALL_RESUMED`
  exactly once on exit; repeated ticks emit no duplicates; the
  `STALL_RESUMED` triggers (`record_activity`,
  `record_invocation_start`, `record_invocation_end`,
  `_accumulate_waiting_run` EXITED)
  each flip the flag and emit the matching kind; a stall
  oscillating across many ticks still emits exactly-once per
  transition. The `is_stalled` property mirrors the runtime state.
- `tests/agents/invoke/test_reader_stall_lifetime.py` — the reader
  teardown publishes the clear: the last two emitted events are
  `STALLED` then `STALL_RESUMED`, and the final event carries
  `stall_active is False`.
- `tests/unit/display/test_parallel_display_t22.py` — the
  `watchdog_attention_sink` mirror: a `STALLED` event with
  `stall_active=True` sets the host's `watchdog_attention` to
  `"stalled"`, and the matching `STALL_RESUMED` event with
  `stall_active=False` clears it.
- `tests/pipeline/test_run_loop_status_bar_wiring.py` — the
  production push path carries no `last_activity_monotonic`
  (no display-side derivation); the host's `watchdog_attention`
  slot exists, defaults to `None`, and the setter writes the slot;
  the pushed model leaves `attention` at the operator-pushed value.

### `STALLED` label behavior

A healthy agent with a quiet stdout stays clean indefinitely: the
bar renders `STALLED` only while the watchdog reports a stall
assessment. A stalled run flips to `STALLED` when the watchdog
emits the stall-state transition — i.e. the pre-fire
`SUSPECTED_FROZEN` suspect-threshold cross, the `HARD_STOP`
ceiling cross, or a non-absolute `FIRE` verdict — until the
watchdog itself raises the alarm. `SUSPECTED_FROZEN` is a
stall assessment (the watchdog has not yet returned a `FIRE`
verdict — the second ``evaluate()`` still returns
``WAITING_ON_CHILD``) and is paired with `STALLED` because the
operator-visible signal is "the watchdog believes this is a
stall", not "the watchdog has actually fired".

---

## AC-5 — Recovery & stopping visibility

> The R1–R8 watchdog spec covers product-brief acceptance criteria
> 1, 2, 3, 4, and 6. Acceptance criterion 5 — *Users can understand
> why Ralph is waiting, recovering, or stopping* — is the only one
> the watchdog pin tests do not cover. The 'waiting' half is well
> covered by the existing ``WAITING`` / ``RESUMED`` lines in
> ``_run_inner_loop`` (R6) and the structured waiting-status
> heartbeat. The 'recovering' and 'stopping' halves are NOT
> watchdog-internal: they originate from ``RecoveryController``
> publishing ``FalloverEvent`` (fallover) and ``FailureEvent``
> (watchdog-kill / terminal) onto the ``FailureEventBus`` at
> ``ralph/recovery/events.py``. The bus carries two subscribers — a
> loguru logger and the display subscriber below — so a fallover or
> a watchdog-driven kill is visible to the operator at the kill
> moment, including *why* Ralph is recovering or stopping.

### Display surface

A second subscriber on the same ``FailureEventBus``,
``_subscribe_recovery_display`` at
``ralph/pipeline/run_loop.py:3512``, mirrors the existing
``_subscribe_recovery_logger`` (line 3459) and the ``WAITING`` /
``RESUMED`` direct-emit pattern. It routes every fallover /
failure-event through ``emit_activity_line(active_display, None,
status_text(...))`` directly — without a ``PipelineSubscriber``
method, without ``snapshot.py`` / ``parallel_display.py`` subscriber
plumbing. The emitted lines are:

| Event | Tag | Label | Style | Value |
|-------|-----|-------|-------|-------|
| ``FalloverEvent`` | ``fallover`` | ``RECOVERING`` | yellow | ``falling over from <from_agent> to <to_agent> (<reason>)`` |
| ``FailureEvent`` with ``watchdog_reason`` and ``chain_capacity_remaining > 0`` | ``watchdog_recoverable`` | ``RECOVERING`` | yellow | ``agent stalled: <watchdog_reason>; resuming`` |
| ``FailureEvent`` with ``chain_capacity_remaining <= 0`` | ``terminal`` | ``STOPPING`` | red | ``agent stalled: <watchdog_reason>; chain exhausted (<category>: <reason>)`` (or, when ``watchdog_reason is None``, ``chain exhausted (<category>: <reason>)``) |

The third row matches the product-brief 'or stopping' half:
the run-loop's terminal-phase ``state.last_error or 'Unknown
error'`` fallback sits behind a separate user-facing line
that names the actual root cause (cycle cap, exhausted chain,
category, reason) so an operator is never left with 'Unknown
error' alone.

### Cadence (low-noise)

The display subscriber applies a per-event-kind-tag throttle
keyed by an injected ``now`` callable (production
``time.monotonic``; tests inject ``FakeClock.monotonic``). The
throttle window is ``agent_waiting_status_interval_seconds``,
defaulting to the constant ``WAITING_STATUS_INTERVAL_SECONDS``
(30.0) at ``ralph/timeout_defaults.py:67``. The cadence uses the
``GeneralConfig.agent_waiting_status_interval_seconds`` config
field (``ralph/config/general_config.py:233``); no separate noise
knob exists. Repeated same-kind
events within the window emit at most one line; an event AFTER
the window elapses emits a fresh line.

The cadence gate reads time via the injected ``now`` callable so
the AC-03 cadence test in
``tests/pipeline/test_run_loop_recovery_surface.py`` is
deterministic (the test freezes a ``FakeClock`` at t=0, publishes
five fallover events, asserts exactly one captured line, advances
the FakeClock past the interval, and asserts a second line on
the next publish).

### Wiring seam

The display subscriber is registered AFTER ``active_display`` is
assigned (the LOCAL variable at
``ralph/pipeline/run_loop.py:3942``, just after
``_setup_active_display`` at line 3942) and AFTER
``loop_ctx = LoopContext(...)`` is built
(``ralph/pipeline/run_loop.py:3960``). The registration site is
the assignment ``_unsubscribe_display = _subscribe_recovery_display(...)``
at ``ralph/pipeline/run_loop.py:3954``. Registration occurs only
after ``active_display`` exists: the LOCAL ``active_display``
variable is assigned at line 3942.

The unsubscribe is threaded through a required param
``unsubscribe_display`` on BOTH ``_execute_with_cleanup``
(``ralph/pipeline/run_loop.py:3713``) and ``_cleanup_pipeline``
(``ralph/pipeline/run_loop.py:3637``). ``_cleanup_pipeline``
calls it inside the existing ``with suppress(Exception):`` block
right next to ``unsubscribe_bus()`` (line 3644), so a long
unattended daemon-mode run does not accumulate listeners on the
bus. Both functions have exactly one caller (the return at line
3987 and the finally at line 3813 respectively), so the required
parameter cannot silently diverge.

### Defensive contract (AC-04)

The display callback body is fully wrapped in ``try/except
Exception`` that fails silent to a debug log, mirroring the
existing ``_log_recovery_event`` at
``ralph/pipeline/run_loop.py:3461``. A display rendering
exception (a buggy renderer, a closed Rich console, an I/O
hiccup) cannot break recovery propagation. The AC-04 black-box
test in ``tests/pipeline/test_run_loop_recovery_surface.py``
exercises this contract: a display fake whose
``emit_activity_line`` raises ``RuntimeError("display boom")`` is
registered; the bus still returns normally from ``publish``; the
controller's ``snapshot()`` reports a non-error state.

### Pin test (black-box capture, not a watchdog pin)

- ``tests/pipeline/test_run_loop_recovery_surface.py`` — the
  BLACK-BOX CAPTURE test that asserts on captured
  ``emit_activity_line`` output strings. NOT enrolled in
  ``RALPH_PIN_TEST_PATHS`` and NOT listed in the R1–R8 pin-test
  inventory: that inventory is for watchdog criteria R1–R8 and is
  enforced bidirectionally by the
  ``watchdog-spec.md``/``RALPH_PIN_TEST_PATHS`` contract; this
  AC-5 surface is a pipeline display concern enforced by its own
  pytest run + ``make verify``. The test does not depend on any
  watchdog internal state — it drives a real ``RecoveryController``
  with a ``FakeClock`` and asserts on observable output only.

---

## R7 — Explain and handle the "mysterious" rc=0 exits

> Ambiguous rc=0 exits are root-caused, deterministically classified,
> and handled.

### Implementing modules

- `ralph/agents/invoke/_open_code_resumable_exit_error.py:6` —
  `class OpenCodeResumableExitError(AgentInvocationError)`; the
  typed exception that carries the canonical `resumable_session_id`
  attribute (the captured transport-level session id) AND four
  keyword-only diagnostic attributes for the R7 root-cause
  triage surface:
  `last_observed_tool_call` (the parsed tool-call verb from the
  line-reader layer, e.g. `"read_file"` or `"tool_use:Edit"`),
  `last_evidence_summary` (the watchdog's
  `last_evidence_summary(now).to_dict_list()` str-coerced payload),
  `elapsed_seconds` (the watchdog's `idle_elapsed_seconds(clock.monotonic())`
  at the moment of the exit), and `transcript_tail` (the last 10
  lines of the bounded output transcript, hard-capped via tuple
  slice). All four default to `None` / `()`, so callers that omit
  them are unaffected. When populated, the diagnostic context
  is appended to the exception message in a
  `[last_tool_call=..., elapsed=...]` suffix so a logged traceback
  is actionable without requiring a debugger. The exception's
  base diagnostic message text (`"completion sentinel missing, or
  required artifact receipt missing"`) is the root-cause signature
  produced by `ralph/agents/invoke/_completion.py` when an agent
  subprocess exits cleanly (rc=0) without the durable
  `declare_complete` sentinel or without a receipt required by the
  phase. Transcript markers are diagnostic only. No attribute or
  method of `OpenCodeResumableExitError` carries the
  `flagged_for_review` flag.
- `ralph/agents/invoke/_completion.py:177` —
  `@dataclass(frozen=True) class `CompletionCheckOptions`; the
  in-process dataclass that threads the four R7 diagnostic
  fields from the line-reader layer to the
  `OpenCodeResumableExitError` raise site at line 700. The
  four R7 diagnostic fields are enforced keyword-only via a
  Python `dataclasses.KW_ONLY` sentinel (Python 3.10+): a
  positional constructor call that targets the R7 surface
  raises `TypeError`. Defaults `None` / `()` keep the remaining
  ten positional fields optional. The dataclass is otherwise
  frozen; field types and defaults are stable.
- `ralph/agents/invoke/_completion.py:700` — the
  `raise OpenCodeResumableExitError(agent_name, session_id=..., ...)`
  site that forwards the four diagnostic fields from `opts` to
  the exception constructor. Callers that did not populate
  `opts` (e.g. non-watchdog paths) construct cleanly because
  every field defaults to `None` / `()`.
- `ralph/agents/invoke/_process_reader.py:1359` — the subprocess
  transport `CompletionCheckOptions` construction site that
  populates the four diagnostic fields from the watchdog
  instance held on the line reader (`reader._watchdog`, set at
  the start of `read_lines()`).
- `ralph/agents/invoke/_pty_runner.py:232` — the PTY transport
  `CompletionCheckOptions` construction site that mirrors the
  subprocess wiring with the PTY-side watchdog instance
  (`pty_reader._watchdog`, set at the start of `read_lines()`).
- `ralph/recovery/failure_classifier.py:530` —
  `class FailureClassifier`; the typed-cause classification
  pipeline.
- `ralph/recovery/failure_classifier.py:531` —
  `def FailureClassifier.classify`; the public entrypoint that
  routes typed exceptions to `FailureCategory`.
- `ralph/recovery/failure_classifier.py:684` —
  `def FailureClassifier._categorize_exc`; the typed-cause branch
  that maps `OpenCodeResumableExitError` to `FailureCategory.AGENT`
  (never `AMBIGUOUS`) and threads `resumable_session_id` through.

### R7 — Root-cause triage (repo-evidenced signals)

The R7 root-cause triage workflow uses ONLY signals with concrete
repo evidence. The diagnostic payload is built from the watchdog
state the line-reader layer already holds:

1. **`IdleWatchdog.last_evidence_summary(now)`** at
   `ralph/agents/idle_watchdog/idle_watchdog.py:689` (the per-channel
   evidence summary; `to_dict_list()` str-coerced payload). This
   is the same surface the watchdog-kill path surfaces under
   `merged_diag["evidence_summary"]` at
   `ralph/agents/invoke/_process_reader.py:852`.

2. **`IdleWatchdog.diagnostic_snapshot()["current_subagent_tool_call"]`**
   at `ralph/agents/idle_watchdog/_activity_methods.py:109` (the
   parsed tool-call verb; populated by `record_subagent_work`
   for the R5 PROGRESS surface).

3. **`merged_diag["evidence_summary"]`** populated at
   `ralph/agents/invoke/_process_reader.py:852` (the watchdog-kill
   payload; same `last_evidence_summary(now).to_dict_list()`
   pattern used at the R7 enrichment site).

4. **The `OpenCodeResumableExitError` diagnostic attributes**
   (`last_observed_tool_call`, `last_evidence_summary`,
   `elapsed_seconds`, `transcript_tail`) — the typed exception's
   R7 root-cause triage surface that an on-call operator reads
   from a logged traceback.

Speculative 'observed in the wild' scenarios are NOT enumerated
here — the headline PROMPT A.2365s+ indefinite deferral and the
headline PROMPT C. ambiguous rc=0 exit are the only root-cause
signals referenced, and both are pinned by the canonical pin
tests below. Any future 'observed in the wild' root-cause signal
MUST be cited with concrete repo evidence (file path + line
number) before being added to this list — fabrication_guard
level 1 rejects unsupported claims per AGENTS.md.

### Pin tests

- `tests/recovery/test_opencode_resumable_exit_classification.py`
  (6 tests) — proves the OpenCodeResumableExitError typed-cause
  branch classifies as `FailureCategory.AGENT`.
- `tests/recovery/test_opencode_resumable_exit_classifier.py`
  (7 tests) — proves the `FailureClassifier._categorize_exc` branch
  threads `resumable_session_id` through to the recovery controller.
  Together with the file above, 13 tests pin the R7 invariant.
  Two pin tests cover the diagnostic-attribute contract:
    - `test_diagnostic_context_carried`: the four diagnostic
      attributes (`last_observed_tool_call`,
      `last_evidence_summary`, `elapsed_seconds`, `transcript_tail`)
      are preserved through `FailureClassifier.classify`; the
      `AGENT`-not-`AMBIGUOUS` invariant is maintained; the
      exception message embeds the diagnostic context.
    - `test_backward_compatible_construction`: the two-argument
      form `OpenCodeResumableExitError(agent_name, session_id=...)`
      constructs cleanly with all diagnostic attributes defaulting
      to `None` / `()`.
- `tests/recovery/test_opencode_resumable_exit_producer_path.py`
  (2 tests) — proves the R7 PRODUCER-side root-cause contract:
  `ralph.agents.invoke.completion._check_process_result` raises
  `OpenCodeResumableExitError` carrying the captured `session_id`
  when the agent subprocess exits `rc=0` without completion evidence
  (no durable sentinel, or no required receipt). A regression at
  `_completion.py:700` (the `raise` statement) would silently break
  the R4 watchdog-driven resume contract — the recovery controller
  would lose the typed exception it lifts `resumable_session_id`
  from. Together with the classifier and
  classification pin files above, this third pin closes the
  producer→classifier→recovery chain end-to-end (15 R7 tests).
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r7`

---

## R8 — Clean, black-box-testable architecture

> Every edge case has a black-box test with injected clock and
> process sources — no real sleeps or subprocesses.

### Implementing modules

- `ralph/agents/idle_watchdog/timeout_policy.py:51` —
  `class TimeoutPolicy`; the typed configuration consumed by every
  watchdog surface (no hidden globals).
- `ralph/agents/idle_watchdog/idle_watchdog.py:143` —
  `class IdleWatchdog`; constructor takes injected `Clock`,
  `TimeoutPolicy`, optional `ProcessMonitor`, optional
  `Corroborator`, optional `ConnectivityStateProvider`, optional
  `WaitingStatusListener`.
- `ralph/process/monitor/_process_monitor.py:30` —
  `class ProcessMonitor(Protocol)`; tests inject `@dataclass` fakes
  (the `_HelpersOnlyMonitor` / `_FilteredCountMonitor` pattern in
  `tests/agents/test_idle_watchdog.py`
  and the consolidated suite).
- `ralph/agents/timeout_clock.py:15` — `class FakeClock`; the
  deterministic clock every watchdog test drives instead of
  wall-clock waits.
- `ralph/testing/audit_test_policy.py:206` —
  `class TestPolicyAuditor`; the AST-level audit
  module that enforces no real `time.sleep`, no `subprocess.run`
  without `timeout=`, and no real file I/O in non-`subprocess_e2e`
  tests (entrypoints at `audit_test_file` line 542 and
  `audit_tests_directory` line 578).

### Pin tests

- The **canonical RALPH pin-test set** (the entries of
  `RALPH_PIN_TEST_PATHS` in
  `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py`)
  — every entry uses `FakeClock` from `ralph.agents.timeout_clock`
  plus a local `@dataclass` `ProcessMonitor` Protocol fake (no real
  `time.sleep`, no real `subprocess.run`, no real `tmp_path` or
  `Path.read_text()`). This set is the canonical R8 audit target and
  the surface that `test_r8` enforces.
- `ralph/testing/audit_test_policy.py` (run via `make verify`) — the
  AST-level audit module that enforces the no-real-sleep / no-real-
  subprocess / no-real-file-IO contract on every non-`subprocess_e2e`
  test file across the whole `tests/` tree.
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r8`
  — the consolidated AC-08 assertion that the canonical RALPH
  pin-test set exists on disk, that the `ProcessMonitor` Protocol
  advertises both seam names (`spawned_subagent_count` and
  `live_subagent_count`), and that `FakeClock.advance(N)` is
  deterministic.

### Broader test-suite composition (NOT covered by the R8 black-box claim)

The consolidated watchdog suite (`tests/agents/test_idle_watchdog.py`)
contains three test styles that are distinct from the canonical RALPH
pin-test set. The
R8 black-box claim is **scoped to the canonical pin-test set above**;
these additional test styles are listed here for completeness and are
governed by their own contracts:

1. **End-to-end integration tests** (marked with
   `pytest.mark.subprocess_e2e`) — exercise real subprocess, real
   filesystem, and real wall-clock time by design. Example:
   `tests/agents/test_idle_watchdog.py`. These
   tests are explicitly excluded from
   `ralph/testing/audit_test_policy.py` enforcement and are the only
   path that verifies the production `DefaultProcessMonitor`
   against a real descendant tree.

2. **AST/source contract tests** — read the source via
   `Path.read_text()` to pin structural invariants that black-box
   tests cannot observe (e.g. "no `sys.exit` in the watchdog
   modules", "`WatchdogFireReason` is constructed only by the two
   canonical owner classes"). Examples:
   `tests/agents/test_idle_watchdog.py`,
   `tests/agents/test_idle_watchdog.py`. The
   `Path.read_text()` call is part of the watchdog contract
   verification path, not a test artefact; the test asserts on the
   parsed AST structure, not on file contents.

3. **Runtime-facing seam tests** — drive the line-reader layer (the
   place where the runtime actually emits
   `AgentInactivityTimeoutError`) via a synthesized invoke flow with
   temporary workspaces. Example:
   `tests/agents/test_idle_watchdog.py`.
   These tests use `tmp_path` because the runtime seam emits to a
   real workspace; the contract they pin is the canonical
   `WatchdogFireReason -> session_resume_safe` mapping at the line
   reader layer.

The consolidated `test_r8` does NOT assert these broader test styles
use `FakeClock` / no real I/O — it asserts only the canonical RALPH
pin-test set satisfies the black-box contract.
