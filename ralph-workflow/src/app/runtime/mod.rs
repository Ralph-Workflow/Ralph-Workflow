//! Runtime boundary module for event loop and runtime state management.
//!
//! This module contains code that requires mutable state and imperative control flow,
//! specifically the event loop that coordinates reducers, effect handlers, and orchestration.
//! The dylint lints are relaxed here to allow `let mut` and `for` loops.

// Re-export modules that were moved to app/ level
pub use crate::app::cloud_progress;
pub use crate::app::config;
pub use crate::app::core;
pub use crate::app::driver;
pub use crate::app::error_handling;
pub use crate::app::iteration;
pub use crate::app::logging;
pub use crate::app::recovery;
pub use crate::app::trace;

// Re-export public API
pub use crate::app::config::{EventLoopConfig, EventLoopResult, MAX_EVENT_LOOP_ITERATIONS};
pub use crate::app::core::{run_event_loop, run_event_loop_with_handler, StatefulHandler};

// Re-export for internal use within app module
pub(crate) use crate::app::config::{
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
