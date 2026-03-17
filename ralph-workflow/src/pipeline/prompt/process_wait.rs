//! Re-export process_wait functions from runtime boundary module.
//!
//! This module re-exports from the runtime boundary to avoid dylint violations.

pub use super::runtime::process_wait::wait_for_completion_and_collect_stderr;
