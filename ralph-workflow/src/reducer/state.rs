//! Pipeline state types for reducer architecture.
//!
//! Defines immutable state structures that capture complete pipeline execution context.
//! These state structures can be serialized as checkpoints for resume functionality.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

// Checkpoint types and PipelinePhase used by tests
#[cfg(test)]
use super::event::PipelinePhase;
#[cfg(test)]
use crate::checkpoint::{
    PipelineCheckpoint, PipelinePhase as CheckpointPhase, RebaseState as CheckpointRebaseState,
};

// State enums and basic types (ArtifactType, PromptMode, DevelopmentStatus, FixStatus, RebaseState, CommitState)
include!("state/enums.rs");

// Continuation state for development and fix iterations
pub mod continuation;
pub use continuation::ContinuationState;

// Agent fallback chain state and backoff computation
pub mod agent_chain;
pub use agent_chain::{AgentChainState, AgentRole, RateLimitContinuationPrompt};

// Run-level execution metrics
include!("state/metrics.rs");

// Pipeline state module (checkpoint payload and reducer state)
pub mod pipeline;
pub use pipeline::*;

// Tests
include!("state/tests.rs");
