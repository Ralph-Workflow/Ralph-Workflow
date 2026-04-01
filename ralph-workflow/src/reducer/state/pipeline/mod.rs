//! Pipeline state module.
//!
//! This module defines the `PipelineState` struct - the single source of truth
//! for pipeline execution progress. It serves dual purposes:
//!
//! 1. **Runtime State**: Tracks current phase, iteration counters, agent chain state,
//!    and all progress flags that orchestration uses to determine next effects
//!
//! 2. **Checkpoint Payload**: Serializes to JSON for resume functionality, allowing
//!    interrupted pipelines to restart from the exact point of interruption
//!
//! # Architecture
//!
//! ## State Organization by Concern
//!
//! The state is organized into logical groups:
//!
//! - **Core counters**: `phase`, `iteration`, `reviewer_pass`, limits
//! - **Phase-specific progress flags**: Track completion of individual effects
//!   (e.g., `planning_prompt_prepared_iteration`, `development_xml_extracted_iteration`)
//! - **Validated outcomes**: Structured data from parsed/validated agent outputs
//!   (e.g., `PlanningValidatedOutcome`, `DevelopmentValidatedOutcome`)
//! - **Agent chain state**: Tracks fallback progression and retry budgets
//! - **Continuation state**: Manages retry/continuation logic across iterations
//! - **Metrics**: Run-level statistics for observability
//! - **Prompt inputs**: Materialized (truncated/referenced) inputs after oversize handling
//!
//! ## Reducer Contract
//!
//! **CRITICAL**: `PipelineState` is IMMUTABLE from the reducer's perspective.
//!
//! - State transitions happen ONLY through the `reduce` function
//! - Effects observe state but never mutate it directly
//! - All mutations flow through `Event` → `reduce` → new `PipelineState`
//!
//! This immutability enables:
//! - Deterministic state transitions (same events = same state)
//! - Testable reducers (pure functions)
//! - Reliable checkpoint/resume (state is always consistent)
//!
//! ## Checkpoint/Resume Semantics
//!
//! When a checkpoint is loaded:
//! - Counters (`iteration`, `reviewer_pass`) are restored
//! - All progress flags are reset to `None`
//! - Orchestration re-evaluates which effects to run based on counters + flags
//!
//! This ensures that interrupted work is safely re-executed rather than skipped.
//!
//! # Module Structure
//!
//! - `bounded_execution_history`: `BoundedExecutionHistory` newtype (ring-buffer API)
//! - `core_state`: Main `PipelineState` struct definition
//! - `core_state_methods`: `PipelineState` constructor and accessor methods
//! - `phase_fields`: Phase-specific outcome types and prompt input structures
//! - `helpers`: Pure query methods (e.g., `is_complete()`, `current_head()`)
//! - `checkpoint_conversion`: Conversion from checkpoint format to runtime state
//!
//! # See Also
//!
//! - `reducer::state_reduction` for state transition logic
//! - `reducer::orchestration` for effect sequencing based on state
//! - `reducer::event` for event types that drive state changes

use crate::checkpoint::execution_history::ExecutionStep;
use crate::checkpoint::{
    PipelineCheckpoint, PipelinePhase as CheckpointPhase, RebaseState as CheckpointRebaseState,
};
use crate::reducer::event::PipelinePhase;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

use super::{
    AgentChainState, CommitState, ConnectivityState, ContinuationState, DevelopmentStatus,
    FixStatus, MaterializedPromptInput, RebaseState, RunMetrics,
};

mod prompt_permissions;
pub use prompt_permissions::PromptPermissionsState;

// Phase-specific validated outcome types
include!("phase_fields.rs");

// Bounded execution history newtype
include!("bounded_execution_history.rs");

// Main PipelineState struct
include!("core_state.rs");

// PipelineState constructor and accessor methods
include!("core_state_methods.rs");

// Helper methods for state queries
include!("helpers.rs");

// Checkpoint to state conversion
include!("checkpoint_conversion.rs");
