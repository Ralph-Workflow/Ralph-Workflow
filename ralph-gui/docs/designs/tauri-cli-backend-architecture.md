# Ralph GUI - Tauri/CLI Backend Architecture

Terminology in this document follows `ralph-gui/docs/glossary.md`.

Language rules used here:

- `Session` is the user-facing launched unit in the GUI.
- `Run` is the underlying Ralph CLI execution.
- One session currently maps to one run.

## Purpose

This document defines the target architecture for integrating Tauri with the
Ralph CLI so the GUI can rely on the CLI as its execution backend instead of
re-implementing Ralph behavior inside the desktop app.

The guiding concept for this design is:

- One GUI session launch maps to one Ralph CLI process.
- Closing a session means the GUI initiates CLI cleanup for that session.
- Tauri is the desktop-side orchestration layer, not the source of workflow
  truth.
- The CLI remains the system of record for pipeline state, logs, checkpoints,
  and lifecycle transitions.

This architecture uses the existing acceptance criteria as the product context,
 especially:

- `AC-4.4` Session Launch
- `AC-5.1` to `AC-5.8` Run Monitoring
- `AC-1.2` Workspace Lifecycle
- `AC-11.1` Notifications
- `AC-15.2` Reliability
- `AC-15.4` Security

## Problem Statement

Today the GUI already depends on Tauri commands to talk to Ralph CLI processes
for launch, resume, inspection, config, and log-related flows. This document is
not proposing a new Tauri-to-CLI direction; it is formalizing the architecture
that already exists and defining how it should be completed so the ownership
boundaries and lifecycle rules are explicit.

The current integration is real, but still thin and partly split-brain:

- Tauri launches `ralph` as a detached background process.
- The GUI reads local checkpoint and log files through Tauri commands.
- Tauri invents a `run_id` on launch instead of receiving an authoritative run
  identifier from the CLI.
- Run streaming is implemented by Tauri tailing files, not by a structured CLI
  event channel.
- Session ownership, cleanup, and crash recovery are implicit rather than
  codified.

That is enough for a prototype, but it is not sufficient for the acceptance
criteria that require reliable launch, real-time monitoring, degraded-state
visibility, recoverability, and clear connection status.

## Goals

1. Make the CLI the authoritative backend for every Ralph run.
2. Keep Tauri responsible for desktop integration, process supervision, and
   bridging typed messages into Angular.
3. Define an explicit lifecycle for launch, attach, monitor, resume, cancel,
   close, and cleanup.
4. Replace file-polling-first integration with structured CLI communication.
5. Preserve unattended Ralph behavior: no interactive operator dependencies.
6. Support future UX work without adding backend ambiguity.
7. Make every architectural boundary testable in isolation.
8. Enforce separation of concerns so transport, orchestration, persistence, and
   presentation can evolve independently.
9. Keep the integration modular so richer CLI event emission is an optional
   enhancement, not a prerequisite for architectural correctness.

## Non-Goals

- Replacing the Ralph CLI with a long-lived in-process Rust library inside
  Tauri.
- Moving pipeline execution into Angular.
- Storing workflow state primarily in GUI preferences or frontend memory.
- Introducing a generic RPC framework unless it clearly improves determinism and
  versioning.

## Terms

- `Workspace`: an opened repository in the GUI.
- `Run`: a single Ralph pipeline execution against a repo or worktree.
- `GUI session`: the user-facing run tab/detail context in the desktop app.
- `CLI process`: the OS process running `ralph` for a run.
- `Session controller`: a Tauri-managed object that owns the CLI child process,
  transport handles, cached metadata, and cleanup hooks for one run.

To avoid ambiguity, this document uses `run` for workflow execution and
`session controller` for the Tauri-side owner.

## Acceptance-Criteria Context

The architecture must directly support these product requirements:

### Launch and Immediate Visibility

`AC-4.4` requires the GUI to launch Ralph in the background, surface launch
failures, and show the session immediately as starting. That means the launch
path must return an authoritative run envelope quickly, before the full run is
complete.

### Real-Time Monitoring

`AC-5.1`, `AC-5.2`, `AC-5.3`, `AC-5.4`, and `AC-5.5` require phase, logs,
degraded state, iteration data, and resumability to update live. That argues for
structured CLI events rather than Tauri scraping files after the fact.

### Workspace and Close Behavior

`AC-1.2` requires workspace close confirmation when active runs exist. The
architecture therefore needs a first-class definition of which runs belong to
which workspace, and what happens when the user closes either a run detail view,
 a workspace tab, or the whole app.

### Reliability and Crash Recovery

`AC-15.2` requires graceful handling of CLI crashes and no data loss on app
close. That means the GUI must be able to distinguish:

- run is healthy and connected
- run is still executing but the GUI transport was lost
- CLI process died unexpectedly
- run completed and only historical artifacts remain

## Recommended Architecture

The recommended model is a hybrid process architecture:

1. Angular owns presentation state.
2. Tauri owns desktop integration and per-run supervision.
3. The Ralph CLI owns workflow execution and workflow truth.
4. Communication between Tauri and CLI uses structured stdin/stdout messages for
   control and events.
5. Durable artifacts remain on disk under Ralph-owned run directories for resume
   and post-crash recovery.

This keeps the responsibilities clean:

- Angular never talks to the CLI directly.
- Angular only invokes Tauri commands and subscribes to Tauri events.
- Tauri does not infer workflow semantics from arbitrary files when a live CLI
  connection exists.
- The CLI publishes canonical state transitions and final outcomes.

It also keeps the integration modular:

- transport parsing is separate from session orchestration
- session orchestration is separate from persistence/recovery
- persistence/recovery is separate from Angular-facing view models
- CLI event richness can improve over time without forcing a redesign of the
  outer layers

## High-Level Component Model

```text
Angular UI
  -> Tauri command/event bridge
    -> SessionRegistry (Tauri)
      -> SessionController (one per run)
        -> Ralph CLI child process
            -> Ralph run directory/checkpoints/logs/artifacts
```

### Angular Responsibilities

- launch, resume, cancel, close-session, and close-workspace intents
- render run lists and run detail views
- subscribe to typed events for status/log/timeline updates
- persist UI-only preferences and view state

### Tauri Responsibilities

- validate and normalize launch arguments
- spawn and supervise CLI child processes
- maintain the in-memory registry of active session controllers
- forward structured events from CLI to Angular
- expose attach/replay/snapshot commands for reconnect flows
- perform deterministic cleanup when the user closes a run or exits the app

Tauri should be internally split into modular components rather than one broad
command layer:

- `CommandFacade`: Tauri command handlers and Angular-facing API
- `SessionRegistry`: active run ownership and lifecycle coordination
- `SessionSupervisor`: child-process supervision and timeout handling
- `TransportAdapter`: stdio/file/event parsing behind a stable interface
- `RunStore`: app-local manifests and recovery metadata
- `SnapshotAssembler`: combines live state and durable state for UI snapshots

### CLI Responsibilities

- create authoritative run IDs
- execute the unattended Ralph workflow
- emit structured lifecycle events
- write durable checkpoints/logs/artifacts to disk
- honor control messages such as cancel, shutdown, and resume
- expose stable machine-readable output modes for GUI integration

## Core Design Decision: One Run = One CLI Process

The GUI should treat each launched run as an isolated CLI process. This is the
recommended default and should remain the primary model unless the CLI itself
later grows an explicit daemon mode.

Why this model is preferred:

- failure isolation is strong; one bad run does not poison others
- cleanup semantics are simple and map directly to the user's mental model
- resource accounting is straightforward per run
- resume remains a CLI concern, not a Tauri scheduler concern
- it matches the current product concept already described by the team

Implications:

- a workspace can own multiple concurrent runs, therefore multiple CLI child
  processes
- closing a single run should target only that run's controller/process
- closing a workspace with active runs must offer a bulk decision policy
- app restart must support re-attaching to durable runs even if the original
  controller no longer exists in memory

## Transport Design

The communication layer is part of the architecture, but the detailed message
contract lives in `ralph-gui/docs/designs/tauri-cli-protocol.md`.

The architectural summary is:

- live communication should prefer newline-delimited JSON over stdio
- Tauri is the only GUI-side transport owner
- durable run artifacts are the fallback and recovery channel, not the primary
  live transport
- Tauri should depend on a modular `TransportAdapter` abstraction so the CLI can
  evolve incrementally without forcing an outer-layer redesign
- the transport contract should include explicit handshake, capability,
  correlation, and structured-error semantics so failures are diagnosable rather
  than implicit

## Session Registry in Tauri

Tauri should maintain a `SessionRegistry` keyed by authoritative `run_id`.

Each registry entry stores:

- `run_id`
- `workspace_id`
- `repo_path`
- `worktree_path`
- controller state: `starting | attached | running | stopping | stopped | lost`
- child process handle and PID if locally spawned
- last acknowledged event sequence
- latest run snapshot
- cleanup policy and timestamps

The registry is authoritative for active GUI attachments, but not for workflow
truth. If registry memory disagrees with CLI or checkpoint state, CLI/checkpoint
state wins.

### Robustness Requirements for SessionRegistry

To make the integration operationally robust, `SessionRegistry` should follow
these rules:

- all registry mutations are serialized through one owner path in Tauri
- controller state transitions are explicit and validated, not ad hoc
- duplicate attach/close/cancel requests are idempotent
- registry recovery after app restart reconstructs state from durable manifests
  before the UI is shown as settled
- no UI action depends on renderer memory as the only source of run ownership

The practical goal is to avoid races such as:

- launch completes while the user closes the workspace
- the renderer reloads while a run is transitioning to failed/completed
- cancel is clicked twice and produces inconsistent cleanup state
- a process exits between a status query and an attach request

## Required CLI Capabilities

The CLI must expose a GUI-safe execution mode with stable machine-readable
behavior for launch, resume, inspect, cancel, and event streaming.

The architecture does not require fixed command names, but it does require:

- an authoritative bootstrap response for launched/resumed runs
- structured lifecycle events with ordered sequencing
- compatibility/version signaling
- a recoverable relationship between live transport and durable artifacts

Detailed command, event, and payload expectations live in
`ralph-gui/docs/designs/tauri-cli-protocol.md`.

## Command Flow

### 1. Launch Run

1. Angular sends `launchSession` to Tauri.
2. Tauri validates workspace/worktree/prompt context.
3. Tauri spawns a new CLI process in GUI integration mode.
4. CLI emits bootstrap response with authoritative `run_id`.
5. Tauri creates a `SessionController` and stores it in `SessionRegistry`.
6. Tauri emits `session-created` to Angular.
7. Angular shows the run immediately in `Starting` state.
8. Subsequent CLI events drive the page into running/phase/log states.

### 2. Resume Run

1. Angular requests resume for a resumable run.
2. Tauri resolves the existing durable run directory.
3. Tauri spawns a new CLI process in resume mode.
4. CLI rehydrates state from checkpoint and emits bootstrap + replay metadata.
5. Tauri attaches the resumed controller to the same `run_id`.

### 3. Attach/Reattach

Needed for app restart or renderer refresh.

1. Angular requests active/historical run snapshots for a workspace.
2. Tauri rebuilds a snapshot from:
   - active controllers first
   - durable run manifests/checkpoints second
3. Angular subscribes to Tauri event channels for each visible run.
4. Tauri replays buffered events after the last known sequence if available.

Attach/reattach should be idempotent. If Angular requests attach multiple times
for the same run, Tauri should return the existing attachment state rather than
creating duplicate listeners or duplicate controllers.

### 4. Cancel Run

Cancel must be explicit, structured, and reversible only through a later resume
flow if the CLI supports it.

1. Angular sends cancel intent.
2. Tauri writes `cancel` control message to the CLI.
3. CLI performs graceful shutdown and checkpoint finalization.
4. CLI emits `run.cancelled` and cleanup events.
5. Tauri updates registry state and releases resources.

Deleting lock files directly should be treated as a temporary implementation
detail to remove, not the target architecture.

## Close and Cleanup Semantics

This is the most important lifecycle rule in the design.

### Closing a Run Detail View

Closing the UI view for a run does not automatically kill the run. It only ends
that renderer subscription unless the user explicitly chooses `Close and stop`.

Reason: users need to navigate freely without accidentally terminating work.

### Closing a Session

If the product uses a first-class session object that maps one-to-one to a run,
then closing the session should mean one of two explicit actions:

1. `Detach`: remove the run from the current visible context but allow it to
   continue in the background.
2. `Stop and close`: ask Tauri to request CLI shutdown and cleanup.

The default recommendation is:

- completed/failed/cancelled runs: `Close` means detach from active UI state
  and keep artifacts
- running/paused runs: the user must choose between detach and stop

### Closing a Workspace

When a workspace closes and active runs exist, Tauri must present a policy that
maps to `AC-1.2`:

- `Keep running in background`
- `Stop all runs and close workspace`
- `Cancel`

Recommended default: keep running in background.

Why: it avoids destructive surprises and matches desktop-tool expectations.

### Closing the App

On app exit, Tauri should support a deterministic shutdown policy:

- by default, detach from running CLI processes and persist enough metadata to
  reattach on next launch
- if the user explicitly chooses `Quit and stop active runs`, Tauri sends stop
  to every active controller and waits for bounded cleanup

The GUI must never rely on best-effort orphaning without metadata. Every detach
must leave a recoverable record.

## Cleanup Contract

Cleanup must happen in layers.

### CLI-Owned Cleanup

The CLI is responsible for:

- releasing run locks
- flushing logs
- writing final checkpoint/outcome state
- cleaning transient temp files that belong strictly to that run
- emitting final cleanup events

### Tauri-Owned Cleanup

Tauri is responsible for:

- closing pipes and event listeners
- removing the controller from `SessionRegistry`
- pruning renderer-only caches
- marking the run detached or finalized in app-local metadata

Tauri cleanup should also be bounded and observable:

- graceful-stop waits use explicit timeouts
- timeout expiry is recorded as `forced_detach` or equivalent, not hidden
- cleanup steps are safe to retry after partial failure
- partial cleanup never deletes durable artifacts needed for resume or diagnosis

### Artifact Retention

Do not delete durable run artifacts on normal close. They are needed for:

- completed run inspection
- failed run diagnostics
- resumable state
- audit history

Retention policy should be a separate concern, not coupled to window close.

## Connection Model and Status Bar Semantics

The connection indicator in `AC-2.3` should represent the Tauri-to-CLI transport
health for the active run context, not a vague global boolean.

Recommended connection states:

- `Connected`: live controller attached and events flowing
- `Reconnecting`: transport lost, Tauri is attempting reattach/replay
- `Detached`: run exists but no live controller is attached
- `Exited`: CLI process ended normally
- `Crashed`: CLI process died unexpectedly

This enables better UX than today's simple connected/disconnected language.

## Error Handling

### Launch Errors

If the CLI cannot start, Tauri must return a structured error envelope with:

- user-safe summary
- machine-readable error code
- optional diagnostics block for logs/dev mode
- whether retry is sensible

### Runtime Errors

If the CLI emits an internal failure:

- preserve the last successful run snapshot
- surface the failure in run detail and session list
- keep logs and checkpoint pointers available
- offer context-appropriate actions: retry, resume, open artifacts, go to config

### Transport Loss

If stdio breaks while the child still appears alive:

- mark the controller as `lost`
- attempt bounded re-attachment via durable run inspection
- if reattach fails, show `Disconnected from live run; last checkpoint available`

Transport loss should not automatically imply workflow failure. The architecture
must separate:

- lost transport
- lost child process
- unrecoverable run failure

Those are different operational states and should remain distinct in both Tauri
and the UI.

### Crash Recovery

On app start, Tauri should scan durable GUI run manifests and known Ralph run
directories to detect:

- active orphaned runs still executing
- resumable interrupted runs
- finished runs with unseen final status

This is required for `AC-15.2` reliability.

Recovery should be conservative:

- if process liveness cannot be proven, mark the run `unknown` or `detached`, not
  falsely `running`
- if both live memory and durable state exist, prefer the newest timestamped
  durable fact for final-state decisions
- if recovery metadata is corrupt, preserve artifacts and surface a repairable
  diagnostics state rather than deleting anything

## Process Supervision

Robustness depends heavily on supervision behavior in Tauri.

Each locally spawned CLI process should have:

- spawn timestamp
- pid if available
- startup timeout for bootstrap receipt
- heartbeat or progress timeout policy for long silent periods
- exit classification: expected, cancelled, failed, crashed, unknown

Recommended supervision rules:

- if bootstrap is not received within the startup timeout, mark launch failed
- if the process exits before bootstrap, return a launch error with captured diagnostics
- if the process exits after bootstrap, classify the exit using final event,
  exit code, and durable checkpoint state together
- do not assume silence means failure for legitimately long phases; use bounded
  heuristics and explicit UI messaging such as `still running; no recent output`

## Testability Requirements

Everything in this architecture should be testable without full end-to-end app
startup.

Required test seams:

- `TransportAdapter` can be fed recorded CLI/stdout/checkpoint inputs
- `SessionRegistry` can be tested with fake supervisors and fake stores
- `SessionSupervisor` can be tested against stub child-process handles and timed
  event sequences
- `SnapshotAssembler` can be tested with live-only, durable-only, and divergent
  state inputs
- Tauri command handlers can be tested as thin wrappers over these services

Recommended test layers:

- unit tests for parsing, state transitions, and idempotency
- integration tests for launch/resume/cancel/close/recovery flows
- fixture-based tests using captured checkpoints, logs, and event streams
- failure-path tests for timeouts, partial writes, replay gaps, and crash
  recovery

The architecture should make it easy to prove behavior with deterministic tests,
not only with manual desktop testing.

## Separation of Concerns Rules

The integration should remain aggressively separated by responsibility.

- Angular owns rendering, user actions, and view composition only.
- Tauri command handlers own API translation only.
- Session orchestration owns lifecycle decisions only.
- Transport adapters own message/file ingestion only.
- Persistence owns manifests and recovery metadata only.
- The CLI owns workflow execution and canonical workflow state only.

No layer should simultaneously:

- parse transport data and make product lifecycle policy decisions
- manage process state and assemble Angular view models
- treat UI convenience state as canonical workflow truth

This separation is what makes the integration maintainable and testable.

## Persistence Safety

The app-local run manifest and any Tauri-owned metadata should be written
defensively.

Recommended rules:

- write manifests atomically when practical
- prefer replace-overwrite patterns to reduce partial-write risk
- include schema version in persisted metadata
- tolerate unknown fields during read for forward compatibility
- keep corruption handling non-destructive and diagnosable

The key principle is that GUI metadata may be lost or repaired, but it must not
become a source of destructive cleanup.

## State Model

The GUI should maintain two related but distinct models.

### Live Run State

Derived from CLI events and held in Tauri/Angular for responsive UI.

Fields should include:

- `run_id`
- `workspace_id`
- `repo_path`
- `worktree_path`
- `status`
- `phase`
- `iteration`
- `review_pass`
- `agent_identity`
- `is_degraded`
- `last_error`
- `started_at`
- `updated_at`
- `connection_state`

### Durable Run Manifest

Stored on disk and used for restart/recovery.

Suggested manifest fields:

- `run_id`
- `repo_path`
- `worktree_path`
- `run_dir`
- `pid` if known
- `spawned_by_gui`
- `created_at`
- `last_seen_seq`
- `last_known_status`
- `last_known_phase`
- `cleanup_state`
- `detached_at`

This manifest belongs in Tauri app data, while canonical run artifacts remain in
Ralph-owned directories.

## Type Reconciliation Strategy

Type reconciliation is an architectural concern, but the detailed mapping rules
live in `ralph-gui/docs/designs/tauri-cli-protocol.md`.

The architectural summary is:

- Specta covers `Tauri Rust types -> TypeScript types` for the frontend contract
- Specta does not remove the need for `CLI payload types -> Tauri domain types`
- Tauri is the normalization boundary between CLI protocol types and stable GUI
  contract types
- type layers must remain explicit: `CLI protocol -> Tauri domain -> frontend contract`
- protocol drift must be contained at the mapping boundary, not leaked across
  the entire application

## Suggested Tauri API Shape

Angular should continue to talk only to a typed `TauriService`, but the command
set should evolve toward lifecycle-oriented operations:

- `launch_run(request) -> RunBootstrap`
- `resume_run(request) -> RunBootstrap`
- `list_workspace_runs(workspace_id) -> RunSnapshot[]`
- `get_run_snapshot(run_id) -> RunSnapshot`
- `attach_run(run_id, last_seq?) -> AttachResult`
- `detach_run(run_id) -> void`
- `cancel_run(run_id) -> void`
- `close_run(run_id, policy) -> void`
- `close_workspace(workspace_id, policy) -> void`
- `get_run_artifacts(run_id) -> RunArtifacts`

Tauri event channels should be stable and typed:

- `run://<run_id>/event`
- `workspace://<workspace_id>/summary`
- `app://run-lifecycle`

Avoid event names derived from ad hoc implementation details once the transport
contract is formalized. Detailed command/event contract definitions live in
`ralph-gui/docs/designs/tauri-cli-protocol.md`.

Command handlers should also be idempotent where practical:

- `attach_run` on an already attached run returns current attachment state
- `detach_run` on a detached run succeeds as a no-op
- `cancel_run` on a terminal run returns a terminal-state response, not an error
- `close_run` should report whether it detached, stopped, or found the run already closed

Internally, these commands should target interfaces rather than concrete file or
process code so the implementation remains modular and easy to test.

## Versioning and Compatibility

The CLI/Tauri protocol must be versioned from day one. The detailed versioning
rules belong in `ralph-gui/docs/designs/tauri-cli-protocol.md`, but the
architectural requirement is simple: incompatible protocol changes must fail fast
and additive evolution should remain backward-compatible where practical.

## Security Boundaries

In line with `AC-15.4`:

- Angular must not handle secret material beyond user-visible fields already
  required for UI
- Tauri must avoid logging secret-bearing command arguments
- CLI events must redact sensitive tokens or credentials before emission
- GUI-safe JSON events should never include raw API keys or auth headers
- Tauri should prefer passing file paths or config references over inline secret
  payloads

## Observability

To support operations and debugging, the Tauri layer should maintain its own
supervision log separate from Ralph's pipeline log.

Recommended Tauri supervision events:

- process spawned
- bootstrap received
- attach requested
- transport lost
- replay started/completed
- stop requested
- graceful exit
- forced detach

This log is for desktop integration diagnostics, not pipeline behavior.

For robustness analysis, include stable correlation keys in both Tauri and CLI
diagnostics:

- `run_id`
- workspace identifier
- pid when known
- protocol version
- last seen sequence number

Without correlation keys, restart and reconnect bugs become much harder to
diagnose.

## Migration Plan

### Phase 1 - Stabilize Contracts

- make CLI return authoritative run IDs on launch/resume
- introduce structured JSON bootstrap output
- add protocol versioning
- keep existing file-based log/checkpoint reads as fallback only
- extract internal Tauri boundaries (`TransportAdapter`, `SessionRegistry`,
  `RunStore`, `SnapshotAssembler`) before making protocol changes broad

### Phase 2 - Live Events

- add structured event emission from CLI
- update Tauri to consume stdout events instead of tailing log files for live UI
- preserve log-file reading only for historical replay

Note: richer CLI event emission is recommended, but the architecture should not
depend on a big-bang CLI rewrite. Tauri should be able to improve incrementally
as the CLI exposes more machine-readable state.

### Phase 3 - Session Registry

- add `SessionRegistry` and per-run `SessionController`
- introduce attach/detach/close policies
- persist durable GUI run manifests for restart recovery

### Phase 4 - UX Alignment

- wire connection states into status bar and run detail
- support workspace-close policies for active runs
- surface better launch/runtime diagnostics

### Phase 5 - Remove Legacy Shortcuts

- stop generating synthetic run IDs in Tauri
- stop treating lock-file deletion as cancellation architecture
- stop relying on file polling as the primary live transport

## Architecture Rules

These rules should be treated as binding unless a later architecture decision
explicitly replaces them.

1. Angular never launches or supervises CLI processes directly.
2. Tauri never invents authoritative run identity.
3. CLI remains the source of truth for run lifecycle and status.
4. Live updates use structured messages first, file inspection second.
5. Closing UI does not implicitly destroy durable run state.
6. Cleanup is explicit, layered, and observable.
7. Workspace-close and app-close behavior must be policy-based, not accidental.
8. Protocol versioning is mandatory.
9. Session registry operations and lifecycle commands must be idempotent.
10. Durable artifacts are never deleted as part of uncertain recovery.
11. Final run state is determined from authoritative CLI output plus durable
    artifacts, not frontend assumptions.
12. Every architectural component must expose test seams that allow deterministic
    verification without full desktop E2E execution.
13. Separation of concerns is mandatory: transport, supervision, persistence,
    orchestration, and presentation stay modular.
14. Richer CLI event emission is an optimization path, not a license to blur
    boundaries or couple Angular directly to CLI behavior.

## Open Design Questions

These do not block the architecture, but they should be resolved before full
implementation:

1. Should the CLI expose dedicated `gui-*` subcommands, or a general machine API
   mode on existing commands?
2. Should historical event replay come from a compact event journal, from
   checkpoint snapshots, or both?
3. Should completed run retention be bounded by count, age, or size?
4. Should `Detach` be visible to users as a first-class action, or only implied
   by closing the app/workspace?

## Recommendation Summary

The recommended architecture is:

- one Ralph run equals one CLI process
- Tauri owns supervision and session registry
- CLI owns run identity, lifecycle, and structured events
- Angular consumes only Tauri commands/events
- durable artifacts remain the recovery plane, not the live transport

That model best fits the current product concept, the existing Tauri/Angular
split, and the acceptance criteria for launch reliability, live monitoring,
degraded-state visibility, and safe cleanup.
