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
- [Configuration failure transport and rebase self-recovery](#configuration-failure-transport-and-rebase-self-recovery) — typed failure transport, budget protection, and fingerprint-based retry

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
roles are competing to render in one frame, with the per-role "underlying
state changed" signal driven by a **state-token** comparison in
``ParallelDisplay._apply_salience`` (each call-site's token fields are
published in ``SALIENCE_STATE_TOKEN_FIELDS``): a role that just changed
its own token is promoted, a role that has been steady for the decay window
decays one rung, so a long quiet run visibly drains of colour and a
genuine event re-lights instantly. The keyword-only ``state_token``
parameter on ``_apply_salience`` carries the per-role state: the
``_build_line`` call site bids the token fields ``("line", "level")``,
and the ``_activity_text`` call site bids the token fields
``("activity", "kind", "unit_id")`` — the leading element namespaces the
two call sites so a chrome-row `info` and an activity-row `info` are
not compared as the same underlying state.

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
byte size, and `.agent/raw/<id>.overflow.log` recovery destination.


### Canonical activity presentation

Providers first write each original line to one of two sibling captures.
The verbatim `.agent/raw/<id>.log` holds agent output plus, for interactive
PTY transports, the reader's own session and turn-boundary markers; bodies
the display condenses out of the live view go to `.agent/raw/<id>.overflow.log`
instead, so the verbatim capture stays parseable as the transcript that
Ralph Workflow grades for corruption. The identity `<id>` distinguishes agents
that share an executable — headless Claude from interactive Claude, and each
`ccs/<alias>` from every other — so two agents can never write one capture and
grade each other's bytes. They then normalize each logical event once before the live display
and the ANSI-free `.agent/raw/<id>.rendered.log` record consume it. A phase header
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

The replay matrix in ``tests/test_parsers_1.py`` exercises
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

## Configuration failure transport and rebase self-recovery

During auto-integrate rebase conflict resolution, conflict resolution executes within the target workspace's configuration and policy context. If the target workspace contains a malformed configuration file, Ralph Workflow transports the failure through typed error channels, protects the conflict attempt budget, and uses content fingerprinting to gate automatic retries on operator repairs.

### Typed configuration failure pipeline

Target worktree activation is isolated via `workspace_context(target_root)` in `ralph.workspace.context`. When loading configuration and policies for the target worktree:

1. **Error wrapping**: Syntactic defects raise `ConfigTomlError`, while schema validation defects trigger a `SystemExit` wrapping `pydantic.ValidationError`. `workspace_context` intercepts both exceptions and wraps them into a typed `WorkspaceContextError(file=file_path, reason=..., fix=...)`. The error attributes the failure to the specific offending file (such as `.agent/ralph-workflow.toml` or `.agent/agents.toml`) and provides an actionable remediation command: `fix <path> then run ralph --check-config`.
2. **Resolver transport**: The rebase stop resolver in `ralph.pipeline.auto_integrate_agent` catches `WorkspaceContextError`. Rather than crashing or classifying the failure as an unresolved git conflict, it sets:
   - `session.charge_conflict_budget = False`
   - `session.terminal_reason = ResolutionTerminationReason.CONFIG_FAILURE`
   - `session.last_failure_cause = ResolutionFailureCause.CONFIG_FAILURE`
   - `session.exhaustion_reason = f"fix {exc.file} then run ralph --check-config"`
   The resolver returns a `ResolutionOutcome` marked with `failure_cause=ResolutionFailureCause.CONFIG_FAILURE`.
3. **Engine-level propagation**: In `ralph.pipeline._auto_integrate_rebase_resolve`, `carried_cause` evaluates `session.last_failure_cause` and `session.terminal_reason`. When either matches `ResolutionFailureCause.CONFIG_FAILURE` or `ResolutionTerminationReason.CONFIG_FAILURE`, `RebaseResolutionResult(False, carried_reason, carried_cause)` propagates the typed failure out of the rebase resolution runner.

### Conflict budget protection

Configuration defects are environment and setup errors, not failed LLM resolution attempts. To ensure that an operator repairing a syntax error does not return to an exhausted conflict budget:

- **Resolver not spent**: `ResolutionTerminationReason.CONFIG_FAILURE` is registered in `RESOLVER_NOT_SPENT_TERMINATION_REASONS`. Because `session.charge_conflict_budget = False`, the resolver chain is not recorded as exhausted.
- **Counter protection across both seams**:
  - In `ralph.pipeline.run_loop` (`_handle_pending_retry`), rebases with `last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE` or `ResolutionFailureCause.CONTEXT_FAILURE` transition to `OperationDisplayState.WAITING` without incrementing the `consecutive_conflicts` counter.
  - In `ralph.pipeline.auto_integrate_budget_seam` (`apply_completed_attempt_budget`), both `ResolutionFailureCause.CONFIG_FAILURE` and `ResolutionFailureCause.CONTEXT_FAILURE` are handled as non-repository failures via `charge_non_repository_failure`, keeping `consecutive_conflicts` unchanged and preserving repository conflict identity unassigned.
  - Optional user portfolio configuration at `docs/ralph-workflow-policy/policy-portfolio.toml` gracefully falls back to built-in defaults with deduplicated warnings on syntax or schema errors, while `ralph --check-config` strictly reports any malformed or schema-invalid portfolio file as an error.
- **Repair attempts uncharged**: In `ralph.pipeline._integration_diagnostic`, diagnostic counters and `record.merge_commit_repair_attempts` are not charged for configuration failures, preserving the agent's resolution budget for genuine Git merge conflicts.
- **Operator visibility**: The Status Bar and activity feed display the specific failure cause and required fix:
  `integration conflict (CONFIG_FAILURE: fix <path> then run ralph --check-config) — resolution required`.

### Fingerprint-based retry gating in the waiting loop

When a rebase pauses on a configuration failure, Ralph Workflow enters a waiting loop to give the operator an opportunity to repair the defective file without aborting the workflow run. To prevent busy-spinning against unchanged defective files:

1. **Content fingerprinting**: `ralph.pipeline.config_file_fingerprint` computes immutable `ConfigFileFingerprint(path, content_hash)` tuples using SHA-256 digests via `compute_config_file_fingerprint(path)`:
   - Valid, readable files produce a 64-character hexadecimal SHA-256 digest.
   - Missing paths, directories, or `None` paths yield `None`.
   - Files unreadable due to filesystem permissions or OS errors return distinct descriptors (`unreadable:<ErrorClass>`), allowing permission fixes to register as valid state transitions.
   - `extract_config_path_from_reason(reason)` parses the target file path directly from the structured error reason string.
2. **Distinct-fingerprint retry gating**: `ralph.pipeline.config_file_fingerprint_watcher.ConfigFileFingerprintWatcher` (and the pure helper `should_retry_config_failure(path, last_fingerprint)`) tracks attempted fingerprints:
   - The initial failure establishes a baseline fingerprint for the defective path.
   - While the file remains unchanged on disk (`current_hash == last_hash`), `should_retry()` returns `False`. The run loop sleeps and preserves the `WAITING` state, generating zero CPU churn or repeated resolver invocations.
   - When the operator edits and repairs the file, the file's content hash changes. `should_retry()` detects the distinct fingerprint, marks it as attempted, and allows exactly one paused rebase continuation attempt.
   - If the repair succeeds, rebase continuation proceeds. If the file is still invalid (or introduces a new error), a new distinct fingerprint is recorded, awaiting the next file modification.

### Resolution candidate exhaustion versus tool surface death

When auto-integrate conflict resolution runs through its configured resolver agent chain:

1. **Candidate classification**:
   - `ResolutionTerminationReason.TOOL_SURFACE_DEAD` is used strictly when an agent candidate was actively launched and its tool surface died, failed, or was terminated.
   - When no candidate agent is eligible and none has a finite waiting cooldown (all candidates are spent or demoted), the termination reason is `ResolutionTerminationReason.CANDIDATE_EXHAUSTED`.
   - When an agent is currently on cooldown with a finite wait remaining, `ResolutionTerminationReason.CANDIDATE_UNAVAILABLE` indicates a temporary wait rather than permanent exhaustion.
2. **Failure ledger and escalation ladder**:
   - Unlike `TOOL_SURFACE_DEAD` and `CANDIDATE_UNAVAILABLE` (which belong to `RESOLVER_NOT_SPENT_TERMINATION_REASONS`), `CANDIDATE_EXHAUSTED` records exhaustion in the `AgentFailureLedger`. This permits the recovery escalation ladder to progress rather than looping indefinitely.
   - `RecoveryController` exposes `all_exhausted` and `agents_now_available` to differentiate between chains that have live options remaining and chains that are completely spent.
3. **Operator hand-off message and commands**:
   - When candidate agents are exhausted or conflicts are out of reach, the engine constructs an actionable operator message listing each spent agent with its specific failure reason, the conflicted paths, and the exact manual command needed to continue:
     `all N resolver agents are spent/unavailable (<reasons>); conflict on <paths> needs operator action: git add -- <paths>`
   - Generated via `operator_command_for_paths(paths)`, the command properly escapes and formats pathnames for direct shell execution.
   - On the merge seam (`ralph.pipeline.auto_integrate_resolve`), an exhausted resolution returns `MergeResult(outcome=MERGE_COMMIT_PENDING, failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT)` with this operator hand-off message, eliminating misleading "conflict resolution incomplete; progress retained" summaries.

### Durable continuation retry gate across processes

Continuation attempts often run in fresh supervisor processes where in-memory state is discarded. To prevent hot retry loops across process invocations:

1. **Durable gate key in `IntegrationRecord`**:
   - The engine computes a composite retry gate key `(stop_identity, sorted_eligible_agents, config_fingerprint)` and persists it inside `IntegrationRecord` in `.agent/auto_integrate_in_progress.json`.
   - When a subsequent process starts, `_integration_continuation` re-reads the durable record.
2. **Gate evaluation**:
   - If the stop identity, eligible resolver agents, and configuration fingerprints are unchanged, continuation resolution is refused. Exactly one actionable `WAITING` status line is emitted instead of repeated `CRITICAL` log floods and busy process respawns.
   - Resolution is re-entered only when the gate key changes (such as when conflict identity, eligible agents, or configuration change, or when backoff is reset).
   - Pre-existing integration records lacking the gate key field deserialize safely and operate in ungated mode for backward compatibility.
3. **Forced operator retry via `ralph --retry`**:
   - Operators can bypass the durable retry gate at any time using `ralph --retry` (or `ralph --retry-integration`).
   - The CLI passes `force_retry=True` to `recover_incomplete_integration`, which constructs or forwards launch-capable conflict/rebase resolver callbacks and an active `RecoveryController` from the pipeline context.
   - Forced retry executes exactly one resolution attempt; if resolution cannot complete, the new gate key is recorded back to disk so subsequent regular runs remain gated.

### Gitlink conflict resolution and escalation on sight

Submodule gitlink conflicts (`160000` mode in git index) occur when two branches advance a submodule pointer independently:

1. **Deterministic submodule resolution**:
   - Before falling back to manual escalation or invoking resolver agents, `try_deterministic_resolution` inspects the conflicted submodule gitlink objects.
   - If the submodule directory is uninitialized or missing, the engine executes `git submodule update --init -- <path>`.
   - If gitlink commit objects are missing locally, `git -C <path> fetch` is attempted to fetch upstream objects.
   - If the local and incoming submodule commits have an ancestor/descendant relationship, the descendant commit is automatically staged, resolving the conflict deterministically without agent intervention.
2. **Immediate escalation on sight**:
   - If gitlink commits have diverged (neither commit is an ancestor of the other) or objects cannot be fetched, resolving the gitlink is beyond the capability of code-editing agents.
   - `classify_unmerged_conflicts` classifies the gitlink as `ConflictSight.OUT_OF_REACH`.
   - The engine immediately escalates on sight with `ResolutionTerminationReason.OUT_OF_REACH` and `ResolutionFailureCause.AGENT_FAILURE`, naming the path and exact operator command `git add -- <path>` (`"OUT_OF_REACH: gitlink conflict on <path> needs operator action: git add -- <path>"`), without spending candidate agent attempts.

### Real-configuration isolation guard and audit trail

To guarantee that the test suite never mutates or leaks state from real user configuration files in `~/.config` or `$XDG_CONFIG_HOME`:

1. **Snapshot and Diff Guard**: `tests/conftest.py` implements session-scoped snapshotting (`_scan_guarded_config_files`) of any paths matching `ralph-workflow*` or `.ralph-workflow*` in guarded configuration directories (`_resolve_guarded_config_dirs`). `_diff_config_snapshots` detects file creation, deletion, content changes (SHA-256 hash), modification time changes (`mtime_ns`), and permission changes (`st_mode`).
2. **Audit Hook for Read Interception**: A Python audit hook registered with `sys.addaudithook` monitors file `open` events. Any read of real configuration paths matching guarded patterns is intercepted and attributed to the offending test node (`_CURRENT_TEST_NODE`).
3. **Per-Test Sandbox Redirection**: `_apply_isolate_process_home` redirects `HOME`, `XDG_CONFIG_HOME`, `GLOBAL_CONFIG_PATH`, `GLOBAL_AGENTS_CONFIG_PATH`, `DEFAULT_STATE_PATH`, and `_AGY_CLI_LOG_PATH` into isolated per-test sandboxes under `tmp_path_factory`.
4. **Audit Trail of Test Files**: All test files referencing user home paths, global config constants, or path expansion have been audited to ensure complete isolation:
   - `tests/agents/invoke/test_claude_interactive_real_capture_replay.py`: Redirects `Path.home()` via fixture monkeypatch.
   - `tests/agents/invoke/test_subagent_transcript_tail.py`: Redirects `Path.home()` via fixture monkeypatch.
   - `tests/config/test_conflict_resolution_config.py`: Explicitly overrides global paths with `tmp_path` fixtures.
   - `tests/conftest.py`: Establishes session snapshots, audit hooks, and per-test home isolation.
   - `tests/mcp/test_e25_candidate_survives_agent_tmp_deletion.py`: Uses sandboxed cache directories under isolated home.
   - `tests/test_agent_config_subagent_default.py`: Monkeypatches `GLOBAL_CONFIG_PATH` and `GLOBAL_AGENTS_CONFIG_PATH` to `tmp_path`.
   - `tests/test_agy_empty_output_failure.py`: Uses path tails instead of live `Path.home()` lookups.
   - `tests/test_agy_live_regression.py`: Derives isolated sandbox home copying only required `.gemini` tokens.
   - `tests/test_config_loader.py`: Monkeypatches global config constants to `tmp_path`.
   - `tests/test_config_propagation.py`: Monkeypatches global config constants to `tmp_path`.
   - `tests/test_pro_support_workspace.py`: Exercises `expanduser` against sandboxed `HOME`.
   - `tests/test_provider_fallback_warning.py`: Monkeypatches global config constants to `tmp_path`.
   - `tests/test_real_config_guard.py`: Meta-tests verifying detection of file creation, modification, deletion, chmod, and reads.
   - `tests/test_skills_agent_paths.py`: Asserts dynamic re-resolution of `Path.home()`.
   - `tests/test_skills_auto_update_no_repair.py`: Validates that canonical paths are sandboxed.
   - `tests/test_skills_installer_project.py`: Uses `tmp_path` for all home directories.

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
