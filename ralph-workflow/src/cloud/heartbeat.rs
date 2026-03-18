//! Heartbeat background task for cloud mode.
//!
//! This module re-exports from the runtime boundary module.

pub use crate::cloud::runtime::heartbeat_worker::HeartbeatGuard;
