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

pub use orchestration::run_initial_rebase;

pub use types::ConflictResolutionResult;
pub use types::InitialRebaseOutcome;
