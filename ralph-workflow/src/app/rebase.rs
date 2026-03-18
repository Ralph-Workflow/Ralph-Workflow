//! Rebase operations for the pipeline.
//!
//! This module contains functions for running pre-development rebase
//! and conflict resolution during the pipeline.

#[path = "rebase/types.rs"]
mod types;

#[path = "rebase/orchestration.rs"]
mod orchestration;

#[path = "rebase/conflicts.rs"]
mod conflicts;

pub use conflicts::try_resolve_conflicts_without_phase_ctx;
pub use orchestration::{run_initial_rebase, run_rebase_to_default};

pub use types::ConflictResolutionResult;
pub use types::InitialRebaseOutcome;
