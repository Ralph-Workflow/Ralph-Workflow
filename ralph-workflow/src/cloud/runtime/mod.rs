//! Runtime boundary for cloud heartbeat background task.
//!
//! This module contains the imperative thread-spawning code that cannot be
//! expressed functionally. The HeartbeatGuard spawns a background thread
//! to periodically send heartbeat signals to the cloud API.

pub mod heartbeat_worker;
