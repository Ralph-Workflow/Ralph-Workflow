# Developer Internals

This page documents Ralph Workflow internals for contributors who need to read the code that backs the policy-driven pipeline.

This section is for contributors and maintainers working on Ralph Workflow itself. These pages explain the runtime architecture and internal contracts behind the operator-facing product.

If you only need to run Ralph Workflow, start with [Configuration](configuration.md) and [Concepts](concepts.md) instead.

## What lives here

- [MCP Architecture](mcp-architecture.md) — server lifecycle, capability gates, and upstream proxying
- [Artifacts](artifacts.md) — typed handoffs and artifact storage contracts
- [Getting Started](getting-started.md) — run-spec authoring, prompt template shape, and proof-of-finish handoff
- [Streaming Blocks and Long-Content Display](#streaming-blocks-and-long-content-display) — output event structure and rendering behavior
- [Background-aware presentation](#background-aware-presentation) — surface-adaptive chroma, frequency tiers, and the per-frame salience budget
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
   `ralph --init`; create this optional per-repo override explicitly
   with `ralph --init-local-config` (or `ralph --generate-local-config`).
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

Agent CLI definitions (`[agents.*]`) load from
`~/.config/ralph-workflow-agents.toml`, merged BELOW the main config so
an `[agents.*]` table left in an existing `ralph-workflow.toml` keeps
its precedence.

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

### Responsive Status Bar

The persistent one-row Status Bar uses one responsive layout. At 120 columns
it shows attention, phase, liveness, elapsed time, run position, agent, and
working directory. At 80 columns the directory left-elides; at 60 it drops,
the phase abbreviates, and the agent remains; at the 40-column floor attention,
phase, liveness, elapsed time, and position remain. Position uses `cycle` for
the outer loop, `iter` for the inner loop, and `round` only for conflict
resolution. The watchdog alone supplies `STALLED`, and reader teardown clears
that assessment when its owning invocation ends; the injected monotonic clock
advances the liveness frame during quiet work at the
bounded live-refresh cadence. Width and height changes reflow the footer
immediately, including 12-row and temporarily below-floor terminals.

### Color Precedence

| Priority | Env var | Effect |
|----------|---------|--------|
| 1 | `NO_COLOR=<any>` | Disables all ANSI color output |
| 2 | `FORCE_COLOR=<any>` | Forces ANSI color on (even when not a TTY) |

`NO_COLOR` takes precedence over `FORCE_COLOR` per standard CLI conventions.

### Background-aware presentation

`DisplayContext` resolves the terminal background by precedence: the OSC 11 probe's
measurement wins whenever it succeeds; only when the probe cannot measure the surface
(non-tty, no reply, timeout) does an explicit `RALPH_TERMINAL_BG` override (`light`,
`dark`, or a `#RRGGBB` hex) apply, followed by the `COLORFGBG` dual-safe hint. A
malformed override falls through to the next tier rather than being threaded into the
solver unvalidated. Monokai Pro-derived role anchors are dynamically
solved against the measured or declared terminal surface to guarantee WCAG AA (4.5:1)
contrast. Code and diff previews use a complete surface derived from the measured background
when known, including source rows, gutters, and padding; an unknown background deliberately
falls back to transparency. Event identities, semantic event states, and the Status Bar use
the matching solved palette. An undetermined background uses the dual-safe palette targeting
the `[0.175, 0.1833]` luminance band rather than assuming a dark terminal. The text label
remains the identity carrier, so color is never required to understand an entry.

Role anchors are classified into four render-frequency tiers (constant body
text down to rare alarms), with chroma budget inversely proportional to how
often a role renders; structural chrome (panel borders, titles, rules) is
deliberately split from the semantic `info` state so the busiest on-screen
accent does not also mean "an info event just happened." A per-frame
salience allocator then spends a bounded accent budget across whichever
roles are competing to render in one frame, promoting a role that just
changed state and decaying one that has been steady, so a long quiet run
visibly drains of colour and a genuine event re-lights instantly.

See [Colour model: surface-adaptive chroma, frequency tiers, and the salience budget](display.html#colour-model-surface-adaptive-chroma-frequency-tiers-and-the-salience-budget)
for the palette solver's surface-adaptive chroma, the E-1 render-frequency tier table, and
the Section G per-frame salience allocator that governs how many accents may render at once.

### Display support contract

The reference display supports dark, light, and undetermined backgrounds;
truecolour, reduced-colour, and no-colour output; Unicode and ASCII glyphs;
and TTY, redirected, and CI destinations. Fully laid-out composition begins at
80 columns and degrades intentionally to the 40-column floor (including a
12-row terminal). Semantic and preview foregrounds are solved dynamically
against their resolved surface toward each role's own Monokai Pro reference
lightness -- reproducing Monokai Pro's own lightness structure (dark accents
stay darker, light accents stay lighter) instead of every role converging on
one plane. The 4.5:1 WCAG AA ratio is enforced as a **floor beneath** that
Monokai-derived target, not the target itself: a role only moves off its
reference lightness when accessibility demands it. The one deliberate
exception is the dimmed `comment` role, whose Monokai Pro reference
(`#727072` on `#2D2A2E`) measures only 2.88:1 -- below the floor -- so it is
deliberately lifted to 4.5:1 rather than reproduced bit-for-bit. `NO_COLOR` disables escapes; `FORCE_COLOR`
retains ANSI colour for render-capable redirected or CI capture. Motion is
reserved for a real TTY; redirected and CI output instead records durable state
transitions and elapsed-time heartbeats. Condensed output identifies its count,
byte size, and `.agent/raw/` recovery destination.


### Canonical activity presentation

Providers first write every original line to verbatim `.agent/raw/<id>.log`.
They then normalize each logical event once before the live display and the
ANSI-free `.agent/raw/<id>.rendered.log` record consume it. A phase header
carries readable phase words, cycle/iteration position, and identity once;
its indented event rows carry their timestamp, role, body, and only a
non-default severity. It never exposes parser channel names or repeats phase,
identity, time, or healthy severity on every event.

Tool calls and terminal results correlate by provider call ID, so a partial
update cannot look like a completed result and two identical-looking calls stay
distinct. Continuous reasoning is coalesced into one passage. Unknown or
malformed provider input uses the generic parser and retains the same hierarchy
rather than becoming a raw dump. The production replay corpus covers every
shipped agent plus the generic fallback, so this contract is verified at the
record and live-display seam rather than inferred from individual renderers.

All command output follows the same display boundary: run, commit plumbing,
policy check, diagnose, explain, init, cleanup, star,
contribute, smoke, and conflict resolution emit through
``ParallelDisplay.emit_*``. The smoke ``EXIT_CODE=N`` line remains the sole
machine-readable exception and is explicitly guarded.

Oversized content follows the shared size-based condenser on both human
surfaces. Its marker reports hidden amount, size, and the corresponding
verbatim raw-log destination. File operations retain an operation-and-path
header; recognized source, diff, shell, JSON, YAML, and traceback content is
syntax-highlighted only in the live display. `NO_COLOR`, ASCII fallback, and
rendered records retain the structural header and indentation without ANSI.

### Long-content summary configuration

The deterministic headline summary layer is default-on for completed
content above 4000 display cells. It keeps the entry readable before the shared
condenser accounts for omitted content. If no headline is available, it emits
`(no headline available)`; inline summaries are capped at 200 characters.

Set `RALPH_LONG_CONTENT_SUMMARY` to `0`, `false`, `no`, or `off` to disable the
deterministic headline. `RALPH_LONG_CONTENT_AI_SUMMARY` is a separate opt-in
for an `ai-summary:` line. The normalizer still produces one human entry per
logical event; these are entry details, never parser-channel rows.

### Display environment variables

| Variable | Effect |
|----------|--------|
| `NO_COLOR` | Disables color while retaining labels and hierarchy. |
| `FORCE_COLOR` | Forces terminal color where supported. |
| `COLUMNS` | Overrides terminal width with a positive integer. |
| `RALPH_FORCE_ASCII` | Uses ASCII glyph fallbacks. |
| `RALPH_LONG_CONTENT_SUMMARY` | Enables the deterministic headline by default. |
| `RALPH_LONG_CONTENT_AI_SUMMARY` | Opts into an `ai-summary:` detail. |

## Display presentation path

Raw agent output is parsed into ``AgentOutputLine`` values, normalized into
``AgentActivityEvent`` values, and emitted through
``ParallelDisplay.emit_parsed_event``. That one path drives the live activity
feed and the text-only ``.agent/raw/<id>.rendered.log`` record. Parsers may
omit optional data or retain malformed input as an unknown event; the presenter
still supplies timestamps, indentation, role markers, and the shared
size-based condensation rule rather than falling back to an unstructured dump.

The replay matrix in ``tests/display/test_universality_replay.py`` exercises
parser-native Claude, Claude Headless, Claude Interactive, Codex, OpenCode,
Pi, Cursor, AGY, Nanocoder, generic, and Gemini captures through this path.
It keeps fallback behavior and shipped agents on the same presentation contract.

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

## Runtime guarantees

For the operator-facing mental model, see [Concepts](concepts.md). This section
documents the implementation contracts that make that model reliable.

### Event loop and reducers

The runtime has two complementary structures:

- **Reducers** — pure functions of `(state, event) -> state`. They update the `PipelineState` in response to events (artifact submission, agent invocation result, watchdog signal). Reducers are testable in isolation (no I/O).
- **Effects** — imperative actions the runtime performs in response to the new state (spawn agent, write checkpoint, request recovery).

Effects are the integration points with the filesystem, agent subprocess, and MCP server. The split is intentional and protected by `audit_di_seam.py`. See `ralph/pipeline/reducers/` and `ralph/pipeline/effects/`.

The orchestrator is a **pure** `determine_next_effect(state) -> Effect` function: given the current `PipelineState`, it consults the policy and returns the next effect to execute. The effect is then handed to the appropriate handler in `ralph/pipeline/effects/` and `ralph/phases/`.

### Interrupt dispatch

`InterruptDispatcher` in `ralph.interrupt.dispatcher` coordinates orderly
shutdown. The first SIGINT routes through
`InterruptController.begin_interrupt(kill_label='invoke:')`, which sends
SIGTERM to the agent process group. A polling thread escalates to SIGKILL when
there is no CPU-time progress within `hard_kill_budget_s` (default 1.5s).
A second SIGINT calls `InterruptController.force_exit(bridge_pids=...)`,
terminates tracked processes, and exits with code 130. The CLI paths in
`ralph.cli.commands.run` and `ralph.cli.main` also invoke the dispatcher with
`block=True`, so this contract applies outside the pipeline loop as well.

### Verification-gate mechanics

The 60-second combined test budget is enforced by
`ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS = 60.0`. `run_verify()` tracks
elapsed time with `time.monotonic()` across `_BUDGET_TRACKED_STEPS`; the
`_KNOWN_TEST_STEP_LABELS` set keeps every declared test step covered by that
budget. Import-time `RuntimeError` guards pin the budget and its tracking
invariants, including under `python -O`.

The verification gate also runs the `ralph/testing/audit_*.py` invariant set,
including `audit_lint_bypass.py`, `audit_typecheck_bypass.py`,
`audit_test_policy.py`, `audit_mcp_timeout.py`,
`audit_resource_lifecycle.py`, and
`audit_artifact_submission_canonical_path.py`. These audits detect weakened
checks and unsafe lifecycle behavior; their documented allowlists are the only
approved exception path. See [Concepts](concepts.md#verification-model) for
why operators should treat `make verify` as non-bypassable.

## Pipeline lifecycle (high level)

A typical Ralph Workflow run follows this shape: planning → development → review → commit → recovery. Policy-driven orchestration happens via `ralph/pipeline/orchestrator.py` and `ralph/pipeline/reducer.py`. Each phase has one job: prepare a prompt, invoke the agent, validate the artifact, advance the state machine. The reducer records the artifact and routes to the next effect based on policy. The recovery layer catches watchdog and timeout fires and decides retry vs terminal.

## Related pages

- [Configuration](configuration.md) — operator-facing configuration reference
- [Python API Reference](modules.rst) — autodoc for the `ralph.*` package
- [Release & Versioning](versioning.md) — release and publishing policy
