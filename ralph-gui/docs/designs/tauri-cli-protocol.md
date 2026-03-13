# Ralph GUI - Tauri/CLI Protocol Contract

Terminology in this document follows `ralph-gui/docs/glossary.md`.

This document defines the detailed communication contract between Tauri and the
Ralph CLI. It complements, but does not replace,
`ralph-gui/docs/designs/tauri-cli-backend-architecture.md`.

Use this document for message-level details, type-layer rules, compatibility
guidance, and testable protocol expectations.

## Purpose

This document exists to keep low-level protocol detail out of the higher-level
architecture document while still making the communication contract explicit,
reviewable, and testable.

## Protocol Summary

- Tauri is the only GUI-side process that talks directly to the Ralph CLI.
- The preferred live transport is newline-delimited JSON over stdio.
- The CLI owns protocol payload production.
- Tauri owns protocol validation, normalization, and translation into stable
  frontend contract types.
- Durable run artifacts remain the fallback and recovery channel.
- Protocol messages must be structured, correlated, versioned, and safe to
  evolve incrementally.

## Handshake and Session Establishment

The protocol should begin with an explicit bootstrap/handshake rather than
relying on implicit assumptions.

The first machine-readable response from the CLI should establish:

- protocol version
- CLI version/build identity
- authoritative `run_id` if a run is being launched or resumed
- supported capabilities
- whether the process is fresh-launch, resume, inspect-only, or recovery mode

This handshake is what allows Tauri to fail fast on incompatibility instead of
discovering mismatches later during streaming.

### Capability Advertisement

The CLI bootstrap response should advertise capability flags rather than making
Tauri infer support from version strings alone.

Examples of useful capabilities:

- structured event streaming
- replay support
- heartbeat support
- cancellation acknowledgement
- chunked payload support
- resume metadata support

Version checks still matter, but capability negotiation makes incremental
evolution more robust.

## Transport Contract

### Primary Live Transport

The preferred live transport is newline-delimited JSON over stdio.

- Tauri writes control messages to CLI stdin.
- CLI writes events and responses to stdout.
- CLI writes human-oriented diagnostics to stderr.

Example envelope:

```json
{
  "version": 1,
  "channel": "event",
  "event": "run.phase_changed",
  "run_id": "run_123",
  "seq": 17,
  "timestamp": "2026-03-13T17:42:11Z",
  "payload": {
    "phase": "Review",
    "iteration": 2,
    "review_pass": 1
  }
}
```

### Framing Rules

To keep the transport robust, the framing rules should be explicit:

- stdout is reserved for machine-readable protocol messages only
- each stdout frame is exactly one UTF-8 JSON object terminated by `\n`
- stderr is reserved for human-readable diagnostics and never parsed as protocol
- partial or malformed frames are treated as protocol errors, not best-effort
  messages
- oversized payloads should be chunked or redirected to durable artifacts rather
  than emitted as a single unbounded frame

These rules matter more than the exact message schema because framing bugs are
one of the fastest ways to make a local IPC protocol unreliable.

### Why stdio First

Use stdio first because it is:

- local-only by default
- naturally tied to child-process supervision
- easy to version and test
- simpler than opening ports or embedding HTTP servers
- aligned with the CLI's existing process model

### Durable Fallback Channel

The run directory remains the recovery channel, not the primary live channel.

Disk artifacts are still required for:

- resume after GUI restart
- post-crash inspection
- offline run detail viewing
- auditability of pipeline behavior

Tauri may read artifacts only when:

- attaching to an already-running or previously-running run
- reconstructing history after app restart
- backfilling missed events after transport loss

### Modular Transport Rule

The integration must not assume that correctness depends on an immediate large
CLI event expansion.

Tauri should depend on a `TransportAdapter` abstraction that can be backed by:

- current checkpoint/log inspection
- current Tauri event wiring
- future richer CLI event streams

## CLI Capability Contract

To support the GUI cleanly, the CLI should expose a GUI-safe execution mode,
for example:

```text
ralph gui-run --json --emit-events
ralph gui-resume --run-id <id> --json --emit-events
ralph gui-inspect --run-id <id> --json
ralph gui-cancel --run-id <id> --json
```

The exact command names can change, but the capability set should not.

### Launch Contract

When Tauri launches a run, the CLI must emit an early bootstrap message before
heavy work begins:

```json
{
  "version": 1,
  "channel": "response",
  "command": "launch",
  "run_id": "run_123",
  "payload": {
    "repo_path": "/repo",
    "worktree_path": "/repo/.worktrees/wt-62-gui",
    "run_dir": "/repo/.agent/runs/run_123",
    "status": "starting"
  }
}
```

This bootstrap response is the preferred source of authoritative run identity.

### Correlation Rules

Request/response style protocol messages should carry correlation identifiers.

Recommended fields:

- `request_id` on commands sent by Tauri
- `response_to` or echoed `request_id` on CLI responses
- `run_id` on any run-scoped event or response

This prevents ambiguity when multiple operations occur close together or when
launch/resume/inspect flows share the same protocol machinery.

### Structured Error Envelope

Protocol-level failures should use a structured error shape rather than raw
strings.

Recommended fields:

- `code`
- `message`
- `retryable`
- `details` or diagnostics block when safe
- `request_id` / `run_id` when relevant

This aligns with good Tauri command practice as well: errors should be typed and
structured so the frontend can react predictably.

## Event Contract

### Minimum Event Taxonomy

The CLI should emit at least these event classes:

- `run.started`
- `run.status_changed`
- `run.phase_changed`
- `run.iteration_started`
- `run.iteration_completed`
- `run.review_started`
- `run.review_completed`
- `run.degraded`
- `run.log`
- `run.checkpoint_written`
- `run.warning`
- `run.failed`
- `run.completed`
- `run.cancelled`
- `run.cleanup_started`
- `run.cleanup_finished`

Every event must include `run_id`, a monotonic `seq`, and a timestamp.

### Event Delivery Guarantees

The transport does not need distributed-systems complexity, but it does need
clear guarantees.

Recommended guarantees:

- per-run ordering is preserved by `seq`
- duplicate events are tolerated and ignored by replay-aware consumers
- replay after reconnect starts from `last_seen_seq + 1`
- snapshots are authoritative if event replay cannot fully fill a gap
- final lifecycle events are persisted to durable state before process exit

If the CLI does not yet emit enough event detail, Tauri may synthesize
non-authoritative UI convenience events from durable state, but those events
must be treated as derived projections rather than canonical workflow facts.

### Heartbeats and Silent Periods

Long-running workflows may legitimately go quiet for periods of time, so the
protocol should distinguish `no recent output` from `dead transport`.

Recommended options:

- explicit heartbeat/progress frames from the CLI, or
- a documented silent-period contract combined with checkpoint freshness rules

Either way, Tauri should not have to guess blindly whether silence means idle,
busy, blocked, or crashed.

### Backpressure and Throughput

Tauri should:

- buffer live events per run with bounded queues
- treat log lines as lossy-display / durable-on-disk data when buffers overflow
- treat lifecycle/status events as loss-intolerant and preserve them
  preferentially
- expose overflow state to the UI when live display is truncated

For the Tauri-to-frontend side, high-frequency updates should prefer typed
streaming primitives over an ad hoc global event spray. Tauri's typed channel
pattern is a better fit for sustained ordered streams than loosely structured UI
events.

## Type Reconciliation Strategy

The GUI integration must not let the CLI, Tauri, and Angular each drift into
separate definitions of the same concepts.

### Required Type Layers

Use three intentional layers of types in Rust:

- `CLI protocol types`
  - structs/enums representing machine-readable payloads emitted by or sent to
    the Ralph CLI
- `Tauri domain types`
  - internal Rust types used by supervision, registry, snapshot assembly, and
    recovery logic
- `Frontend contract types`
  - the stable types exposed through Tauri commands/events to Angular

These layers may look similar, but they are not interchangeable by default.

Important clarification:

- Specta solves `Tauri Rust types -> TypeScript types` for the frontend contract
- Specta does not solve `CLI payload types -> Tauri domain types`
- a reconciliation boundary is still required between the CLI and Tauri even
  when Specta is used for Rust-to-TypeScript generation

### Mapping Rule

When two layers need the same concept, prefer explicit mapping over ad hoc
duplication.

That means:

- one canonical type per layer for `RunStatus`, `RunSnapshot`, `RunBootstrap`,
  `RunEvent`, and related concepts
- explicit conversion code between layers
- no silent shape-copying across modules because fields happen to match today
- normalization at the Tauri boundary when CLI shapes are richer, noisier, or
  versioned differently

Recommended flow:

```text
CLI payload type -> Tauri domain type -> Frontend contract type
```

### Rust Guidance

Recommended module ownership:

- `cli_protocol::*` for CLI-facing message types
- `domain::*` for Tauri orchestration types
- `frontend_contract::*` for command/event output types
- `mappers::*` or equivalent conversion points where lossy or validated
  conversions are required

Specta should be applied at the `frontend_contract::*` layer, not treated as a
replacement for protocol/domain mapping.

Recommended rule:

- CLI wire types may be plain serde-facing protocol structs
- Tauri domain types normalize and validate CLI semantics
- frontend contract types derive `specta::Type` and represent the stable GUI API

Use `TryFrom` when:

- protocol versions differ
- fields are optional in one layer but required in another
- enum variants need validation or collapse
- backward compatibility must be enforced explicitly

### Canonical Enums and Shared Concepts

The following concepts should have clearly named canonical representations in
Tauri domain code, even if the CLI protocol uses different wire shapes:

- run status
- connection state
- cleanup state
- launch result
- attach result
- close policy
- run event kind

Tauri is the normalization boundary that reconciles protocol-level detail into
stable domain concepts for the GUI.

### Compatibility Rule

If the CLI evolves to emit richer events or slightly different payloads, Tauri
should absorb that change in protocol-to-domain mapping rather than forcing the
rest of the application to change types immediately.

## Tauri Command/Event Contract

Angular should continue to talk only to a typed `TauriService`, and the command
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

Command handlers should be idempotent where practical:

- `attach_run` on an already attached run returns current attachment state
- `detach_run` on a detached run succeeds as a no-op
- `cancel_run` on a terminal run returns terminal-state information, not a hard
  error
- `close_run` reports whether it detached, stopped, or found the run already
  closed

## Versioning and Compatibility

The CLI/Tauri protocol must be versioned from day one.

Requirements:

- every message includes a protocol version
- Tauri validates protocol compatibility during bootstrap
- incompatible versions fail fast with a clear upgrade message
- additive evolution should be backward-compatible where practical
- unknown fields are ignored where safe
- unknown event kinds are surfaced as diagnostics without corrupting the run
  state machine
- deprecated fields are removed only after a compatibility window, not
  immediately

### Schema Evolution Rules

To keep protocol evolution safe:

- add new optional fields before making them required
- prefer additive enum variants over semantic reuse of old ones
- never change the meaning of an existing field name silently
- reserve room for future metadata such as `capabilities`, `source`, or
  `checkpoint_ref`
- keep wire compatibility separate from internal refactors in Tauri

## Testability of the Protocol Contract

This protocol must be testable without full desktop E2E execution.

Required tests should cover:

- CLI payload -> domain mapping
- domain -> frontend contract mapping
- backward-compatible decoding of older payload shapes
- rejection of invalid or ambiguous payloads
- enum normalization and unknown-variant handling
- replay after reconnect
- overflow and backpressure behavior
- handshake/version mismatch behavior
- malformed frame handling
- structured error envelope decoding
- heartbeat or silent-period timeout behavior

If protocol mapping is not explicitly tested, drift between CLI, Tauri, and
Angular will become one of the highest-risk failure modes in the integration.
