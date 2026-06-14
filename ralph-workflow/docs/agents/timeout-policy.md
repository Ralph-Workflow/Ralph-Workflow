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

## Absolute ceilings are unaffected

The ``SESSION_CEILING_EXCEEDED`` and ``CHILDREN_PERSIST_TOO_LONG``
ceilings are checked BEFORE the activity-deferral hook in
``IdleWatchdog.evaluate()`` and remain absolute. A productive
session that is busy on a non-stdout channel cannot defeat either
ceiling. The per-kind weight policy is layered on top of the
existing activity-aware verdict and does not extend any
ceiling.

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
