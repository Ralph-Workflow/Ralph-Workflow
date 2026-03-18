//! Main event loop implementation.
//!
//! This module contains the core orchestrate-handle-reduce cycle that drives
//! the reducer-based pipeline. The event loop coordinates pure reducer logic
//! with impure effect handlers, maintaining strict separation of concerns.
//!
//! ## Event Loop Cycle
//!
//! ```text
//! State → Orchestrate → Effect → Handle → Event → Reduce → Next State
//!         (pure)                 (impure)         (pure)
//! ```
//!
//! The loop continues until reaching a terminal state (Interrupted, Completed)
//! or until max iterations is exceeded.
//!
//! ## Architecture
//!
//! The event loop is organized into several modules:
//! - `driver` - Main iteration loop implementing orchestrate→handle→reduce cycle
//! - `recovery` - Defensive completion and max iterations handling
//! - `error_handling` - Panic recovery and error routing
//! - `trace` - Trace buffer and diagnostic dumps
//! - `config` - Configuration and initialization

use super::config::{create_initial_state_with_config, EventLoopConfig, EventLoopResult};
use super::driver::run_event_loop_driver;
use crate::phases::PhaseContext;
use crate::reducer::{EffectHandler, MainEffectHandler, PipelineState};
use anyhow::Result;

/// Trait for handlers that maintain internal state.
///
/// This trait allows the event loop to update the handler's internal state
/// after each event is processed.
pub trait StatefulHandler {
    /// Update the handler's internal state.
    fn update_state(&mut self, state: PipelineState);
}

/// Run the main event loop for the reducer-based pipeline.
///
/// This function orchestrates pipeline execution by repeatedly:
/// 1. Determining the next effect based on the current state
/// 2. Executing the effect through the effect handler (which performs side effects)
/// 3. Applying the resulting event to state through the reducer (pure function)
/// 4. Repeating until a terminal state is reached or max iterations exceeded
///
/// The entire event loop is wrapped in panic recovery to ensure the pipeline
/// never crashes due to agent failures (panics only; aborts/segfaults cannot be recovered).
///
/// # Arguments
///
/// * `ctx` - Phase context for effect handlers
/// * `initial_state` - Optional initial state (if None, creates a new state)
/// * `config` - Event loop configuration
///
/// # Returns
///
/// Returns the event loop result containing the completion status and final state.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn run_event_loop(
    ctx: &mut PhaseContext<'_>,
    initial_state: Option<PipelineState>,
    config: EventLoopConfig,
) -> Result<EventLoopResult> {
    let state = initial_state.unwrap_or_else(|| create_initial_state_with_config(ctx));
    let mut handler = MainEffectHandler::new(state.clone());
    run_event_loop_driver(ctx, Some(state), config, &mut handler)
}

/// Run the event loop with a custom effect handler.
///
/// This variant allows injecting a custom effect handler for testing.
/// The handler must implement `EffectHandler` and `StatefulHandler` traits.
///
/// # Arguments
///
/// * `ctx` - Phase context for effect handlers
/// * `initial_state` - Optional initial state (if None, creates a new state)
/// * `config` - Event loop configuration
/// * `handler` - Custom effect handler (e.g., `MockEffectHandler` for testing)
///
/// # Returns
///
/// Returns the event loop result containing the completion status and final state.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn run_event_loop_with_handler<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    initial_state: Option<PipelineState>,
    config: EventLoopConfig,
    handler: &mut H,
) -> Result<EventLoopResult>
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    run_event_loop_driver(ctx, initial_state, config, handler)
}
