//! Subprocess termination helpers for idle-timeout enforcement.
//!
//! This module re-exports from the runtime boundary module.

pub use crate::pipeline::idle_timeout::runtime::kill::{KillConfig, DEFAULT_KILL_CONFIG};
