# Idle Watchdog Timeout Policy

The Ralph Workflow idle watchdog decides whether an agent is stuck
from **all observable evidence of work**, not just stdout output.
The workspace channel (file changes detected by
``WorkspaceMonitor``) is class-aware: each file change is classified
into one of five ``WorkspaceChangeKind`` values and weighted
BINARILY (``0.0`` = drop, ``1.0`` = full activity).

Workspace evidence collection runs whenever a run has a
``workspace_path``, regardless of whether the progress UI
(``show_progress``) is enabled. A quiet unattended run that is doing
real file work is therefore not falsely killed as idle.

"Activity" means **demonstrated work**, not mere existence. An
OpenCode subagent process that is alive but has produced no output,
no tool calls, and no file changes for the configured idle window is
**not** evidence of progress. Once scoped Ralph child evidence goes
stale, the run falls back to the normal idle timeout instead of
lingering under the larger cumulative waiting-on-child ceiling. Raw
OS descendants alone defer the verdict only when Ralph never had
scoped visibility into the child in the first place.

> **Behavior change for existing operators** — the default policy
> is conservative: only source-code changes count as activity.
> If you previously relied on log-file activity to defer the
> ``NO_OUTPUT_DEADLINE`` verdict, add the
> ``agent_workspace_change_weights`` opt-in to your
> ``ralph-workflow.toml`` (see the [Migration](#migration) section
> below).

## Workspace change filtering

Each file change in the monitored workspace is classified by
``WorkspaceChangeClassifier`` into one of the five kinds:

| Kind       | Default weight | Recognized by                                                        |
|------------|---------------:|----------------------------------------------------------------------|
| `source`   | 1.0            | Source code / documentation extensions (`.py`, `.rs`, `.ts`, `.md`, etc.) |
| `log`      | 0.0            | `*.log`, `*.tmp`, `*.bak`, `*.swp`, `*~`, `*.pyc`, `*.pyo`            |
| `cache`    | 0.0            | Parent dirs: `.git`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `node_modules`, `.venv`, `.agent/tmp`, `.agent/raw`; filename glob: `completion_seen_*.json` |
| `artifact` | 0.0            | Parent dir: `.agent/artifacts`                                        |
| `other`    | 0.0            | Anything that does not match a specific rule                          |

The rule order is fixed (see
``ralph/agents/invoke/_workspace_change_classifier.py:classify``):

1. CACHE parent walk (against ``CACHE_PARENT_DIRS``)
2. CACHE filename glob (`completion_seen_*.json`)
3. ARTIFACT parent walk (against ``ARTIFACT_PARENT_DIRS``)
4. LOG name/extension (against ``LOG_SUFFIXES``)
5. SOURCE extension (against ``SOURCE_EXTENSIONS``)
6. OTHER (fallback)

The ``.agent`` top-level is intentionally NOT in ``CACHE_PARENT_DIRS``
so ``.agent/artifacts/plan.json`` is correctly classified as
``ARTIFACT`` (the prior implementation put ``.agent`` in
``CACHE_PARENT_DIRS`` and made ARTIFACT unreachable — that bug is
fixed in the current release).

## Weight semantics (binary only)

The weights are **binary**: ``0.0`` means the change is dropped
(it does NOT defer the ``NO_OUTPUT_DEADLINE`` verdict); ``1.0``
means the change counts as full activity. Intermediate values
(``0.5`` etc.) are rejected by the validator and reserved for a
future fractional-TTL feature. The default policy is conservative:
only ``source`` is weighted ``1.0``; all other kinds are weighted
``0.0``.

## Operator opt-in (the migration path)

To restore the prior "every file change counts" behavior for a
specific kind, set the weight to ``1.0`` in your
``ralph-workflow.toml`` under the ``[general]`` section. For
example, to count log files AND source code (and only those):

```toml
[general]
# ... other keys ...
agent_workspace_change_weights = { source = 1.0, log = 1.0, cache = 0.0, artifact = 0.0, other = 0.0 }
```

The dict is merged over the conservative defaults via
``_normalize_workspace_change_weights``, so unspecified keys fall
back to the default. Unknown keys (typos like
``agent_workspace_change_weights = { logs = 1.0 }``) raise
``ValueError`` at config-load time so the operator sees the bug
immediately rather than silently dropping the kind.

## Tier-labelled per-channel evidence summary

Every fire log embeds the per-channel evidence summary in the loguru
``extra=`` dict. The summary lists all five evidence channels in fixed
order, labels each with its ``tier`` (``first_party`` or ``side_channel``),
and shows whether that channel is allowed to defer the verdict
(``can_defer``). Only strong channels defer: first-party channels
(``mcp_tool``, ``subagent_output``) and quality-filtered workspace
changes (``workspace`` with positive source-code weight) set
``can_defer=true``; stdout, bare subagent liveness, and weak workspace
changes set ``can_defer=false``.

```
extra = {
    "evidence_summary": [
        {"channel": "stdout",            "tier": "first_party",   "last_at": ..., "age_seconds": ..., "counter": ..., "can_defer": false},
        {"channel": "mcp_tool",          "tier": "first_party",   "last_at": ..., "age_seconds": ..., "counter": ..., "can_defer": true},
        {"channel": "subagent_output",   "tier": "first_party",   "last_at": ..., "age_seconds": ..., "counter": ..., "can_defer": true},
        {"channel": "subagent_liveness", "tier": "side_channel",  "last_at": ..., "age_seconds": ..., "counter": ..., "can_defer": false, "alive_by": "..."},
        {"channel": "workspace",         "tier": "side_channel",  "last_at": ..., "age_seconds": ..., "counter": ..., "can_defer": true,
         "kind_breakdown": {"source": 5, "log": 0, "cache": 0, "artifact": 0, "other": 0}},
    ],
    "active_channel": "none",
    "activity_evidence_ttl_seconds": 30.0,
    "fire_reason": "no_output_deadline",
}
```

The post-mortem reader can see exactly which kinds were most
active at the moment of the fire, e.g. ``{"source": 5, "log": 0}``
indicates that the agent was making source-code changes but
emitting no stdout (the conservative default policy would still
defer the verdict on those source changes — this is the expected
behavior). A bare subagent PID with no observable output appears
under ``subagent_liveness`` with ``can_defer=false``; it is
reported but does not reset the idle clock.

## Migration

**Before this release**: every file change counted as workspace
activity (no classifier; every change updated the
``last_workspace_event_at`` timestamp).

**After this release**: only file changes whose kind is weighted
``1.0`` count. The default policy is conservative (only
``source`` is weighted ``1.0``).

**If you need the prior behavior**, opt the kinds back in:

```toml
[general]
agent_workspace_change_weights = { source = 1.0, log = 1.0, cache = 0.0, artifact = 0.0, other = 0.0 }
```

**Why the change**: the prior behavior gave log files / cache
files / artifact files equal weight with source-code changes, so a
debugging session that wrote a lot to ``agent.log`` (and nothing
else) would defer the watchdog indefinitely even if no real work
was happening. The new policy makes source-code changes the
primary signal of work, with log/cache/artifact activity dropped
by default to keep the verdict honest.

## Worked example

Default policy (``source=1.0``, all others ``0.0``):

| File change                            | Kind     | Verdict effect       |
|----------------------------------------|----------|----------------------|
| `src/foo.py`                           | source   | defer (counts)       |
| `tests/test_foo.py`                    | source   | defer (counts)       |
| `docs/README.md`                       | source   | defer (counts)       |
| `agent.log`                            | log      | dropped              |
| `__pycache__/foo.pyc`                  | cache    | dropped              |
| `.agent/tmp/stream.bin`                | cache    | dropped              |
| `.agent/artifacts/plan.json`           | artifact | dropped              |
| `random.bin`                           | other    | dropped              |

Opt-in for log files (``source=1.0``, ``log=1.0``, all others ``0.0``):

| File change                            | Kind     | Verdict effect       |
|----------------------------------------|----------|----------------------|
| `src/foo.py`                           | source   | defer (counts)       |
| `agent.log`                            | log      | defer (counts)       |
| `__pycache__/foo.pyc`                  | cache    | dropped              |

## Process monitor and subagent output capture

Three new ``[general]`` tunables control how the watchdog gathers
non-stdout evidence. They ship with safe defaults and require no
operator action:

| Config key | Default | Purpose |
|------------|---------|---------|
| ``agent_process_monitor_enabled`` | ``true`` | Enable the agent-agnostic process monitor that discovers spawned subagents and tracks their liveness. |
| ``agent_subagent_output_capture_enabled`` | ``true`` | Enable polling of observable subagent output streams as first-party evidence. |
| ``agent_subagent_output_poll_interval_seconds`` | ``1.0`` | Poll cadence for subagent output streams. |

When ``agent_process_monitor_enabled`` is ``false``, the watchdog
does not scan the process tree; subagent liveness is inferred only
from progress signals already received by the MCP server. When
``agent_subagent_output_capture_enabled`` is ``false``, subagent log
streams are not polled even if the process monitor is enabled. Set
these to ``false`` only when the agent's documentation confirms it
does not expose observable subagent output.

Subagent output capture is documentation-grounded per agent: the
strategy returns an empty mapping when the expected log layout is
not present on disk, so the watchdog degrades gracefully instead of
inventing paths.

## OS-descendant-only escalation

A wedged-but-alive subprocess appears in the OS process tree but produces no
observable progress signals (no output, no workspace changes, no MCP tool
calls). The watchdog previously relied on the 600s no-progress ceiling for
these cases, meaning a wedged opencode subprocess could run for 540s+ before
the first PROGRESS event fired.

Four new ``[general]`` tunables address this:

| Config key | Default | Purpose |
|------------|---------|---------|
| ``agent_os_descendant_only_ceiling_seconds`` | ``120.0`` | Short ceiling (~120s) for the OS-descendant-only case; fires CHILDREN_PERSIST_TOO_LONG in ~120s instead of waiting for the no-progress ceiling. Set to ``null`` to disable and fall back to the no-progress ceiling. |
| ``agent_os_descendant_only_suspect_seconds`` | ``60.0`` | Earlier SUSPECTED_FROZEN (~60s) for the OS-descendant-only case; fires at ``min(suspect_waiting_on_child_seconds, this value)``. Set to ``null`` to disable. |
| ``agent_cpu_idle_seconds`` | ``60.0`` | A known descendant PID with 0 user+system CPU time over this window is reported as ``CPU_IDLE_WHILE_ALIVE`` by the read-loop corroborator. The 60s default tolerates sub-step quiescence (I/O wait, GC pause). Set to ``null`` to disable the CPU probe. |
| ``agent_log_growth_seconds`` | ``30.0`` | The per-run ``.agent/raw/{safe_id}.log`` file not growing for this many seconds is reported as ``LOG_STALE_WHILE_ALIVE``. Set to ``null`` to disable the log-growth probe. |

Setting any key to ``null`` in ``ralph-workflow.toml`` opts out:

```toml
[general]
agent_cpu_idle_seconds = null   # disable CPU idle probe
agent_log_growth_seconds = null  # disable log-growth probe
```

**Event timeline** (OS-descendant-only case):

| Event | Trigger |
|-------|--------|
| ``ENTERED`` | ``idle_timeout_seconds`` deadline reached; child enters WAITING_ON_CHILD |
| ``PROGRESS`` | ``waiting_status_interval_seconds`` cadence (default 30s) |
| ``SUSPECTED_FROZEN`` | ``min(suspect_waiting_on_child_seconds, os_descendant_only_suspect_seconds)`` (default ~60s) |
| ``HARD_STOP`` | ``os_descendant_only_ceiling_seconds`` (default ~120s) |

**New alive_by labels**: the read-loop corroborator attaches
``CPU_IDLE_WHILE_ALIVE`` or ``LOG_STALE_WHILE_ALIVE`` to the
``CorroborationSnapshot`` when a known descendant has had zero CPU
activity for ``cpu_idle_seconds`` or the overflow log has not grown
for ``log_growth_seconds``. Both labels short-circuit the
OS-descendant-only ceiling and fall back to the no-progress ceiling.
``LOG_STALE_WHILE_ALIVE`` takes precedence over ``CPU_IDLE_WHILE_ALIVE``.

**New diagnostic keys**: the ``HARD_STOP`` and ``SUSPECTED_FROZEN``
diagnostics include ``effective_ceiling_label`` (``"os_descendant_only"``
| ``"no_progress"`` | ``"standard"``) and ``suspect_reason``
(``"os_descendant_only"`` | ``"standard"``):

```
idle watchdog: FIRE reason=CHILDREN_PERSIST_TOO_LONG ... effective_ceiling_label=os_descendant_only effective_ceiling=120.0
```

The five-channel ``evidence_summary`` surface (``stdout``, ``mcp_tool``,
``subagent_output``, ``subagent_liveness``, ``workspace`` in fixed order)
is **preserved unchanged**. The ``Tier-labelled per-channel evidence
summary`` section below details the five channels.

See also: ``tests/agents/idle_watchdog/test_os_descendant_only_escalation.py``,
``ralph/agents/idle_watchdog/idle_watchdog.py``,
``ralph/agents/idle_watchdog/timeout_policy.py``,
``ralph/process/_alive_by.py``,
``ralph/display/raw_overflow.py``.

## Per-transport subagent discovery

The table below maps each supported transport to the
documentation-grounded discovery strategy and command-line role
classifier used by the agent-agnostic process monitor. The upstream
source of truth for documentation citations and fallback rationales
is ``ralph/process/monitor/documentation-sources.md``; this table is
a renderer of that file, not a parallel copy.

| Transport | Documentation source | Subagent output discovery strategy | Role classifier from ``role_classifier_for_transport`` | Conservative fallback |
|-----------|----------------------|------------------------------------|--------------------------------------------------------|-----------------------|
| ``CLAUDE`` | ``documentation-sources.md`` § Claude Code | ``ClaudeCodeSubagentOutputDiscovery`` (reports channel unavailable) | ``_claude_code_role_classifier`` | ``INCIDENTAL_HELPER`` |
| ``CLAUDE_INTERACTIVE`` | ``documentation-sources.md`` § Claude Code | ``ClaudeCodeSubagentOutputDiscovery`` (reports channel unavailable) | ``_claude_code_role_classifier`` | ``INCIDENTAL_HELPER`` |
| ``OPENCODE`` | ``documentation-sources.md`` § OpenCode | ``OpencodeSubagentOutputDiscovery`` (reports channel unavailable) | ``_opencode_role_classifier`` | ``INCIDENTAL_HELPER`` |
| ``CODEX`` | ``documentation-sources.md`` § Codex CLI | none / channel unavailable | ``_codex_role_classifier`` | ``INCIDENTAL_HELPER`` |
| ``NANOCODER`` | ``documentation-sources.md`` § Nanocoder | none / channel unavailable | ``_nanocoder_role_classifier`` | ``INCIDENTAL_HELPER`` |
| ``AGY`` | ``documentation-sources.md`` § AGY | none / channel unavailable | ``_agy_role_classifier`` | ``INCIDENTAL_HELPER`` |
| ``GENERIC`` | ``documentation-sources.md`` § Fallback policy | none / channel unavailable | ``_generic_role_classifier`` | ``INCIDENTAL_HELPER`` |

For transports with no documented subagent output log path, the
discovery strategy reports the channel as unavailable. The watchdog
then degrades gracefully to the stdout, MCP tool-call, and workspace
evidence channels, and the diagnostic output records
``subagent output channel unavailable for transport X`` so the
operator can see which channels were observable.

For ``OPENCODE``, spawned subagent PIDs are also tracked through the
structured ``child_started`` lifecycle events that OpenCode emits on
stdout. That first-party evidence is independent of the command-line
classifier and is described in
``ralph/process/monitor/documentation-sources.md``.

See ``ralph/process/monitor/documentation-sources.md`` for the
per-agent documentation URLs, citations, and the procedure followed
when adding a new transport.

## Absolute ceilings are unaffected

The ``SESSION_CEILING_EXCEEDED`` and ``CHILDREN_PERSIST_TOO_LONG``
ceilings are checked BEFORE the activity-deferral hook in
``IdleWatchdog.evaluate()`` and remain absolute. A productive
session that is busy on a non-stdout channel cannot defeat either
ceiling. The per-kind weight policy is layered on top of the
existing activity-aware verdict and does not extend any
ceiling.

## Per-reason backoff and the forever-wait state

When an agent fails, it is classified with one of the following `UnavailabilityReason` values. Each reason has a specific exponential backoff policy (doubling on consecutive failures up to the cap):

| Unavailability Reason | Base Backoff | Max Backoff | Rationale |
|-----------------------|-------------:|------------:|-----------|
| `out_of_credits` | 60s | 30m | High backoff to allow credit reset or operator replenishment |
| `no_output_at_start` | 5s | 30s | Fast retry for agents that fail immediately with no output at start |
| `no_output_after_activity` | 10s | 120s | Moderate backoff for agents that freeze midway |
| `suspicious_timeout_no_output` | 10s | 60s | Backoff for hit waiting ceiling without progress |
| `stale_child_quiet` | 15s | 300s | Backoff for stuck child process with stale progress |

### The Forever-Wait Contract

If all agents in the recovery chain for a given phase are temporarily unavailable, the pipeline enters a **forever-wait state**. Rather than crashing or exiting, the run loop:
1. Emits a structured loguru `WAITING` line (at `INFO` level with `binding(recovery=True)`) containing the current phase, the last unavailability reason, details for all agents (cooldown, attempt counts), and the total wait duration.
2. Emits a structured loguru `DEBUG` line (also `binding(recovery=True)`) immediately before `ctx.sleep(...)` confirming the exact sleep duration and the phase. The DEBUG line carries the same `recovery=True` binding as the WAITING/RESUMED INFO lines so an operator can correlate the three records with a single `grep recovery=True` filter.
3. Sleeps for the minimum duration required for the earliest agent to become available again.
4. Emits a structured loguru `RESUMED` line (at `INFO` with `binding(recovery=True)`) when the sleep finishes and retries the phase with the newly available agent.

This ensures the pipeline remains alive indefinitely under transient outages.

The wait state is detected by the run loop via the structured
`PipelineState.is_waiting_state: bool` flag (set by `RecoveryController`
when it enters the wait branch), NOT via parsing the `last_error` text.
The `last_error` text remains as operator-readable context; the
structured flag is the single source of truth and the only signal the
run loop keys off. This separation matters because the controller and
the run loop are decoupled and have historically disagreed about the
exact text of the `last_error` string (the controller inserts the
`(last reason: ...)` segment which the run loop's previous text parser
did not match). The structured flag eliminates the entire class of
mismatch bugs.

### Default `no_output_at_start_seconds` tuning

The default threshold for `no_output_at_start_seconds` is tuned to **30s** (down from 60s). This threshold is:
- Long enough to accommodate typical 95th-percentile first-token latency of slower models (e.g. Claude Code or Opencode).
- Short enough to fall over to the next agent before hitting cumulative session or waiting ceilings.

### NO_OUTPUT_AT_START deferral gates (wt-021)

The NO_OUTPUT_AT_START trip is deferred (returns ``CONTINUE`` instead of ``FIRE``) when EITHER of the following LIVE signals is present at the moment of the evaluate:

- ``self._safe_corroborate()`` returns a ``CorroborationSnapshot`` with ``alive_by != None`` -- the corroborator (process tree / OS descendant scan / heartbeat) confirms a live child agent. The deferral consults ``_safe_corroborate()`` LIVE inside ``_evaluate_no_output_at_start`` (NOT ``self._last_alive_by``, which is stale post-fire state set only by ``NO_PROGRESS_QUIET`` at ``idle_watchdog.py:620`` and is never populated for ``NO_OUTPUT_AT_START``).
- ``self._cumulative_waiting_on_child_seconds > 0`` -- the agent has already survived a full ``WAITING_ON_CHILD`` entry/exit cycle this invocation, which demonstrates it is alive enough that ``NO_OUTPUT_AT_START`` no longer applies.

The deferral is consult-only -- the underlying NO_OUTPUT_AT_START trigger logic, the StuckClassifier gate, and the channel-evidence deferral are all unchanged. An agent that returns an empty ``CorroborationSnapshot`` AND has no prior waiting run still fires ``NO_OUTPUT_AT_START`` at the threshold (no false-positive deferral). See ``TestNoOutputAtStartLiveCorroborationDefer`` in ``tests/agents/test_idle_watchdog_no_output_at_start_lifecycle.py``.

### NO_OUTPUT_AT_START is now session-resume-safe

The ``NO_OUTPUT_AT_START`` reason has been added to the ``session_resume_safe`` whitelist in BOTH ``_process_reader.py`` (subprocess path) and ``_pty_runner.py`` (PTY path). A watchdog kill with reason ``NO_OUTPUT_AT_START`` now raises ``AgentInactivityTimeoutError`` with ``session_resume_safe=True`` and the captured ``resumable_session_id``, so the high-level ``invoke_agent`` seam resumes the SAME agent session via the orchestrator's resume-style retry (NOT a fresh-from-scratch restart). See ``tests/test_subprocess_reader_resume_safe.py::test_subprocess_reader_session_resume_safe_for_no_output_at_start`` and ``tests/test_claude_interactive_timeout_reason.py::test_run_pty_and_read_lines_resume_safe_for_no_output_at_start``.

### Cross-transport subagent visibility

A new ``_classify_generic_child_signal`` classifier in ``ralph/agents/execution_state/_helpers.py`` recognises permissive child-signal markers across transports:

- JSON envelopes whose ``type`` / ``event`` key is in ``{child_progress, progress, tool_call, task_progress}`` -> ``CHILD_PROGRESS``.
- JSON envelopes whose ``type`` / ``event`` key is in ``{child_heartbeat, heartbeat, child_alive, alive}`` -> ``CHILD_HEARTBEAT``.
- Plain-text markers (``[child]``, ``[subagent]``, ``subagent: ``, ``child: ``, ``subagent progress``, ``child progress``, ``task progress``) -> ``CHILD_PROGRESS``.
- Plain-text heartbeat markers (case-insensitive ``subagent heartbeat`` / ``child heartbeat``) -> ``CHILD_HEARTBEAT``.

The classifier is wired into ``BaseExecutionStrategy.observe_line`` so EVERY transport's ``observe_line`` automatically invokes the watchdog's subagent activity sink on child signals (no per-transport classifier needed). The OpenCode strategy continues to override ``observe_line`` entirely (it owns the specialised OpenCode wire format); the base implementation is only invoked for strategies that do NOT override ``observe_line`` (Claude, Codex, Generic, Agy, Nanocoder), so there is NO double-invocation. See ``tests/agents/execution_state/test_generic_child_signal.py``.

### Stuck-job intelligence across fire paths

The ``StuckClassifier`` is consulted by ``_gate_fire`` at every non-absolute fire path (``NO_OUTPUT_DEADLINE``, ``NO_OUTPUT_AT_START``, ``NO_PROGRESS_QUIET``, ``STALLED_AFTER_TOOL_RESULT``, ``CHILDREN_PERSIST_TOO_LONG``). The classifier returns one of six ``StuckKind`` values; the gate returns ``CONTINUE`` for any non-``STUCK`` kind so a productive session that does not look productive is not killed. The ``SESSION_CEILING_EXCEEDED`` reason is the only absolute reason that bypasses the gate (it is an operator-set hard cap, not a stuck-detection signal).

The classifier consults the per-channel evidence summary (``IdleWatchdog.last_evidence_summary(now)``) which includes a fresh subagent-liveness signal whenever a process monitor reports a live subagent, AND the cumulative-waiting / corroborator live signals via the watchdog's own evaluators. The ``_evaluate_no_progress_quiet`` evaluator consults ``_safe_corroborate()`` LIVE and defers the ``NO_PROGRESS_QUIET`` fire when ``corroboration.alive_by != None`` -- the cumulative ``CHILDREN_PERSIST_TOO_LONG`` ceiling (default 600s) is the correct upper bound for live-child stalls, NOT the 120s ``NO_PROGRESS_QUIET`` fire.

The ``_handle_waiting_branch`` CHILDREN_PERSIST_TOO_LONG fire path and the ``_evaluate_no_output_at_start`` NO_OUTPUT_AT_START fire path both thread the LIVE ``CorroborationSnapshot`` into the classifier via the new ``corroboration`` keyword argument on ``classify_stuck``. The classifier receives the LIVE corroboration so the gate's call site always has access to the current ``alive_by`` signal rather than relying on the stale ``self._last_alive_by`` post-fire field (which is only populated post-fire by ``NO_PROGRESS_QUIET``). The classifier's CURRENT verdict policy does NOT depend on the corroboration alone -- the watchdog's own ``_effective_waiting_ceiling`` math already handles alive_by-based ceiling selection (e.g. ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` short-circuits to the no_progress / os_descendant_only ceiling), and the classifier's job is to label the apparent stall (``StuckKind``), not to re-derive a wait/defer verdict from a different snapshot. The corroboration parameter is plumbed so future classifier extensions can use it (e.g. distinguishing truly-dead-child scenarios from process-monitor-only live signals) without changing the call site; if a future PR makes the classifier verdict depend on ``alive_by``, it MUST update the ``ClassifyStuckInputs.corroboration`` documentation AND the ``test_corroboration_*`` regression tests in ``tests/agents/idle_watchdog/test_stuck_classifier.py`` together. This is the analysis-feedback contract for AC-05: the gate's call site sees the LIVE corroboration rather than the stale ``self._last_alive_by`` field (which is only populated post-fire by ``NO_PROGRESS_QUIET``). See ``tests/agents/idle_watchdog/test_stuck_job_intelligence.py::test_children_persist_too_long_uses_live_corroboration_alive_by`` (corroborator consulted during fire decision) and ``test_children_persist_too_long_stale_corroboration_does_not_defeat_ceiling`` (FIRE for stale alive_by when no_progress ceiling is reached), and the unit tests in ``tests/agents/idle_watchdog/test_stuck_classifier.py::test_corroboration_*`` that pin the non-decisive-on-corroboration verdict policy.

### LIFECYCLE frames do NOT reset the NO_OUTPUT_AT_START baseline

The watchdog distinguishes between **meaningful output** and **cosmetic
lifecycle frames** (e.g. the opencode ``process started; waiting for first
output`` frame). Only meaningful output advances the NO_OUTPUT_AT_START
baseline; a LIFECYCLE frame resets the idle baseline (``_last_activity``) so
the agent is not declared idle, but it is deliberately excluded from the
``_last_meaningful_output_at`` timestamp that gates the NO_OUTPUT_AT_START
trip.

This fix closes a real-world bug in which an opencode subprocess that
produced a single LIFECYCLE frame at process startup and then emitted no
output for 15 minutes would silently defeat the fast-fallover path. With
the fix, the watchdog fast-fires NO_OUTPUT_AT_START at the 30s default and
the operator sees the agent in the structured WAITING log within tens of
seconds instead of waiting for the cumulative 600s no-progress ceiling.

The three semantic baselines are now independent:

| Field | Reset by | Purpose |
|-------|----------|---------|
| ``_last_activity`` | ``record_activity()``, ``record_lifecycle_activity()``, ``record_progress_report()`` (fingerprint change) | The idle baseline used by NO_PROGRESS_QUIET and the cumulative ceiling |
| ``_last_meaningful_output_at`` | ``record_invocation_start()``, ``record_activity()``, ``record_progress_report()`` (fingerprint change) | The NO_OUTPUT_AT_START baseline. LIFECYCLE frames are excluded by design |
| Per-channel ``_last_at`` (mcp_tool, subagent_output, workspace) | The corresponding ``record_*`` side-channel recorder | The positive-waiting suppression baseline (see below) |

LIFECYCLE frames are a real production signal — they are the only
evidence we have that the agent subprocess is alive at startup — but they
are NOT a substitute for real output. The watchdog therefore records them
in the idle baseline (so the agent is not declared idle) while leaving the
NO_OUTPUT_AT_START baseline untouched (so a hung agent is still caught).

### Positive-waiting suppression channels

The NO_OUTPUT_AT_START trip is suppressed (returns ``CONTINUE`` instead
of ``FIRE``) when ANY of three side-channel evidence recorders has been
called recently — i.e. while the channel timestamp is fresher than
``activity_evidence_ttl_seconds``. This is the prompt's
**"we are running some subagents and are just waiting"** branch: a
productive session that is busy on a non-stdout channel must not be
misclassified as unavailable.

The three channels are:

| Channel | Recorder | Tier | Evidence source |
|---------|----------|------|-----------------|
| `mcp_tool` | ``record_mcp_tool_call()`` | first-party | Ralph MCP server tools/call invocations / completions |
| `subagent_output` | ``record_subagent_work()`` / ``record_subagent_output()`` | first-party | Subagent heartbeat / phase-change / observable log-stream lines |
| `workspace` | ``record_workspace_event(kind=...)`` | side-channel | ``WorkspaceMonitor`` file-change events, quality-filtered by ``WorkspaceChangeKind`` weight |

The deferral logic in ``_channel_evidence_active`` consults the full
tier-aware evidence summary (see the [Tier-labelled per-channel evidence
summary](#tier-labelled-per-channel-evidence-summary) section above) and
defers the NO_OUTPUT_AT_START trip while any first-party or
quality-filtered side-channel is fresh. A bare subagent PID with no
observable output is reported in the summary under
``subagent_liveness`` with ``can_defer=False``; it is informational and
does NOT reset the idle clock.

These three channels are locked behind black-box tests in
``tests/agents/test_idle_watchdog_no_output_at_start_lifecycle.py``:
``test_subagent_work_progress_defers_no_output_at_start``,
``test_workspace_event_progress_defers_no_output_at_start``, and
``test_mcp_tool_call_progress_defers_no_output_at_start``. The first
``TestNoOutputAtStartLifecycleBypass`` class in the same file proves
that a LIFECYCLE frame does NOT defer the trip (the bug-expose test).

### Session-scoped `UnavailabilityStore` Protocol seam

The recovery controller delegates unavailable storage to an
``AgentUnavailabilityTracker`` instance, which implements a runtime-
checkable ``UnavailabilityStore`` ``Protocol`` defined in
``ralph/recovery/agent_unavailability_tracker.py``. The Protocol is the
single seam for swapping the current in-memory implementation for a
persistent one (sqlite, redis, file) without changing the controller or
any of the 25+ existing call sites.

Key contract points:

- **Scope is session by default.** The tracker exposes a ``.scope``
  property that returns ``"session"`` (the current in-memory default).
  A future persistent implementor overrides the constructor's ``scope``
  keyword to ``"persistent"`` and provides the I/O. Callers MUST NOT
  depend on the dict-shaped ``snapshot()`` output for any cross-session
  use; the snapshot format is legacy and may change when a persistent
  store is introduced.
- **Per-reason backoff is doubled on each consecutive unavailable mark**
  and capped at the reason's ``max_backoff_ms`` (see the table above).
  The cap is enforced by the tracker; the controller never bypasses it.
- **Public surface is additive.** The existing ``mark_unavailable``,
  ``is_available``, ``earliest_unavailable_wait_ms``, ``reset_backoff``,
  and ``snapshot`` methods keep their signatures. Test-only seams
  (``initial_entries`` and ``initial_timeouts`` on
  ``RecoveryControllerOptions``) are also public.

The Protocol is enforced at typecheck time by ``typing.Protocol`` and at
runtime by ``@runtime_checkable``; the
``test_unavailability_store_protocol_is_runtime_checkable`` test in
``tests/recovery/test_unavailability_tracker.py`` asserts
``isinstance(tracker, UnavailabilityStore) is True`` (and the test
passes under ``python -O``).

#### Controller injection and public surface

The `RecoveryController` constructor accepts a Protocol-typed dependency
via `RecoveryControllerOptions.unavailability_store: UnavailabilityStore |
None = None`. When the option is not provided, the controller constructs
a default `AgentUnavailabilityTracker`; when it IS provided, the
controller uses the caller's implementation as-is (the caller is
responsible for the store's initial state, so the legacy
`unavailable_timeouts` and `unavailability_entries` options are
ignored). The controller exposes the store via a public
`controller.unavailability_store` property — callers MUST consume it
through this property, NOT through the private
`_unavailability_tracker` attribute.

Two public methods wrap the store access so the run loop never reaches
through to the private store / clock fields:

| Method | Returns | Purpose |
|--------|---------|---------|
| `controller.waiting_state_payload(phase, agents)` | `list[tuple[str, int, int]]` of `(agent, attempt, cooldown_ms_remaining)` | The WAITING structured log payload (replaces the previous `ctx.controller._unavailability_tracker` / `tracker._clock` reach-through) |
| `controller.agents_now_available(phase, agents)` | `list[str]` of agent names | The RESUMED structured log payload (replaces the previous `tracker.is_available(phase, agent)` reach-through) |

A source-level guard test
(`tests/pipeline/test_run_loop_waiting_state_real_controller.py::test_run_loop_does_not_reach_through_private_tracker_attributes`)
fails CI if a future contributor reintroduces the
`ctx.controller._unavailability_tracker` or `tracker._clock` patterns in
`ralph/pipeline/run_loop.py`.

## See also

- ``ralph-workflow/ralph/agents/idle_watchdog/idle_watchdog.py`` —
  the watchdog implementation
- ``ralph-workflow/ralph/agents/invoke/_workspace_change_classifier.py`` —
  the classifier implementation
- ``ralph-workflow/ralph/timeout_defaults.py`` — the
  ``DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS`` constant
- ``ralph-workflow/tests/agents/test_workspace_change_classifier.py`` —
  classifier unit tests (47 tests)
- ``ralph-workflow/tests/agents/test_idle_watchdog_workspace_smart_filter.py`` —
  end-to-end AC #7 tests (12 tests)
