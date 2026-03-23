//! Runtime module for diagnostics - contains OS-boundary code.
//!
//! This module satisfies the dylint boundary-module check for code that uses
//! std::env for system information.

use std::path::PathBuf;

pub use crate::executor::ProcessExecutor;

/// Get the OS and architecture as a formatted string.
pub fn get_os_info() -> String {
    format!("{} {}", std::env::consts::OS, std::env::consts::ARCH)
}

/// Get the current working directory.
pub fn get_working_directory() -> std::io::Result<PathBuf> {
    std::env::current_dir()
}

/// Get the SHELL environment variable.
pub fn get_shell() -> Option<String> {
    std::env::var("SHELL").ok()
}

/// Get the architecture.
pub fn get_arch() -> &'static str {
    std::env::consts::ARCH
}

/// Check if the current directory is a git repository.
pub fn is_git_repo(executor: &dyn ProcessExecutor) -> bool {
    executor
        .execute("git", &["rev-parse", "--git-dir"], &[], None)
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Get the OS.
pub fn get_os() -> &'static str {
    std::env::consts::OS
}
