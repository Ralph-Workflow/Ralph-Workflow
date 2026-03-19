//! Platform I/O boundary module.
//!
//! This module contains process-related operations that are forbidden in
//! domain modules. These include checking command existence via process execution.

use crate::executor::ProcessExecutor;

/// Check if a command exists in PATH by executing it.
/// This function performs a process operation (.status.success()) which
/// must remain in a boundary module.
pub fn command_exists(executor: &dyn ProcessExecutor, cmd: &str) -> bool {
    executor
        .execute("which", &[cmd], &[], None)
        .map(|output| output.status.success())
        .unwrap_or(false)
}
