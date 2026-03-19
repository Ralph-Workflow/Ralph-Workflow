//! Runtime primitives for git_helpers boundary module.
//!
//! This module contains process-global state that requires interior mutability,
//! specifically static Mutex declarations used for cross-thread communication
//! between the main thread and signal handlers.

use std::path::PathBuf;
use std::sync::Mutex;

/// Process-global repo root set during `start_agent_phase` for signal handler fallback.
///
/// The signal handler needs a reliable repo root when CWD-based discovery may fail.
/// This is set in `start_agent_phase` and cleared in `end_agent_phase_in_repo`.
pub static AGENT_PHASE_REPO_ROOT: Mutex<Option<PathBuf>> = Mutex::new(None);

/// Process-global ralph git dir set during `start_agent_phase_in_repo`.
///
/// Signal handlers cannot call libgit2, so we pre-compute the ralph dir path
/// on the main thread and store it here. Signal handlers read via `try_lock`.
pub static AGENT_PHASE_RALPH_DIR: Mutex<Option<PathBuf>> = Mutex::new(None);

/// Process-global hooks dir set during `start_agent_phase_in_repo`.
///
/// Used by signal handler cleanup to avoid recomputation via libgit2.
/// For linked worktrees, hooks are worktree-scoped, so this ensures the signal
/// handler cleans the active worktree's hooks instead of touching siblings.
pub static AGENT_PHASE_HOOKS_DIR: Mutex<Option<PathBuf>> = Mutex::new(None);

#[cfg(any(test, feature = "test-utils"))]
#[must_use]
pub fn agent_phase_test_lock() -> &'static std::sync::Mutex<()> {
    static TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    &TEST_LOCK
}
