//! Core event type definitions for the reducer architecture.
//!
//! This module re-exports all event enum definitions organized by category.
//! Each event represents a fact about what happened during pipeline execution.
//!
//! # Event Architecture
//!
//! Events follow the reducer architecture contract:
//! - **Events are facts** (past-tense, descriptive)
//! - **Events carry data** needed for reducer decisions
//! - **Handlers emit events**, reducers decide what to do next
//!
//! # Event Categories
//!
//! Events are organized into logical categories for type-safe routing:
//! - [`PlanningEvent`] - Plan generation events (in `planning.rs`)
//! - [`PromptInputEvent`] - Prompt input materialization (in `prompt_input.rs`)
//! - [`RebaseEvent`] - Git rebase operations (in `rebase.rs`)
//! - [`CommitEvent`] - Commit generation (in `commit.rs`)
//! - [`AwaitingDevFixEvent`] - Dev-fix flow events (in `awaiting_dev_fix.rs`)
//!
//! Other categories are defined in separate files:
//! - `DevelopmentEvent` in `development.rs`
//! - `ReviewEvent` in `review.rs`
//! - `AgentEvent` in `agent.rs`
//! - `ErrorEvent` in `error.rs`
//!
//! Supporting types are in `supporting_types.rs`.

// Re-export types from state module that are used in events
pub use crate::reducer::state::{MaterializedPromptInput, PromptInputKind};

// Re-export all event category modules
mod planning;
pub use planning::PlanningEvent;

mod prompt_input;
pub use prompt_input::PromptInputEvent;

mod rebase;
pub use rebase::{ConflictStrategy, RebaseEvent, RebasePhase};

mod commit;
pub use commit::{CommitEvent, ProcessExecutionResult};

mod awaiting_dev_fix;
pub use awaiting_dev_fix::AwaitingDevFixEvent;

mod supporting_types;
pub use supporting_types::{
    default_timeout_output_kind, AgentErrorKind, CheckpointTrigger, TimeoutOutputKind,
};
