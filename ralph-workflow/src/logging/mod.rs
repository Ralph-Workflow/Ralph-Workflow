pub mod io;
mod run_id;
mod run_log_context;
/// Per-run logging infrastructure.
///
/// This module provides types and utilities for organizing all logs from a single
/// pipeline run under a per-run directory (.agent/logs-<`run_id`>/).
///
/// Key types:
/// - `RunId`: Unique timestamp-based identifier for each pipeline run
/// - `RunLogContext`: Manages log directory creation and path resolution
/// - `EventLoopLogger`: Records event loop execution for diagnosability
pub mod runtime;

pub use io::event_loop_logger::{EventLoopLogger, LogEffectParams};
pub use run_id::RunId;
pub use run_log_context::{ConfigSummary, RunLogContext, RunMetadata};
