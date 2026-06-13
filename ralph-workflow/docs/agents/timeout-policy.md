# Idle Watchdog Timeout Policy

The Ralph Workflow idle watchdog decides whether an agent is stuck
from **all observable evidence of work**, not just stdout output.
The workspace channel (file changes detected by
``WorkspaceMonitor``) is class-aware: each file change is classified
into one of five ``WorkspaceChangeKind`` values and weighted
BINARILY (``0.0`` = drop, ``1.0`` = full activity).

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

## Per-kind breakdown in the fire diagnostic

Every ``NO_OUTPUT_DEADLINE`` fire log now embeds the per-channel
evidence summary in the loguru ``extra=`` dict, including the
per-kind ``kind_breakdown`` for the workspace channel:

```
extra = {
    "evidence_summary": [
        {"channel": "stdout",    "last_at": ..., "age_seconds": ..., "counter": ...},
        {"channel": "mcp_tool",  "last_at": ..., "age_seconds": ..., "counter": ...},
        {"channel": "subagent",  "last_at": ..., "age_seconds": ..., "counter": ...},
        {"channel": "workspace", "last_at": ..., "age_seconds": ..., "counter": ...,
         "kind_breakdown": {"source": 5, "log": 0}},
    ],
    "active_channel": "none",
    "fire_reason": "no_output_deadline",
}
```

The post-mortem reader can see exactly which kinds were most
active at the moment of the fire, e.g. ``{source: 5, log: 0}``
indicates that the agent was making source-code changes but
emitting no stdout (the conservative default policy would still
defer the verdict on those source changes — this is the expected
behavior).

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
  classifier unit tests (38 tests)
- ``ralph-workflow/tests/agents/test_idle_watchdog_workspace_smart_filter.py`` —
  end-to-end AC #7 tests (12 tests)
