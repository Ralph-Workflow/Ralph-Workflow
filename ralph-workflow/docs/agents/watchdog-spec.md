# Trustworthy Idle Watchdog — Spec Traceability Map

This document is the **traceability map** between each acceptance
criterion (R1–R8) of the Trustworthy Idle Watchdog spec and the
implementing module + pin test that enforces it. It is intentionally
tutorial-free: no code, no implementation walkthrough — only the
per-AC linkage. Drift between this document and the codebase is caught
by the consolidated pin test
`tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::test_r8`
(whose `RALPH_PIN_TEST_PATHS` references every dedicated pin test file
listed below); if a file is renamed or moved, `test_r8` fails and the
doc must be updated to match.

For architectural context, see
[`watchdog-architecture.md`](watchdog-architecture.md). For the
underpinning cross-transport subagent visibility contract, see the
R5 section below and the per-transport parametrize at
`tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py`.

---

## R1 — Child-process monitors count only real subagents

> Child-process / per-agent monitors count only real subagents; host
> and internal helper spawns are provably excluded.

### Implementing modules

- `ralph/agents/idle_watchdog/_subagent_identity.py:82` —
  `class SubagentIdentity`; the per-transport source tag is the seam
  the filter consults.
- `ralph/agents/idle_watchdog/_subagent_identity.py:130` —
  `class SubagentPidRegistry`; FIFO-capped at
  `_MAX_REGISTRY_ENTRIES` (1024), shared across transports, filtered
  by `source`.
- `ralph/process/monitor/_process_monitor.py:18` —
  `class ProcessRole(StrEnum)`; the role enum every per-agent
  monitor tags its observations with.
- `ralph/process/monitor/_process_monitor.py:42` —
  `class ProcessMonitor(Protocol)`; canonical seam every per-agent
  monitor implements.
- `ralph/process/monitor/_process_monitor.py:78` —
  `ProcessMonitor.live_subagent_count`; canonical filtered count.
- `ralph/process/monitor/_process_monitor.py:91` —
  `ProcessMonitor.spawned_subagent_count`; canonical filtered count
  (preferred alias; returns the same filtered value as
  `live_subagent_count`).
- `ralph/process/monitor/_default_monitor.py:136` —
  `DefaultProcessMonitor.live_subagent_count`; production
  implementation that excludes helpers.
- `ralph/process/monitor/_default_monitor.py:146` —
  `DefaultProcessMonitor.spawned_subagent_count`; production
  filtered count.

### Pin tests

- `tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py`
- `tests/agents/idle_watchdog/test_hard_ceiling_with_helpers_alive.py`
- `tests/agents/idle_watchdog/test_shared_subagent_pid_registry.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::test_r1`

---

## R2 — No false positives (don't kill healthy or waiting work)

> No watchdog kill occurs while activity is recent or a real subagent
> is working.

### Implementing modules

- `ralph/agents/idle_watchdog/_gate.py:16` — `def classify_stuck_now`;
  the StuckClassifier input combiner consumed by the smart-verdict
  gate.
- `ralph/agents/idle_watchdog/_gate.py:99` — `def gate_fire`; the
  gate that returns `WatchdogVerdict.CONTINUE` for every non-STUCK
  classification.
- `ralph/agents/idle_watchdog/_stuck_classifier.py:290` —
  `def classify_stuck`; pure-function mapping from
  `AgentExecutionState` + channel evidence + corroboration to
  `StuckKind`.
- `ralph/agents/idle_watchdog/_active_branch.py:226` —
  `def evaluate_inner`; the central verdict path that consults every
  escape hatch.
- `ralph/agents/idle_watchdog/_fire_evaluators.py:118` —
  `def evaluate_no_progress_quiet`; the strictly-stuck and
  no-progress-quiet ceiling enforcement.
- `ralph/agents/idle_watchdog/_fire_evaluators.py:303` —
  `def evaluate_strictly_stuck`; the orthogonal strictly-stuck
  ceiling for stuck-but-alive subagents.
- `ralph/agents/idle_watchdog/_fire_evaluators.py:419` —
  `def evaluate_no_output_at_start`; the premature first-output
  guard with multiple deferral gates (subagent activity, recent
  tool result, first-party channel freshness).

### Pin tests

- `tests/agents/idle_watchdog/test_silent_after_tool_call_wedge.py`
- `tests/agents/idle_watchdog/test_stuck_classifier.py`
- `tests/agents/idle_watchdog/test_no_output_at_start_loading.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::test_r2`

---

## R3 — No false negatives (always catch real hangs)

> Every genuine hang fires within a bounded ceiling, even when a
> non-subagent process looks like a lingering child.

### Implementing modules

- `ralph/agents/idle_watchdog/_active_branch.py:226` —
  `def evaluate_inner`; the session-ceiling + repeated-error
  breaker + strictly-stuck ceiling + no-progress ceiling
  pre-checks.
- `ralph/agents/idle_watchdog/_active_branch.py:262` —
  inline `SESSION_CEILING_EXCEEDED` check; the absolute
  `max_session_seconds=3300` bypass triggered when
  `session_elapsed >= self._config.max_session_seconds`.
- `ralph/agents/idle_watchdog/_active_branch.py:255-265` —
  session-ceiling early-exit block (the hard wall bypassing the
  smart-verdict gate for `SESSION_CEILING_EXCEEDED` only).
- `ralph/agents/idle_watchdog/_gate.py:99` — `def gate_fire`;
  bypass path for `WatchdogFireReason.SESSION_CEILING_EXCEEDED`
  (line 141) — the only reason that bypasses the gate.
- `ralph/agents/idle_watchdog/_waiting_branch.py:20` —
  `def effective_waiting_ceiling`; the bounded waiting ceiling
  computed from corroboration (`max_waiting_on_child_seconds=1800`,
  `stuck_job_sub_ceiling_seconds=600`).
- `ralph/agents/idle_watchdog/_fire_evaluators.py:118` —
  `def evaluate_no_progress_quiet`; the dumb-kill ceiling +
  heartbeat-only fire path.
- `ralph/agents/idle_watchdog/_fire_evaluators.py:303` —
  `def evaluate_strictly_stuck`; the orthogonal strictly-stuck
  ceiling for stuck-but-alive subagents.
- `ralph/agents/idle_watchdog/timeout_policy.py:134` —
  `TimeoutPolicy.max_session_seconds`; the configurable wall-clock
  cap consumed by the session-ceiling check above.

### Pin tests

- `tests/agents/idle_watchdog/test_hard_ceiling_with_helpers_alive.py`
- `tests/agents/idle_watchdog/test_stuck_job_sub_ceiling.py`
- `tests/agents/idle_watchdog/test_session_ceiling_no_resume.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::test_r3`

---

## R4 — Resume on watchdog kill, never restart

> Watchdog-driven kills resume the existing session; new sessions
> occur only on deliberate phase transitions.

### Implementing modules

- `ralph/agents/idle_watchdog/idle_watchdog.py:220` —
  `class IdleWatchdog`; the `_RESUMABLE_FIRE_REASONS` set +
  function-separated resume/fresh paths.
- `ralph/agents/idle_watchdog/_activity_methods.py:253` —
  `def diagnostic_snapshot`; carries `resumable_session_id: str | None`
  hardcoded to `None` (the watchdog itself never populates the
  field; it is a stable key reserved for the outer watchdog-kill
  readers that merge the snapshot into a larger post-mortem
  payload).
- `ralph/agents/invoke/_process_reader.py:609` —
  `merged_diag["resumable_session_id"] = captured_session_id`; the
  watchdog-kill path for subprocess transports populates this key
  on the OUTER `merged_diag` payload (NOT inside the inner
  `watchdog_snapshot` dict). The id is sourced from the visible-TUI
  `self._captured_session_id` populated from the agent session.
- `ralph/agents/invoke/_pty_line_reader.py:676` —
  `merged_diag["resumable_session_id"] = captured_session_id`; the
  watchdog-kill path for PTY transports populates this key on the
  OUTER `merged_diag` payload with the same semantics as the
  subprocess path.
- `ralph/agents/idle_watchdog_kill.py:19` —
  `class IdleWatchdogKilledError(Exception)`; the typed exception
  that carries the resumable session id through the
  failure-classifier pipeline. This is the CANONICAL carrier of
  the id for the failure-classifier pipeline; the `merged_diag`
  population above is the canonical carrier for log-only consumers
  that walk the post-mortem dict directly without inspecting the
  exception chain.
- `ralph/agents/invoke/_open_code_resumable_exit_error.py:73` —
  `class OpenCodeResumableExitError(AgentInvocationError)`; the
  typed rc=0 exit exception classified by R7 below.
- `ralph/agents/invoke/_session_resume.py:170` —
  `def recovery_action_for_failure_reason`; the function that maps
  a failure reason to `"resume"` vs `"fresh"`.

> **Contract note (resumable_session_id location):**
> `resumable_session_id` lives on THREE surfaces: (1) the outer
> `merged_diag` payload (set by `_process_reader.py:609` and
> `_pty_line_reader.py:676` at the moment of the watchdog kill), (2)
> the typed `IdleWatchdogKilledError.resumable_session_id` attribute
> (used by the failure classifier via `exc.__cause__`), and (3) the
> inner `IdleWatchdog.diagnostic_snapshot()` dict key
> `resumable_session_id` — hardcoded to `None`; the watchdog itself
> does NOT populate the field. Consumers MUST consult the outer
> `merged_diag` payload OR walk the exception chain
> (`failure.resumable_session_id`) — NOT the inner
> `diagnostic_snapshot()` key.

### Pin tests

- `tests/agents/idle_watchdog/test_resume_after_kill_contract.py`
- `tests/agents/idle_watchdog/test_resume_after_kill_watchdog_boundary.py`
- `tests/agents/idle_watchdog/test_resume_session_id_threading.py`
- `tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::test_r4`

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
| **PROGRESS**                   | `last_subagent_progress_description` | `subagent_activity`         | `str \| None`   | Free-form description text set by `record_subagent_work(description=...)`. EXISTING field — preserved for backward compatibility. |
| **LAST ACTIVITY**              | `last_subagent_progress_at`     | `last_subagent_progress_at`     | `float \| None` | Monotonic timestamp of the most recent subagent observation. NEW. |
| **CURRENT TOOL CALL**          | `current_subagent_tool_call`     | `current_subagent_tool_call`    | `str \| None`   | Parsed `verb:` prefix from `PROGRESS` when the description starts with a known tool-call verb; `None` otherwise. NEW. Parsed by `_parse_tool_call_from_description` in `ralph/agents/idle_watchdog/_activity_methods.py`. |

The known tool-call verbs are: `tool_use`, `tool_result`, `mcp_tool`,
`subagent`, `bash`, `read`, `write`, `edit`, `glob`, `grep`,
`webfetch`, `websearch`. The canonical production format from the
NDJSON parser layer is `tool_use:<name>` (colon, no space) per
`ralph/agents/parsers/claude_interactive.py` line 61.

PER-TRANSPORT COVERAGE REQUIRED: every `AgentTransport` member
(OpenCode, Claude, Claude-interactive, Codex, Nanocoder, Generic, Agy,
Pi) flows all three R5 fields through both surfaces via the
parametrized per-transport pin test at
`tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py`.

### Implementing modules

- `ralph/agents/idle_watchdog/_activity_methods.py:50` —
  `def _parse_tool_call_from_description`; the R5 CURRENT TOOL CALL
  parser (pure function).
- `ralph/agents/idle_watchdog/_activity_methods.py:253` —
  `def diagnostic_snapshot`; exposes all three R5 fields on the
  watchdog public surface.
- `ralph/agents/idle_watchdog/waiting_status_event.py:14` —
  `class WaitingStatusEvent`; the frozen dataclass carrying all
  three R5 fields on every emitted event.
- `ralph/agents/idle_watchdog/_active_branch.py:100` —
  `def emit`; the dispatcher that populates all three R5 fields on
  every emitted `WaitingStatusEvent`.
- `ralph/agents/idle_watchdog/_waiting_branch.py:92` —
  `def handle_waiting_branch`; the SUBAGENT_PROGRESS emit site that
  flows all three fields to operators.
- `ralph/agents/idle_watchdog/_activity_methods.py:425` —
  `def record_subagent_work`; the canonical producer of the R5
  surface (PROGRESS + LAST ACTIVITY).
- `ralph/agents/idle_watchdog/_activity_methods.py:126` —
  `def record_invocation_start`; the per-invocation reset that
  clears all three R5 fields to `None`.

### Pin tests

- `tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py`
  — per-transport parametrize over `list(AgentTransport)`;
  parametrized test methods assert all three R5 fields on BOTH
  surfaces (the watchdog `diagnostic_snapshot()` dict and the
  `WaitingStatusEvent` dataclass) for every supported transport.
- `tests/agents/idle_watchdog/test_subagent_progress_surface.py`
- `tests/agents/idle_watchdog/test_waiting_subagent_progress.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::test_r5`
  — the consolidated surface test; exercises all three R5 fields on
  the watchdog public surface AND on emitted `WaitingStatusEvent`
  instances for a single-watcher scenario.

---

## R6 — Quiet, meaningful output (kill the spam)

> No duplicate/high-frequency log spam in any state; waiting status
> is a single clear, periodic, human-readable message.

### Implementing modules

- `ralph/agents/idle_watchdog/_gate.py:193` —
  `def maybe_log_deferred`; the per-tuple
  `(fire_reason, deferred_kind)` throttle keyed on
  `watchdog_log_throttle_seconds`.
- `ralph/agents/idle_watchdog/_gate.py:230` —
  `def maybe_log_any_deferred`; the coarse per-`fire_reason`
  throttle that caps emissions to one DEBUG record per throttle
  window regardless of `deferred_kind` cycles.
- `ralph/agents/idle_watchdog/_active_branch.py:394` —
  `def maybe_log_evidence_deferral`; the per-channel
  evidence-deferral throttle that mirrors the per-tuple map for
  the activity-aware path.
- `ralph/agents/idle_watchdog/_waiting_branch.py:92` —
  `def handle_waiting_branch`; the structured-status emit path
  (ENTERED, PROGRESS, SUSPECTED_FROZEN, HARD_STOP) replacing the
  per-tick debug spam.
- `ralph/agents/idle_watchdog/timeout_policy.py:52` —
  `class TimeoutPolicy`; carries `watchdog_log_throttle_seconds`
  (line 301), `watchdog_subagent_progress_interval_seconds` (line
  307), `waiting_status_interval_seconds` (line 140) — the cadence
  knobs.
- `ralph/agents/idle_watchdog/_activity_methods.py:126` —
  `def record_invocation_start`; the per-invocation reset that
  clears every throttle map so a fresh run starts with empty maps.

### Pin tests

- `tests/agents/idle_watchdog/test_log_spam_throttle.py`
- `tests/agents/idle_watchdog/test_evidence_deferral_throttle.py`
- `tests/agents/idle_watchdog/test_invocation_start_full_reset.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::test_r6`

---

## R7 — Explain and handle the "mysterious" rc=0 exits

> Ambiguous rc=0 exits are root-caused, deterministically classified,
> and handled.

### Implementing modules

- `ralph/agents/invoke/_open_code_resumable_exit_error.py:73` —
  `class OpenCodeResumableExitError(AgentInvocationError)`; the
  typed exception carrying `resumable_session_id` and the
  `flagged_for_review` flag.
- `ralph/recovery/failure_classifier.py:597` —
  `class FailureClassifier`; the typed-cause classification
  pipeline.
- `ralph/recovery/failure_classifier.py:604` —
  `def FailureClassifier.classify`; the public entrypoint that
  routes typed exceptions to `FailureCategory`.
- `ralph/recovery/failure_classifier.py:856` —
  `def FailureClassifier._categorize_exc`; the typed-cause branch
  that maps `OpenCodeResumableExitError` to `FailureCategory.AGENT`
  (never `AMBIGUOUS`) and threads `resumable_session_id` through.

### Pin tests

- `tests/recovery/test_opencode_resumable_exit_classification.py`
  (6 tests) — proves the OpenCodeResumableExitError typed-cause
  branch classifies as `FailureCategory.AGENT`.
- `tests/recovery/test_opencode_resumable_exit_classifier.py`
  (5 tests) — proves the `FailureClassifier._categorize_exc` branch
  threads `resumable_session_id` through to the recovery controller.
  Together with the file above, 11 tests pin the R7 invariant.
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::test_r7`

---

## R8 — Clean, black-box-testable architecture

> Every edge case has a black-box test with injected clock and
> process sources — no real sleeps or subprocesses.

### Implementing modules

- `ralph/agents/idle_watchdog/timeout_policy.py:52` —
  `class TimeoutPolicy`; the typed configuration consumed by every
  watchdog surface (no hidden globals).
- `ralph/agents/idle_watchdog/idle_watchdog.py:220` —
  `class IdleWatchdog`; constructor takes injected `Clock`,
  `TimeoutPolicy`, optional `ProcessMonitor`, optional
  `Corroborator`, optional `ConnectivityStateProvider`, optional
  `WaitingStatusListener`.
- `ralph/process/monitor/_process_monitor.py:42` —
  `class ProcessMonitor(Protocol)`; tests inject `@dataclass` fakes
  (the `_HelpersOnlyMonitor` / `_FilteredCountMonitor` pattern in
  `tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py`
  and the consolidated suite).
- `ralph/agents/timeout_clock.py:16` — `class FakeClock`; the
  deterministic clock every watchdog test drives instead of
  wall-clock waits.
- `ralph/testing/audit_test_policy.py:211` —
  `class TestPolicyAuditor(ast.NodeVisitor)`; the AST-level audit
  module that enforces no real `time.sleep`, no `subprocess.run`
  without `timeout=`, and no real file I/O in non-`subprocess_e2e`
  tests (entrypoints at `audit_test_file` line 563 and
  `audit_tests_directory` line 625).

### Pin tests

- The entire `tests/agents/idle_watchdog/` suite — every test uses
  `FakeClock` + a `@dataclass` `ProcessMonitor` Protocol fake (no real
  sleep, no real subprocess, no real filesystem).
- `ralph/testing/audit_test_policy.py` (run via `make verify`).
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::test_r8`
  — the consolidated AC-08 assertion that the entire watchdog test
  suite passes the black-box testability contract.