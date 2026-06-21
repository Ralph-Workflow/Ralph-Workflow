# Idle watchdog audit notes

Generated as research for the "ensure idle watchdog don't have false positives
and false negatives" task.  All references are repo-relative under
`ralph-workflow/`.

## 1. Subagent visibility surface

- `IdleWatchdog.record_subagent_work()` is defined in
  `ralph/agents/idle_watchdog/idle_watchdog.py` (line ~1397). It records the
  signal on the `subagent_output` first-party channel and stores a sanitized,
  200-char-truncated description in `_last_subagent_progress_description`.
- Production callers bind a context-var sink before the line loop:
  - `ralph/agents/invoke/_process_reader.py` (~line 353) sets both the MCP
    sink (`record_mcp_tool_call`) and the subagent sink
    (`record_subagent_work`).
  - `ralph/agents/invoke/_pty_line_reader.py` (~line 876) does the same.
- The sink is invoked from execution-strategy `observe_line()` when a line is
  classified as `CHILD_PROGRESS` or `CHILD_HEARTBEAT`:
  - `ralph/agents/execution_state/_base.py` (~line 89) for the base path.
  - `ralph/agents/execution_state/opencode_execution_strategy.py` (~line 144)
    for the OpenCode-specific path (it accepts an optional
    `_subagent_activity_sink` constructor argument and falls back to the
    contextvar).
- `WaitingStatusEvent.subagent_activity` is populated only by
  `IdleWatchdog._emit()` (`idle_watchdog.py` ~line 1976) from
  `self._last_subagent_progress_description`, so every waiting status event
  carries the most recent subagent description.
- There is currently **no public accessor** for the last subagent progress
  description; it is private state surfaced only through the event listener.
- There is currently **no default listener** that logs
  `WaitingStatusEvent.subagent_activity`; callers must supply their own
  `WaitingStatusListener` to see it.

## 2. Silent-after-tool-call wedge

### Already pinned

- `tests/agents/idle_watchdog/test_stuck_classifier.py` pins the pure
  `classify_stuck` `SILENT_SUBAGENT` branch: stale subagent output past
  `silent_subagent_seconds` + no fresh first-party/side-channel evidence +
  `classify_quiet=ACTIVE` -> `StuckKind.SILENT_SUBAGENT`. Fresh subagent
  output -> `THINKING`.
- `tests/agents/idle_watchdog/test_silent_subagent_runtime.py` pins the
  runtime seam: `IdleWatchdog._classify_stuck_now` threads
  `silent_subagent_seconds` into the classifier, the gate surfaces
  `SILENT_SUBAGENT` via `last_deferred_kind`, and `last_fire_reason` collapses
  to `DEFERRED_BY_STUCK_CLASSIFIER`.

### Gaps

- No dedicated regression test for the false-positive contract: a **single**
  in-process MCP `tools/call` followed by quiet for longer than
  `silent_subagent_seconds`, with a corroborator reporting
  `AliveBy.FRESH_PROGRESS`, must NOT fire (neither `NO_PROGRESS_QUIET` nor
  `NO_OUTPUT_AT_START`). The existing tests cover the classifier and runtime
  deferral seams separately, but not this integrated watch-and-corroborator
  scenario.
- The post-tool-result stall path (`STALLED_AFTER_TOOL_RESULT`) is driven by
  provider `TOOL_RESULT` activity, not by a single MCP `tools/call`, and is
  not directly tied to `silent_subagent_seconds`.

## 3. Pure-stall wedge

### Already pinned

- `tests/agents/idle_watchdog/test_no_progress_quiet_watchdog.py` covers
  `NO_PROGRESS_QUIET` firing when `alive_by=None` and no channel evidence is
  active, plus deferral when `alive_by!=None` or fresh tool-result activity
  exists.
- `tests/agents/idle_watchdog/test_no_output_at_start.py` covers
  `NO_OUTPUT_AT_START` firing when no activity occurs within
  `no_output_at_start_seconds`, plus deferral when subagent work is fresh.

### Gaps

- No dedicated regression test that exercises **zero recorded activity at
  all** (no stdout, no MCP tool call, no workspace event, no subagent work)
  and asserts:
  - past `no_progress_quiet_seconds` -> `FIRE` with
    `NO_PROGRESS_QUIET` (not missed)
  - within `no_output_at_start_seconds` -> `FIRE` with
    `NO_OUTPUT_AT_START`.
- Existing tests often seed at least one activity channel; a test that keeps
  every channel at zero would pin the pure-stall false-negative contract.

## 4. Resume-after-kill watchdog boundary

### Contract

The chain is:

1. `IdleWatchdog.evaluate()` chooses a `WatchdogFireReason`.
2. The reason is surfaced through `IdleWatchdogKilledError.reason`.
3. `ralph/agents/invoke/_process_reader.py` asks
   `_is_resumable_fire_reason(reason)` to decide `session_resume_safe`.
4. The recovery controller maps `session_resume_safe=True` +
   `has_prior_session=True` -> `action="resume"`.

### Ownership of the sets

- `_EXPECTED_FIRE_REASONS` (the import-time enum lock) is owned by
  `ralph/agents/idle_watchdog/idle_watchdog.py` (~line 129). It asserts that
  the actual `WatchdogFireReason.__members__` equals the explicit allowlist.
- `_RESUMABLE_FIRE_REASONS` (the resume set) is owned by
  `ralph/agents/invoke/_process_reader.py` (~line 113). It contains the six
  resumable reasons:
  `NO_OUTPUT_AT_START`, `NO_OUTPUT_DEADLINE`, `NO_PROGRESS_QUIET`,
  `STALLED_AFTER_TOOL_RESULT`, `REPEATED_ERROR_LOOP`,
  `REPEATED_IDENTICAL_TOOL_CALL`.

### Literal duplication / related sites

The literal list of the six resumable reasons appears in more than one place:

- `ralph/agents/invoke/_process_reader.py` (~line 113) as the canonical
  `_RESUMABLE_FIRE_REASONS` frozenset.
- `tests/agents/idle_watchdog/test_runtime_session_resume_safe_mapping.py`
  (~line 87) re-declares the expected set as
  `_RESUMABLE_REASONS_EXPECTED` and asserts it equals the imported
  `_RESUMABLE_FIRE_REASONS`.
- `tests/test_subprocess_reader_resume_safe.py` (~line 435) lists the same
  six reasons inline for parametrization.
- `ralph/agents/idle_watchdog/idle_watchdog.py` (~line 912) and
  `docs/agents/watchdog-architecture.md` (~line 18) document the same set in
  prose/comments.

Currently there is **no single invariant test** that checks every member of
`_EXPECTED_FIRE_REASONS` is either in `_RESUMABLE_FIRE_REASONS` or in the
explicit non-resumable exclusion set (`CHILDREN_PERSIST_TOO_LONG`,
`SESSION_CEILING_EXCEEDED`, plus post-exit reasons). Such a test would fail
if a future `WatchdogFireReason` member is added without updating the resume
contract.

## 5. Discovery strategy / transport mapping

- `ralph/agents/invoke/_monitor_factory.py` defines
  `_discovery_strategy_for_config()` (~line 26). It currently ignores the
  transport and unconditionally returns `NullDiscoveryStrategy()`.
- `_make_discovery_strategy()` (~line 52) returns `None` when
  `policy.subagent_output_capture_enabled` is False, otherwise calls the
  function above.
- `AgentTransport` has eight members: `CLAUDE`, `CLAUDE_INTERACTIVE`,
  `CODEX`, `OPENCODE`, `NANOCODER`, `GENERIC`, `AGY`, `PI`.
- No transport currently documents a stable per-worker subagent output log
  path; visibility comes from stdout forwarding, MCP tool-call events, and
  the OpenCode `_subagent_activity_sink`.
- Existing coverage:
  - `tests/agents/invoke/test_invoke_monitor_wiring.py` parametrizes the
    discovery-strategy wiring but intentionally excludes `PI`.
  - `tests/process/monitor/test_monitor_consolidation.py` covers all
    transports for the role classifier and `NullDiscoveryStrategy`.
  - No test under `tests/process/monitor/` directly asserts that
    `_discovery_strategy_for_config` returns `NullDiscoveryStrategy` for
    every `AgentTransport` member including `PI`.
