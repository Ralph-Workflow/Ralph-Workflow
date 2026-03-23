//! Resume functionality for pipeline checkpoints.
//!
//! This module handles the --resume flag and checkpoint loading logic,
//! including validation and state restoration.

/// Length for shortened git OID display (e.g., "a1b2c3d4").
const SHORT_OID_LENGTH: usize = 8;

use crate::agents::AgentRegistry;
use crate::checkpoint::file_state::{FileSystemState, ValidationError};
use crate::checkpoint::{
    checkpoint_exists_with_workspace, load_checkpoint_with_workspace, validate_checkpoint,
    PipelineCheckpoint, PipelinePhase,
};
use crate::config::Config;
use crate::git_helpers::rebase_in_progress;
use crate::logger::Logger;
use crate::workspace::Workspace;

use std::path::Path;

/// Result of handling resume, containing the checkpoint.
pub struct ResumeResult {
    /// The loaded checkpoint.
    pub checkpoint: PipelineCheckpoint,
}

// Sub-modules organized by responsibility
include!("resume/validation.rs");
include!("resume/display_helpers.rs");
include!("resume/display_summary.rs");
include!("resume/display_detailed.rs");
include!("resume/interactive_prompt.rs");
include!("resume/validation_handlers.rs");

#[cfg(test)]
include!("resume/tests.rs");
