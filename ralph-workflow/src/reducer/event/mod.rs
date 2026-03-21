//! Pipeline event types for reducer architecture.
//!
//! Defines all possible events that can occur during pipeline execution.
//! Each event represents a **fact** about what happened, not a command about
//! what to do next.
//!
//! # Event-Sourced Reducer Architecture
//!
//! Ralph's pipeline follows an event-sourced reducer pattern:
//!
//! ```text
//! State → Orchestrate → Effect → Handle → Event → Reduce → State'
//! ```
//!
//! ## The Core Contract
//!
//! | Component | Pure? | Responsibility |
//! |-----------|-------|----------------|
//! | **Orchestration** | ✓ | Derives next effect from state only |
//! | **Handler** | ✗ | Executes effect, emits events describing outcome |
//! | **Reducer** | ✓ | Decides new state based on event |
//!
//! **Events are facts, not commands.**
//!
//! # Event Categories
//!
//! Events are organized into logical categories for type-safe routing to
//! category-specific reducers. Each category has a dedicated enum:
//!
//! - [`LifecycleEvent`] - Pipeline start/stop/resume
//! - [`PlanningEvent`] - Plan generation events
//! - [`DevelopmentEvent`] - Development iteration and continuation events
//! - [`ReviewEvent`] - Review pass and fix attempt events
//! - [`AgentEvent`] - Agent invocation and chain management events
//! - [`RebaseEvent`] - Git rebase operation events
//! - [`CommitEvent`] - Commit generation events
//! - [`AwaitingDevFixEvent`] - Dev-fix flow events
//! - [`PromptInputEvent`] - Prompt materialization events
//! - [`ErrorEvent`] - Typed error events from handlers
//!
//! The main [`PipelineEvent`] enum wraps these category enums to enable
//! type-safe dispatch in the reducer.
//!
//! # Frozen Policy
//!
//! Both [`LifecycleEvent`] and [`PipelineEvent`] are **FROZEN** - adding new variants
//! is prohibited. See their documentation for rationale and alternatives.
//!
//! # See Also
//!
//! - `docs/architecture/event-loop-and-reducers.md` - Detailed architecture doc
//! - `reducer::state_reduction` - Reducer implementations
//! - `reducer::orchestration` - Effect orchestration logic
//! - `reducer::handler` - Effect handler implementations

// These imports are used by constructors.rs (and included sub-files) via `super::`.
// Child modules can access private items from their parent module's scope.
use crate::agents::AgentRole;
use crate::reducer::state::DevelopmentStatus;
use std::path::PathBuf;

// ============================================================================
// Type Definitions Module
// ============================================================================

#[path = "types.rs"]
mod types;

// Re-export all type definitions
pub use types::{
    default_timeout_output_kind, AgentErrorKind, AwaitingDevFixEvent, CheckpointTrigger,
    CommitEvent, ConflictStrategy, LifecycleEvent, MaterializedPromptInput, PlanningEvent,
    ProcessExecutionResult, PromptInputEvent, PromptInputKind, RebaseEvent, RebasePhase,
    TimeoutOutputKind,
};

// ============================================================================
// Category Event Modules
// ============================================================================

#[path = "development.rs"]
mod development;
pub use development::DevelopmentEvent;

#[path = "review.rs"]
mod review;
pub use review::ReviewEvent;

#[path = "agent.rs"]
mod agent;
pub use agent::AgentEvent;

#[path = "error.rs"]
mod error;
pub use error::ErrorEvent;
pub use error::WorkspaceIoErrorKind;

// ============================================================================
// Constructor Module
// ============================================================================

#[path = "constructors.rs"]
mod constructors;

// ============================================================================
// Main Event Enum and Pipeline Phase
// ============================================================================

#[path = "pipeline_phase.rs"]
mod pipeline_phase;
pub use pipeline_phase::PipelinePhase;

#[path = "pipeline_event.rs"]
mod pipeline_event;
pub use pipeline_event::PipelineEvent;
