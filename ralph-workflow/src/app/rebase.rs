//! Rebase operations for the pipeline.
//!
//! This module contains functions for running pre-development rebase
//! and conflict resolution during the pipeline.

#[path = "rebase/types.rs"]
pub(crate) mod types;

pub(crate) mod orchestration;

pub(crate) mod conflicts;

pub(crate) mod boundary;

pub(crate) use orchestration::run_initial_rebase;

pub(crate) use types::ConflictResolutionResult;
pub(crate) use types::InitialRebaseOutcome;
