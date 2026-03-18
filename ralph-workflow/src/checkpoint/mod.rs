//! Pipeline checkpoint system for resume functionality.
//!
//! This module provides checkpoint management for Ralph's pipeline:
//! - Save and load pipeline state
//! - Resume interrupted pipelines
//! - Track pipeline phase progress
//! - Validate checkpoints before resuming
//! - Restore configuration from checkpoints
//!
//! # Example
//!
//! ```ignore
//! use ralph::checkpoint::{
//!     CheckpointBuilder, PipelineCheckpoint, PipelinePhase,
//!     save_checkpoint_with_workspace, load_checkpoint_with_workspace,
//! };
//!
//! // Create a checkpoint using the builder
//! let checkpoint = CheckpointBuilder::new()
//!     .phase(PipelinePhase::Development, 2, 5)
//!     .reviewer_pass(0, 2)
//!     .agents("claude", "codex")
//!     .capture_from_context(&config, &registry, "claude", "codex", &logger)
//!     .build()
//!     .expect("checkpoint should build");
//!
//! save_checkpoint_with_workspace(&workspace, &checkpoint)?;
//!
//! // Load and resume
//! if let Some(checkpoint) = load_checkpoint_with_workspace(&workspace)? {
//!     println!("Resuming from: {}", checkpoint.description());
//! }
//! ```

pub mod builder;
pub mod execution_history;
pub mod file_state;
pub mod recovery;
pub mod restore;
pub mod run_context;
pub mod runtime;
pub mod size_monitor;
pub mod state;
pub mod string_pool;
pub mod validation;

pub use builder::CheckpointBuilder;
pub use execution_history::ExecutionHistory;
pub use file_state::FileSystemState;
pub use restore::apply_checkpoint_to_config;
pub use run_context::RunContext;
pub use size_monitor::{CheckpointSizeMonitor, SizeAlert, SizeThresholds};
pub use state::{
    calculate_file_checksum_with_workspace, checkpoint_exists_with_workspace,
    clear_checkpoint_with_workspace, load_checkpoint_with_workspace,
    save_checkpoint_with_workspace, timestamp, PipelineCheckpoint, PipelinePhase, RebaseState,
};
pub use string_pool::StringPool;
pub use validation::validate_checkpoint;
