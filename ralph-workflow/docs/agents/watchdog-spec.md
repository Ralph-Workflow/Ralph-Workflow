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
- `tests/agents/idle_watchdog/test_production_subagent_registry_wiring.py`
  — production SubagentPidRegistry wiring end-to-end pin; exercises
  the `AgentRegistry.build_subagent_pid_registry(transport)` →
  `BaseExecutionStrategy(subagent_pid_source=...)` →
  `classify_quiet` injection path AND the parser-side registry
  storage for every `AgentTransport` member (8 transports:
  OpenCode, Claude, Claude-interactive, Codex, Nanocoder, Generic,
  Agy, Pi). Pinned in wt-021 to lock the production wiring and
  catch any future refactor that bypasses the registry seam.
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r1`

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
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r2`

---

## R3 — No false negatives (always catch real hangs)

> Every genuine hang fires within a bounded ceiling, even when a
> non-subagent process looks like a lingering child.

### Implementing modules

- `ralph/agents/idle_watchdog/_active_branch.py:226` —
  `def evaluate_inner`; the session-ceiling + repeated-error
  breaker + strictly-stuck ceiling + no-progress ceiling
  pre-checks.
- `ralph/agents/idle_watchdog/_active_branch.py:264` —
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
- `ralph/agents/idle_watchdog/_waiting_branch.py:238-247` —
  cumulative waiting ceiling block (now hard-enforced per R3);
  fires `CHILDREN_PERSIST_TOO_LONG` UNCONDITIONALLY when
  `candidate_total >= effective_ceiling`, without consulting
  `self._gate_fire`. The sub-ceiling block at lines 158-237
  RETAINED its `_gate_fire` consultation because that branch is
  the smart sub-ceiling bounded by `stuck_job_sub_ceiling_seconds`
  (default 600s); the cumulative ceiling is the absolute
  backstop. Per PROMPT R3: "There must be a hard, bounded ceiling
  after which a true hang fires regardless of deferral reasons."
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
- `tests/agents/idle_watchdog/test_pure_stall_wedge.py`
- `tests/agents/idle_watchdog/test_cumulative_waiting_ceiling_fires_with_real_subagent_alive.py`
  — the NEW R3 regression pin for the cumulative ceiling hard
  enforcement. Exercises two scenarios via `_classify_stuck_now`
  override: (1) `SILENT_SUBAGENT` (the 2365s indefinite deferral
  regression) and (2) `LOADING` (a productive liveness signal).
  Both cases MUST fire `CHILDREN_PERSIST_TOO_LONG` at the ceiling.
  Uses `FakeClock` + Protocol-typed `@dataclass` `ProcessMonitor`
  fake (NO real subprocess), in scope for the canonical R8 audit
  target.
- `tests/agents/idle_watchdog/test_stuck_job_heartbeat_ceiling.py`
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
  heartbeat-only ceiling. Pinned in wt-021 to lock the
  heartbeat-only ceiling enforcement.
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r3`

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
- `ralph/agents/invoke/_open_code_resumable_exit_error.py:133-145` —
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
- `tests/agents/idle_watchdog/test_resume_contract_invariant.py`
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
- `tests/agents/idle_watchdog/test_log_spam_throttle_public_surface.py`
  — the NEW R6 public-surface proof that drives the watchdog via
  `watchdog.evaluate(classify_quiet=...)` only (NO `setattr` on
  `_classify_stuck_now`, NO direct call to `_gate_fire`, NO read
  of `_last_*_log_at`). Captures `WaitingStatusEvent` instances via
  `register_waiting_status_listener` (the public listener API) and
  counts both PROGRESS-kind emissions and loguru StringIO records
  filtered on `component='idle_watchdog'`. The 1000-call cycle
  MUST emit `<= 2` PROGRESS events (the R6 spam-invariant).
  Pinned in wt-021 to lock the R6 throttle contract via
  public-surface observables only.
- `tests/agents/idle_watchdog/test_evidence_deferral_throttle.py`
- `tests/agents/idle_watchdog/test_invocation_start_full_reset.py`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r6`
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r6_heartbeat`

### Heartbeat UX (chosen)

The prompt names either "a single updating line OR a low-frequency
heartbeat" as acceptable R6 UX. The watchdog implementation chose the
**low-frequency heartbeat**. While the watchdog is in
`WAITING_ON_CHILD` deferral, it emits a single periodic loguru INFO
record per `evaluate()` call whose cadence gate passes, with a
human-readable template naming:

  * **what is happening** — the agent is WAITING on a subagent,
  * **the live subagent count** from the FILTERED process monitor
    (R1 — the audit-enforced seam `spawned_subagent_count()`; the
    broader `descendant_snapshot()` count is NEVER used here),
  * **elapsed seconds** (rounded), and
  * **the hard ceiling seconds** (rounded).

The exact loguru INFO message template, emitted at
`ralph/agents/idle_watchdog/_waiting_branch.py:337-343`, is:

```
idle watchdog: agent waiting on subagent ({} alive) for {}s - hard ceiling at {}s
```

The cadence knob is
`TimeoutPolicy.waiting_status_interval_seconds`
(`ralph/agents/idle_watchdog/timeout_policy.py:140`), defaulting to
the constant `WAITING_STATUS_INTERVAL_SECONDS` (30s). The heartbeat
fires on every `evaluate()` call whose gate
`now - _last_waiting_status_at >= waiting_status_interval_seconds`
is satisfied, so the operator sees one updating line per cadence
window rather than the per-tick debug spam the prompt's evidence
exhibit described.

The WAITING entry log emitted at
`ralph/agents/idle_watchdog/_waiting_branch.py:122-126` is a
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

## R7 — Explain and handle the "mysterious" rc=0 exits

> Ambiguous rc=0 exits are root-caused, deterministically classified,
> and handled.

### Implementing modules

- `ralph/agents/invoke/_open_code_resumable_exit_error.py:133-145` —
  `class OpenCodeResumableExitError(AgentInvocationError)`; the
  typed exception that carries the canonical `resumable_session_id`
  attribute (the captured transport-level session id) AND four
  NEW keyword-only diagnostic attributes for the R7 root-cause
  triage surface:
  `last_observed_tool_call` (the parsed tool-call verb from the
  line-reader layer, e.g. `"read_file"` or `"tool_use:Edit"`),
  `last_evidence_summary` (the watchdog's
  `last_evidence_summary(now).to_dict_list()` str-coerced payload),
  `elapsed_seconds` (the watchdog's `idle_elapsed_seconds(clock.monotonic())`
  at the moment of the exit), and `transcript_tail` (the last 10
  lines of the bounded output transcript, hard-capped via tuple
  slice). All four default to `None` / `()` so legacy two-arg
  callers are unaffected. When populated, the diagnostic context
  is appended to the exception message in a
  `[last_tool_call=..., elapsed=...]` suffix so a logged traceback
  is actionable without requiring a debugger. The exception's
  base diagnostic message text (`"(no artifact, no
  declare_complete)"`) is the root-cause signature produced by
  `ralph/agents/completion_signals.py::find_declare_complete_marker`
  in `ralph/agents/invoke/_completion.py` when an agent subprocess
  exits cleanly (rc=0) WITHOUT a completion artifact AND WITHOUT
  the `declare_complete` marker. The historical `flagged_for_review=true`
  log line was the ambiguous-warning path that the deterministic
  classification introduced here explicitly removes; no current
  attribute or method of `OpenCodeResumableExitError` carries the
  `flagged_for_review` flag.
- `ralph/agents/invoke/_completion.py:108` —
  `@dataclass(frozen=True) class _CompletionCheckOptions`; the
  in-process dataclass that threads the four R7 diagnostic
  fields from the line-reader layer to the
  `OpenCodeResumableExitError` raise site at line 368. The
  four R7 diagnostic fields are enforced keyword-only via a
  Python `dataclasses.KW_ONLY` sentinel (Python 3.10+): a
  positional constructor call that targets the R7 surface
  raises `TypeError`. Defaults `None` / `()` preserve
  backward compatibility for the original nine positional
  fields. The dataclass is otherwise frozen; field types and
  defaults are stable.
- `ralph/agents/invoke/_completion.py:368` — the
  `raise OpenCodeResumableExitError(agent_name, session_id=..., ...)`
  site that forwards the four diagnostic fields from `opts` to
  the exception constructor. Callers that did not populate
  `opts` (e.g. non-watchdog paths) construct cleanly because
  every field defaults to `None` / `()`.
- `ralph/agents/invoke/_process_reader.py:945` — the subprocess
  transport `_CompletionCheckOptions` construction site that
  populates the four diagnostic fields from the watchdog
  instance held on the line reader (`reader._watchdog`, set at
  the start of `read_lines()`).
- `ralph/agents/invoke/_pty_runner.py:154` — the PTY transport
  `_CompletionCheckOptions` construction site that mirrors the
  subprocess wiring with the PTY-side watchdog instance
  (`pty_reader._watchdog`, set at the start of `read_lines()`).
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

### R7 — Root-cause triage (repo-evidenced signals)

The R7 root-cause triage workflow uses ONLY signals with concrete
repo evidence. The diagnostic payload is built from the watchdog
state the line-reader layer already holds:

1. **`IdleWatchdog.last_evidence_summary(now)`** at
   `ralph/agents/idle_watchdog/idle_watchdog.py:890` (the per-channel
   evidence summary; `to_dict_list()` str-coerced payload). This
   is the same surface the watchdog-kill path surfaces under
   `merged_diag["evidence_summary"]` at
   `ralph/agents/invoke/_process_reader.py:598`.

2. **`IdleWatchdog.diagnostic_snapshot()["current_subagent_tool_call"]`**
   at `ralph/agents/idle_watchdog/_activity_methods.py:253` (the
   parsed tool-call verb; populated by `record_subagent_work`
   for the R5 PROGRESS surface).

3. **`merged_diag["evidence_summary"]`** populated at
   `ralph/agents/invoke/_process_reader.py:598` (the watchdog-kill
   payload; same `last_evidence_summary(now).to_dict_list()`
   pattern used at the R7 enrichment site).

4. **The NEW `OpenCodeResumableExitError` attributes** added in
   step 5 of the wt-021 plan
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
  The two NEW pin tests added in wt-021 are:
    - `test_diagnostic_context_carried`: the four NEW diagnostic
      attributes (`last_observed_tool_call`,
      `last_evidence_summary`, `elapsed_seconds`, `transcript_tail`)
      are preserved through `FailureClassifier.classify`; the
      `AGENT`-not-`AMBIGUOUS` invariant is maintained; the
      exception message embeds the diagnostic context.
    - `test_backward_compatible_construction`: the legacy two-arg
      form `OpenCodeResumableExitError(agent_name, session_id=...)`
      constructs cleanly with all NEW attributes defaulting to
      `None` / `()`.
- `tests/recovery/test_opencode_resumable_exit_producer_path.py`
  (7 tests) — proves the R7 PRODUCER-side root-cause contract:
  `ralph.agents.invoke._completion._check_process_result` raises
  `OpenCodeResumableExitError` carrying the captured `session_id`
  when the agent subprocess exits `rc=0` without completion evidence
  (no artifact, no `declare_complete`). A regression at
  `_completion.py:368` (the `raise` statement) would silently break
  the R4 watchdog-driven resume contract — the recovery controller
  would lose its typed exception to lift `resumable_session_id` from
  and a clean rc=0-no-evidence exit would fall back to the
  ambiguous-warning path. Together with the classifier and
  classification pin files above, this third pin closes the
  producer→classifier→recovery chain end-to-end (20 R7 tests).
- `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py::TestTrustworthyIdleWatchdogSpec::test_r7`

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

The directory `tests/agents/idle_watchdog/` contains three test
styles that are distinct from the canonical RALPH pin-test set. The
R8 black-box claim is **scoped to the canonical pin-test set above**;
these additional test styles are listed here for completeness and are
governed by their own contracts:

1. **End-to-end integration tests** (marked with
   `pytest.mark.subprocess_e2e`) — exercise real subprocess, real
   filesystem, and real wall-clock time by design. Example:
   `tests/agents/idle_watchdog/test_e2e_activity_aware.py`. These
   tests are explicitly excluded from
   `ralph/testing/audit_test_policy.py` enforcement and are the only
   path that verifies the production `DefaultProcessMonitor`
   against a real descendant tree.

2. **AST/source contract tests** — read the source via
   `Path.read_text()` to pin structural invariants that black-box
   tests cannot observe (e.g. "no `sys.exit` in the watchdog
   modules", "`WatchdogFireReason` is constructed only by the two
   canonical owner classes"). Examples:
   `tests/agents/idle_watchdog/test_watchdog_recovery_contract.py`,
   `tests/agents/idle_watchdog/test_diagnostic_snapshot.py`. The
   `Path.read_text()` call is part of the watchdog contract
   verification path, not a test artefact; the test asserts on the
   parsed AST structure, not on file contents.

3. **Runtime-facing seam tests** — drive the line-reader layer (the
   place where the runtime actually emits
   `AgentInactivityTimeoutError`) via a synthesized invoke flow with
   temporary workspaces. Example:
   `tests/agents/idle_watchdog/test_runtime_session_resume_safe_mapping.py`.
   These tests use `tmp_path` because the runtime seam emits to a
   real workspace; the contract they pin is the canonical
   `WatchdogFireReason -> session_resume_safe` mapping at the line
   reader layer.

The consolidated `test_r8` does NOT assert these broader test styles
use `FakeClock` / no real I/O — it asserts only the canonical RALPH
pin-test set satisfies the black-box contract.