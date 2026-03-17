//! Process executor utilities in the runtime boundary.
//!
//! This module re-exports the main process executor types for use
//! in boundary composition.

pub use crate::executor::{ProcessExecutor, ProcessOutput};

/// Trait for process execution, allowing testability.
pub use crate::executor::ProcessExecutor as Executor;
