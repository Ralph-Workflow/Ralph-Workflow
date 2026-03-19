//! Main event loop driver implementing the orchestrate-handle-reduce cycle.
//!
//! This module contains the core iteration logic that repeatedly:
//! 1. Determines the next effect from current state (orchestration)
//! 2. Executes the effect through the handler (side effects)
//! 3. Applies the resulting event through the reducer (pure state transition)
//! 4. Repeats until terminal state or max iterations
//!
//! ## Event Loop Architecture
//!
//! ```text
//! State → determine_next_effect → Effect → execute → Event → reduce → Next State
//!         (pure, from orchestrator)       (impure)          (pure)
//! ```
//!
//! The loop maintains strict separation between pure reducer logic and impure
//! effect handlers, with all state transitions driven by events.

use crate::phases::PhaseContext;
use crate::reducer::{EffectHandler, PipelineState};
use anyhow::Result;

use crate::app::config::{EventLoopConfig, EventLoopResult};
use crate::app::core::StatefulHandler;

pub(super) fn run_event_loop_driver<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    initial_state: Option<PipelineState>,
    config: EventLoopConfig,
    handler: &mut H,
) -> Result<EventLoopResult>
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    crate::app::runtime::run_event_loop_driver(ctx, initial_state, config, handler)
}
