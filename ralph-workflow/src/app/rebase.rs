//! Rebase operations for the pipeline.
//!
//! This module contains functions for running pre-development rebase
//! and conflict resolution during the pipeline.

#[path = "rebase/types.rs"]
pub mod types;

pub mod orchestration;

pub mod conflicts;

pub mod boundary;

pub use orchestration::run_initial_rebase;

pub use types::ConflictResolutionResult;
pub use types::InitialRebaseOutcome;
