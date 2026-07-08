# Developer Internals

This page documents Ralph Workflow internals for contributors who need to read the code that backs the policy-driven pipeline.

This section is for contributors and maintainers working on Ralph Workflow itself. These pages explain the runtime architecture and internal contracts behind the operator-facing product.

If you only need to run Ralph Workflow, start with [Configuration](configuration.md) and [Concepts](concepts.md) instead.

## What lives here

- [MCP Architecture](mcp-architecture.md) — server lifecycle, capability gates, and upstream proxying
- [Artifacts](artifacts.md) — typed handoffs and artifact storage contracts
- [Getting Started](getting-started.md) — run-spec authoring, prompt template shape, and proof-of-finish handoff
- [Streaming Blocks and Long-Content Display](#streaming-blocks-and-long-content-display) — output event structure and rendering behavior
- [Supervising API](#supervising-api) — trackable instance model for orchestration use cases

## Configuration-loading internals

This page documents how Ralph Workflow loads and merges its
configuration at process startup. The contract is: deterministic
merge order, hard failure on bad config, and clear precedence so a
contributor can predict what a change will do.

### Layered TOML configuration

[`ralph.config.loader`](../../modules.html#ralph.config.loader) loads
`ralph-workflow.toml` through a four-layer merge (lowest to highest
priority):

1. **Embedded defaults.** Pydantic field defaults on
   `ralph.config.models.UnifiedConfig` and its sub-models. These are
   the fallbacks shipped with the package.
2. **User-global config.** `~/.config/ralph-workflow.toml`. Settings
   here apply to every project the user runs Ralph Workflow against.
3. **Project-local config.** `.agent/ralph-workflow.toml` inside the
   active workspace. The user-global file is laid down by
   `ralph --init`; the project-local file is the per-repo override
   that ships with version control.
4. **CLI flag overrides.** Applied last, just before Pydantic
   validation; CLI wins on conflict.

The merge uses [`deep_merge`](../../modules.html#ralph.config.loader.deep_merge)
which is recursive: nested tables merge key-by-key so a project-local
override of a single sub-section never clobbers the unrelated
sections above it.

### Policy defaults

[`ralph.policy.loader`](../../modules.html#ralph.policy.loader) loads
the policy tables (`agents.toml`, `pipeline.toml`, `artifacts.toml`,
`mcp.toml`) from the same `.agent/` directory the layered config uses
for `ralph-workflow.toml`, with the same fallback to bundled
defaults under
[`ralph/policy/defaults/`](../../modules.html#ralph.policy.defaults).

User-global policy overrides prefer the **branded** filenames:

- `ralph-workflow-pipeline.toml`
- `ralph-workflow-artifacts.toml`

The legacy unprefixed names (`pipeline.toml`, `artifacts.toml`)
remain accepted for backward compatibility.

All loading goes through Pydantic validation via
[`ralph.policy.validation`](../../modules.html#ralph.policy.validation).
Malformed config surfaces as a `PolicyValidationError` with
field-level detail so a contributor can point at the exact key that
needs to change.

### How defaults are declared and overridden

Defaults are declared in two places: Pydantic field defaults on `UnifiedConfig` / `GeneralConfig` / `AgentConfig` / the relevant sub-models in `ralph/config/models.py` (the lowest-priority defaults), and bundled TOML defaults under `ralph/policy/defaults/` (the lowest-priority policy defaults that also serve as the schema sample new users see).

To override a default for a single field: per-user (add the key to `~/.config/ralph-workflow.toml` or the relevant TOML in the user policy directory), per-project (add the key to `.agent/ralph-workflow.toml` under version control), or per-run (pass the corresponding CLI flag — CLI wins on conflict).

To add a brand-new default for a new feature, set the field default on the Pydantic model in `ralph/config/models.py` and also update the bundled user-global template at `ralph/policy/defaults/ralph-workflow.toml` so new users see the documented default in their first `--init` output.

### What the loader guarantees

- **Deterministic merge order.** Two runs with the same input files
  produce byte-identical merged configs.
- **Hard failure on bad config.** A malformed TOML file, a value
  that fails Pydantic validation, or a contract inconsistency (for
  example, a drain reference that does not exist) aborts the run
  before any side effect. The error carries enough context for the
  contributor to fix the field, not just the file path.
- **Workspace propagation.** Linked worktrees inherit defaults from
  the main worktree unless the linked worktree has its own override;
  this is resolved by
  [`ralph.workspace.scope.WorkspaceScope`](../../modules.html#ralph.workspace.scope.WorkspaceScope)
  at startup.
- **Same-layer isolation.** The merge does NOT cross-contaminate
  unrelated sub-sections of the config; a project-local override of
  one section leaves every other section untouched.

## Streaming Blocks and Long-Content Display

Ralph Workflow emits a structured, line-oriented transcript to stdout during a run. Every line has a fixed format that can be machine-parsed or read directly in a terminal.

### Display Architecture

`DisplayContext` (from `ralph.display`) is the single place where Ralph Workflow decides how output should render: console, theme, terminal width, color policy, display mode, and adaptive character limits. Every renderer function requires a `display_context: DisplayContext` argument — no renderer constructs its own `rich.Console`. Callers create a `DisplayContext` with `make_display_context()` before invoking any renderer:

```python
from ralph.display import make_display_context

ctx = make_display_context()          # uses terminal width, NO_COLOR, etc.
show_phase_start("planning", display_context=ctx)
```

### Width Precedence

| Priority | Source | Effect |
|----------|--------|--------|
| 1 | `force_width` argument to `make_display_context()` | Overrides all width detection |
| 2 | `COLUMNS=<N>` env var (positive int) | Overrides console.width |
| 3 | `console.width` (actual terminal width) | Default fallback |

### Display mode (single default)

Ralph Workflow exposes exactly ONE display mode: ``default``. There is no width-based dispatch and no per-mode limits table. The persistent bottom Status Bar renders all applicable fields (working directory, active phase, applicable outer development iteration, applicable inner analysis iteration) at every terminal width where they fit. At widths >= 40 cols the canonical ``Dev N/cap`` / ``Analysis N/cap`` labels render in full and only path middle-truncation and phase tail-truncation budgets adapt to width. Below 40 cols the implementation may degrade to compact (``D1/3`` / ``A2/5``) or minimal (``1/3`` / ``2/5``) forms to fit. Below 14 cols the iteration segments drop one at a time (outer_dev first, then inner_analysis, then both) so the bar never overflows the working area; phase and path remain visible at every applicable width.

### Color Precedence

| Priority | Env var | Effect |
|----------|---------|--------|
| 1 | `NO_COLOR=<any>` | Disables all ANSI color output |
| 2 | `FORCE_COLOR=<any>` | Forces ANSI color on (even when not a TTY) |

`NO_COLOR` takes precedence over `FORCE_COLOR` per standard CLI conventions.

### Line Format

```
<ISO-TS> <LEVEL> <CAT> [<tag>][<unit>] <content>
```

| Field | Example | Notes |
|-------|---------|-------|
| `<ISO-TS>` | `2026-04-25T12:00:00Z` | ISO-8601 timestamp |
| `<LEVEL>` | `INFO` | One of the five levels below |
| `<CAT>` | `META` | `META` or `CONT` |
| `[<tag>]` | `[phase]` | Sub-operation tag (see table below) |
| `[<unit>]` | `[unit-1]` | Work unit ID in parallel runs; omitted otherwise |
| `<content>` | `Planning started` | Human-readable message |

### Levels

| Level | Meaning |
|-------|---------|
| `INFO` | Routine update or progress |
| `SUCCESS` | Phase or pipeline completed successfully |
| `WARN` | Non-fatal issue or degraded state |
| `ERROR` | Fatal error or malformed input |
| `MILESTONE` | Major phase transition (planning, development, commit) |

Verbosity controls which levels are shown. Use `--quiet` to suppress everything except `ERROR`, or `--debug` to show all levels.

### Categories

| Category | Meaning |
|----------|---------|
| `META` | Workflow metadata: phase transitions, plans, activity, worker events, run summary |
| `CONT` | Agent-produced content: text, thinking blocks, tool calls, tool results, errors |

### Streaming Blocks and Long-Content Display

Long agent outputs (for example code, plans, or long prose) are emitted as streaming blocks bounded by `content-start` / `content-end` tags. Within a block:

- `content-continue` lines carry the raw streamed chunks.
- `content-checkpoint` lines appear at configurable intervals to allow progressive display without buffering the entire block.

Ralph Workflow also applies a deterministic headline summary layer when a completed block exceeds **4000** display cells. That layer is **enabled by default**. It appears before the condensed output so operators get a stable summary instead of scrolling through a giant block.

If no clean headline can be extracted, Ralph Workflow shows **`(no headline available)`**. Inline summary lines are capped at **200** characters, and streaming end-line summaries are capped at **120** characters.

Disable the deterministic headline layer with `RALPH_LONG_CONTENT_SUMMARY` values `0`, `false`, `no`, or `off`. There is no special opt-in value because the feature is already on by default.

When a block ends, Ralph Workflow may append summary lines depending on configuration:

- `⇳ summary:` — static truncation summary (always present for very long blocks)
- `⇳ preview:` — first *N* characters of the block content
- `⇳ ai-summary:` — LLM-generated one-line summary (requires `RALPH_LONG_CONTENT_AI_SUMMARY`)

The optional AI-generated layer is separate from the deterministic headline layer. Use `RALPH_LONG_CONTENT_AI_SUMMARY` only when you want the additional `↳ ai-summary:` style output.

### Environment Variables (display)

| Variable | Default | Effect |
|----------|---------|--------|
| `RALPH_STREAMING_DEDUP` | `1` | Deduplicate identical consecutive streaming chunks |
| `RALPH_STREAMING_CHECKPOINTS` | `0` | Emit `content-checkpoint` lines during streaming |
| `RALPH_LONG_CONTENT_SUMMARY` | `1` | Append `⇳ summary:` after very long content blocks |
| `RALPH_LONG_CONTENT_AI_SUMMARY` | `0` | Append `⇳ ai-summary:` (requires LLM round-trip) |
| `NO_COLOR` | unset | Disable all ANSI colour output (any value) |
| `FORCE_COLOR` | unset | Force ANSI colour even when stdout is not a TTY (any value) |
| `COLUMNS` | unset | Override terminal width; positive integer |

## Supervising API

The supervising API exposes a stable, read-only view of a running workflow instance for orchestration and monitoring. Use it to inspect the stable instance identity, the optional runtime run identity, the lifecycle status, the current pipeline stage, and recent operational activity.

### InstanceStatus

`InstanceStatus` describes the observable lifecycle state of a workflow instance:

- `not_started` — no snapshot has been received yet; the tracker holds a stable pre-start identity
- `active` — the pipeline is currently executing a stage
- `waiting` — the pipeline is active but waiting on child work
- `completed` — the instance reached a successful terminal state
- `failed` — the instance failed or was interrupted

### WorkflowInstanceView

`WorkflowInstanceView` is the immutable snapshot surface for orchestration.

Fields:

- `instance_id`: Stable orchestration identity assigned at `WorkflowInstanceTracker` construction. This is the primary identity an orchestrator uses to track this instance. Unlike ``run_id``, it is fixed before the workflow starts and never changes.
- `run_id`: Optional runtime identifier copied from the live pipeline snapshot. This is separate from ``instance_id`` so that a supervising orchestrator can track the same instance across restarts or reconnects without confusion. It is ``None`` before startup and when the underlying system does not assign one.
- `lifecycle_status`: One of `InstanceStatus`
- `current_stage`: Active pipeline stage name, or ``None``
- `recent_activity`: Recent operational output, ordered oldest to newest

### WorkflowInstanceTracker

``WorkflowInstanceTracker`` owns the stable orchestration identity and updates the immutable view from live snapshots.

#### Constructor

```python
WorkflowInstanceTracker(instance_id: str)
```

Initialize the tracker with a stable ``instance_id`` assigned by the orchestrator. The tracker starts at ``InstanceStatus.NOT_STARTED`` with no ``run_id``, ``current_stage=None``, and empty ``recent_activity``.

#### Properties

- ``view``: Returns the latest immutable `WorkflowInstanceView`. Always reflects the most recent snapshot while preserving the stable ``instance_id``.

#### Methods

- ``update_from_snapshot(snapshot: PipelineSnapshot) -> WorkflowInstanceView``: Updates the view from a live pipeline snapshot. Preserves the stable ``instance_id`` assigned at construction and copies ``snapshot.run_id`` into the view's ``run_id`` field. Returns the updated view.

### Wiring

Connect the supervising view to a live workflow through ``PipelineSubscriber.__init__(..., on_snapshot=...)``:

```python
from ralph.supervising import WorkflowInstanceTracker

tracker = WorkflowInstanceTracker(instance_id="work-42")
subscriber = PipelineSubscriber(
    ...,
    on_snapshot=tracker.update_from_snapshot,
)
# Inspect current state:
view = tracker.view
```

The ``on_snapshot`` callback is invoked after every ``notify()``, ``record_waiting_status()``, or ``record_activity()`` call with the latest snapshot. The tracker's ``view`` property always returns the most recent immutable snapshot while keeping the stable ``instance_id`` from construction time.

### Direct Snapshot Projection

For cases where you only need to project a snapshot without maintaining a tracker:

```python
from ralph.supervising import instance_view_from_snapshot

view = instance_view_from_snapshot(snapshot)
```

In this form, ``view.instance_id`` is taken directly from ``snapshot.run_id``. This is suitable when the runtime identity is the orchestrator-facing identity and ``snapshot.run_id`` is not ``None``.

If ``snapshot.run_id`` is ``None`` and no override is provided, a ``ValueError`` is raised because the supervising contract requires a stable orchestrator-facing identity. For tracker-based supervision, use ``WorkflowInstanceTracker.update_from_snapshot`` instead.

### Stage Semantics

``current_stage`` is ``None`` in these situations: ``lifecycle_status`` is ``not_started`` (before any snapshot), ``completed`` or ``failed`` (terminal states), or the active phase is the ``__unset__`` sentinel. This distinction is intentional: ``None`` means "no active stage" and is not an unknown state. A supervising orchestrator can use ``lifecycle_status`` to determine whether the instance is still running, and ``current_stage=None`` with ``lifecycle_status=active`` correctly indicates an active instance that has not yet entered a named pipeline stage.

### Out of scope

This page does not define transport, storage, authentication, scheduling, fleet orchestration, or protocol details.

## Event loop and reducers

The runtime has two complementary structures:

- **Reducers** — pure functions of `(state, event) -> state`. They update the `PipelineState` in response to events (artifact submission, agent invocation result, watchdog signal). Reducers are testable in isolation (no I/O).
- **Effects** — imperative actions the runtime performs in response to the new state (spawn agent, write checkpoint, request recovery).

Effects are the integration points with the filesystem, agent subprocess, and MCP server. The split is intentional and protected by `audit_di_seam.py`. See `ralph/pipeline/reducers/` and `ralph/pipeline/effects/`.

The orchestrator is a **pure** `determine_next_effect(state) -> Effect` function: given the current `PipelineState`, it consults the policy and returns the next effect to execute. The effect is then handed to the appropriate handler in `ralph/pipeline/effects/` and `ralph/phases/`.

## Pipeline lifecycle (high level)

A typical Ralph Workflow run follows this shape: planning → development → review → commit → recovery. Policy-driven orchestration happens via `ralph/pipeline/orchestrator.py` and `ralph/pipeline/reducer.py`. Each phase has one job: prepare a prompt, invoke the agent, validate the artifact, advance the state machine. The reducer records the artifact and routes to the next effect based on policy. The recovery layer catches watchdog and timeout fires and decides retry vs terminal.

## Related pages

- [Configuration](configuration.md) — operator-facing configuration reference
- [Python API Reference](modules.rst) — autodoc for the `ralph.*` package
- [Release & Versioning](versioning.md) — release and publishing policy
