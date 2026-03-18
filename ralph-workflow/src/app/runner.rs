//! Application entrypoint and pipeline orchestration.
//!
//! This module is the CLI layer operating **before** the repository root is known.
//! It uses [`AppEffect`][effect::AppEffect] for side effects, which is distinct from
//! [`Effect`][crate::reducer::effect::Effect] used after repo root discovery.
//!
//! # Two Effect Layers
//!
//! Ralph has two distinct effect types (see also [`crate`] documentation):
//!
//! | Layer | When | Filesystem Access |
//! |-------|------|-------------------|
//! | `AppEffect` (this module) | Before repo root known | `std::fs` directly |
//! | `Effect` ([`crate::reducer`]) | After repo root known | Via [`Workspace`][crate::workspace::Workspace] |
//!
//! These layers must never mix: `AppEffect` handlers cannot use `Workspace`.
//!
//! # Responsibilities
//!
//! - CLI/config parsing and plumbing commands
//! - Agent registry loading
//! - Repo root discovery
//! - Resume support and checkpoint management
//! - Transition to pipeline execution via `crate::phases`
//!
//! # Module Structure
//!
//! - [`config_init`]: Configuration loading and agent registry initialization
//! - [`effect`]: `AppEffect` definitions for pre-repo-root operations
//! - [`effect_handler`]: Production handler for `AppEffect` execution
//! - [`plumbing`]: Low-level git operations (show/apply commit messages)
//! - [`validation`]: Agent validation and chain validation
//! - [`resume`]: Checkpoint resume functionality
//! - [`detection`]: Project stack detection
//! - [`finalization`]: Pipeline cleanup and finalization

// Include sub-modules
pub mod command_handlers;
pub mod pipeline_execution;
pub mod setup_helpers;
#[cfg(test)]
pub mod tests;

// Re-exports from pipeline_execution
pub use pipeline_execution::run;

// Re-exports from pipeline_execution (helpers is included via include!)
pub use pipeline_execution::CommandExitCleanupGuard;

// Re-exports from pipeline_execution (initialization is included via include!)
pub use pipeline_execution::PipelinePreparationParams;

// Re-exports from setup_helpers
pub use setup_helpers::{validate_and_setup_agents, AgentSetupParams};
