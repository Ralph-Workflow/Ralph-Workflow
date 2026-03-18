//! Runtime boundary module for event loop and runtime state management.
//!
//! This module contains code that requires mutable state and imperative control flow,
//! specifically the event loop that coordinates reducers, effect handlers, and orchestration.
//! The dylint lints are relaxed here to allow `let mut` and `for` loops.

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
