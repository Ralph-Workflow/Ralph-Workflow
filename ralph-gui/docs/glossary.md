# Ralph GUI Glossary

This glossary defines the canonical language for Ralph GUI documentation.

If multiple docs use different terms for the same concept, this file is the
source of truth.

## Core Terms

### Workspace

An opened repository in the GUI.

- A workspace corresponds to a repository root path.
- Workspace state includes navigation context, visible pages, and workspace-level
  summaries.
- A workspace can contain multiple worktrees and multiple sessions.

### Worktree

A git worktree associated with a workspace.

- Worktrees provide isolated working directories for parallel development.
- Sessions run against either the main repository path or a selected worktree
  path.

### Session

The user-facing launched unit in the GUI.

- A session is what users create, view in session lists, resume, cancel, and
  inspect in detail views.
- Session is the preferred product and UX term for launched work.

### Run

The underlying Ralph CLI execution for a session.

- Run is the preferred architecture and execution term when discussing process
  lifecycle, phases, logs, checkpoints, degraded state, retries, and recovery.
- At the current architecture boundary, one session maps to one run.

### Session Controller

A Tauri-owned object that supervises one active run attachment.

- It owns process handles, transport handles, and cleanup hooks for a run.
- It is not the source of truth for workflow state; the CLI and durable run
  artifacts remain authoritative.

### Session Registry

The Tauri-owned registry of active session controllers.

- It tracks live GUI attachments and orchestration state.
- It should be treated as active-session coordination state, not durable workflow
  truth.

### Checkpoint

A durable on-disk snapshot of Ralph workflow state.

- Checkpoints support resume, recovery, diagnostics, and post-crash inspection.
- Checkpoints are part of run durability, not UI-only state.

### Durable Artifacts

Run-owned files persisted by Ralph, such as checkpoints, logs, and related
artifacts.

- Durable artifacts are used for replay, diagnostics, and recovery.
- They should not be deleted during uncertain cleanup or recovery flows.

### Transport Adapter

The Tauri-side abstraction that converts CLI/stdout/file-backed signals into a
stable internal event/snapshot interface.

- This abstraction allows the GUI integration to remain modular.
- It supports current behavior and future richer CLI event emission without
  forcing a redesign.

### CLI Protocol Type

A Rust type representing machine-readable data exchanged with the Ralph CLI.

- These types are concerned with wire shape, serde compatibility, and protocol
  versioning.
- They are not automatically the same as Tauri domain types.

### Tauri Domain Type

A Rust type used internally by Tauri for supervision, orchestration, recovery,
and snapshot logic.

- These types normalize CLI protocol data into stable application concepts.
- They are the main boundary where CLI/Tauri type differences are reconciled.

### Frontend Contract Type

A Rust type exposed by Tauri commands/events to the Angular frontend.

- These are the Rust types that should drive generated TypeScript types.
- In this project, Specta is the preferred tool for this Rust-to-TypeScript
  contract generation.
- Specta does not remove the need for CLI protocol to Tauri domain mapping.

## UI Terms

### Sessions List

The page or sidebar view that shows user-facing sessions for a workspace.

### Run Detail

The detailed monitoring view for a session's underlying run.

- We keep `Run Detail` as the page name because the page focuses on execution
  state, logs, phases, retries, and checkpoints.
- This is not a contradiction: the list is organized around sessions, while the
  detail view focuses on the run behind the selected session.

### Review & Launch

The final step of the new session wizard.

- This is the canonical name for wizard step 3.
- Do not use `Preflight` as the primary label in GUI docs unless describing an
  internal implementation concern.

## State Language

Use these terms consistently when possible:

- `starting`
- `running`
- `paused`
- `degraded`
- `failed`
- `completed`
- `cancelled`
- `detached`
- `reconnecting`
- `crashed`

When documentation needs a user-facing phrase, prefer plain language that maps
cleanly back to these states.

## Ownership Rules

### Angular

Owns presentation state, user interactions, and view composition.

### Tauri

Owns desktop integration, process supervision, transport bridging, and active
session orchestration.

### CLI

Owns workflow execution, canonical run lifecycle, checkpoints, logs, and final
execution truth.

## Documentation Rules

- New GUI docs should link to this glossary.
- Product and UX docs should prefer `Session` for user-facing actions.
- Backend and architecture docs should use `Run` when discussing execution
  mechanics.
- If one term is used in a context where the other would be ambiguous, define it
  locally rather than switching terms casually.
