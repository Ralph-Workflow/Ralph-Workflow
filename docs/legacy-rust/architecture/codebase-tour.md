# Codebase Tour (Where the Architecture Lives)

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This document is a high-level map of the `ralph-workflow` codebase, oriented toward a contributor reading the Rust for the first time.

If you want the state machine behavior first, start with `pipeline-lifecycle.md` and `event-loop-and-reducers.md`.

## Entry Points

- Binary entrypoint: `ralph-workflow/src/main.rs`
  - Parses CLI args and calls `app::run(...)`.
- Library root + module map: `ralph-workflow/src/lib.rs`
  - Re-exports test utilities behind `test-utils` feature.

## High-Level Runtime Flow

At a very high level, `ralph` runs in two layers:

1. CLI/app layer (pre-repo-root): parse args, load config, discover repo root, create a `Workspace`, handle `--init/--diagnose/--resume`, run preflight git checks.
2. Pipeline layer (post-repo-root): run the reducer event loop (pure orchestration + pure reduction + impure effect handling).

The split exists because you cannot reliably use `Workspace` until the repo root is discovered.

## The Two Effect Layers (Where I/O is Allowed)

- CLI/app effects: `ralph-workflow/src/app/effect.rs`
  - Executed by: `ralph-workflow/src/app/effect_handler.rs`
  - May use `std::fs` (this is *before* `WorkspaceFs` exists).
- Pipeline effects: `ralph-workflow/src/reducer/effect/types.rs`
  - Executed by: `ralph-workflow/src/reducer/handler/` (via `MainEffectHandler`)
  - Must use `ctx.workspace` for filesystem I/O (no raw `std::fs`).

See `effect-system.md` for the hard rules.

## The Reducer Event Loop (The Main Engine)

- Event loop driver: `ralph-workflow/src/app/event_loop/`
  - `core.rs` - Main loop implementation that builds an initial `reducer::PipelineState` from config and drives the event loop
  - `config.rs` - Event loop configuration and setup
  - `error_handling.rs` - Error recovery and fault tolerance mechanisms
  - `trace.rs` - Event loop trace ring buffer for debugging and diagnostics
  - Repeats: `determine_next_effect(state)` -> handler executes -> emits `PipelineEvent` -> `reduce(state, event)`.
  - Terminates based on `PipelineState::is_complete()`.

Core reducer modules:

- Orchestration (state -> next effect): `ralph-workflow/src/reducer/orchestration/`
- Reduction (state + event -> state): `ralph-workflow/src/reducer/state_reduction/`
- State types: `ralph-workflow/src/reducer/state/`
- Event types: `ralph-workflow/src/reducer/event/`
  - `types.rs` - Core event type definitions

See `event-loop-and-reducers.md` for invariants and best practices.

## Phase Implementations vs Reducer Effects

You will see a `phases` module:

- `ralph-workflow/src/phases/`

This module contains shared execution helpers used by effect handlers (prompt building, diff truncation, commit message generation helpers, etc.).
The important architectural rule is:

- The *reducer* decides what happens next.
- Phase helpers execute the *how* and report results via events.

## Agents, Prompts, and Streaming Output

- Agent registry + configuration: `ralph-workflow/src/agents/`
  - `AgentRegistry`, agent configs, CCS alias support, fallback chain config.
- Prompt generation for agents: `ralph-workflow/src/prompts/`
  - Text templates in `ralph-workflow/prompts/templates/` (system prompts, phase prompts).
- Streaming NDJSON parsing and rendering: `ralph-workflow/src/json_parser/`
  - Provider-specific parsers and the `StreamingSession` contract.

Two frequently-confused concepts:

- PROMPT.md *work guides* (end-user templates) live under `ralph-workflow/templates/prompts/` and are embedded by `ralph-workflow/src/templates/mod.rs`.
- Agent *system prompts* live under `ralph-workflow/prompts/templates/` and are rendered by `ralph-workflow/src/prompts/`.

See `agents-and-prompts.md` and `streaming-and-parsers.md`.

## Checkpoints and Resume

Checkpoint persistence exists to make unattended runs recoverable:

- Checkpoint types + serialization: `ralph-workflow/src/checkpoint/`
- App-layer resume UX + validation: `ralph-workflow/src/app/resume.rs`

The checkpoint file is `.agent/checkpoint.json` (relative to repo root).

See `checkpoint-and-resume.md`.

## Git Operations and Rebase

All git operations are via libgit2 (no git CLI dependency):

- Git helpers: `ralph-workflow/src/git_helpers/`
  - Start commit baseline (`start_commit`) and review baseline (`review_baseline`).
  - Commit operations (`repo`).
  - Rebase flow and rebase recovery (`rebase`).
  - Git wrapper/hook management for safe agent phases (`wrapper`, `hooks`).

See `git-and-rebase.md`.

## Files and .agent/ Artifacts

- `.agent/` file lifecycle helpers: `ralph-workflow/src/files/`
  - PLAN/ISSUES/STATUS file management.
  - PROMPT.md validation and protection/monitoring.

## Tests (Where to Look)

- Unit tests live near modules in `ralph-workflow/src/...`.
- Integration tests are a separate crate: `tests/` (workspace member).
  - The crate is typically named `ralph-workflow-tests`.

See `tests/INTEGRATION_TESTS.md` before changing integration tests.
