//! Event loop for reducer-based pipeline architecture.
//!
//! This module implements the main event loop that coordinates reducer,
//! effect handlers, and orchestration logic. It provides a unified way to
//! run the pipeline using the event-sourced architecture from RFC-004.
//!
//! # Non-Terminating Pipeline Architecture
//!
//! The pipeline is designed to be **non-terminating by default** for unattended operation.
//! It must NEVER exit early due to internal failures, budget exhaustion, or agent errors.
//!
//! ## Failure Handling Flow
//!
//! 1. Any terminal failure (Status: Failed, budget exhausted, agent chain exhausted)
//!    transitions to `AwaitingDevFix` phase
//! 2. `TriggerDevFixFlow` effect writes completion marker to `.agent/tmp/completion_marker`
//! 3. Dev-fix agent is optionally dispatched for remediation attempt
//! 4. `CompletionMarkerEmitted` event transitions to `Interrupted` phase
//! 5. `SaveCheckpoint` effect saves state for resume
//! 6. Event loop returns `EventLoopResult { completed: true, ... }`
//!
//! ## Acceptable Termination Reasons
//!
//! The ONLY acceptable reasons for pipeline termination are catastrophic external events:
//! - Process termination (SIGKILL)
//! - Machine outage / power loss
//! - OS kill signal
//! - Unrecoverable panic in effect handler (caught and logged)
//!
//! All internal errors route through the failure handling flow above.
//!
//! # Module Organization
//!
//! - `config` - Event loop configuration and initialization
//! - `trace` - Event trace buffer and diagnostics
//! - `error_handling` - Panic recovery and error extraction
//! - `driver` - Main iteration loop implementing orchestrate→handle→reduce cycle
//! - `recovery` - Defensive completion and max iterations handling
//! - `core` - Public API and convenience functions

mod cloud_progress;
mod config;
mod core;
mod driver;
mod error_handling;
mod iteration;
mod logging;
mod recovery;
mod trace;

// Re-export public API
pub use config::{EventLoopConfig, EventLoopResult, MAX_EVENT_LOOP_ITERATIONS};
pub use core::{run_event_loop, run_event_loop_with_handler, StatefulHandler};

// Re-export for internal use within app module
pub(crate) use config::{
    create_initial_state_with_config, overlay_checkpoint_progress_onto_base_state,
};

#[cfg(test)]
mod tests_checkpoint;
#[cfg(test)]
mod tests_iteration_control;
#[cfg(test)]
mod tests_review_flow;
#[cfg(test)]
mod tests_trace_dump;
