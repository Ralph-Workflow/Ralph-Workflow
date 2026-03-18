//! Agent spawning for prompt execution.
//!
//! This module re-exports agent spawning functionality from the runtime boundary module.

pub(crate) use crate::pipeline::prompt::runtime::agent_spawn::run_with_agent_spawn;
