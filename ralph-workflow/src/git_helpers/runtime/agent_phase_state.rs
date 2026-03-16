//! Agent phase state - process-global state for git wrapper enforcement.
//!
//! This module contains the process-global state used by the git wrapper to track
//! the agent phase. These are intentionally placed in a runtime/ boundary module
//! because they use interior mutability (Mutex) for cross-thread communication
//! between the main thread and signal handlers.

use std::path::PathBuf;
use std::sync::Mutex;

/// Process-global repo root set during `start_agent_phase` for signal handler fallback.
///
/// The signal handler needs a reliable repo root when CWD-based discovery may fail.
/// This is set in `start_agent_phase` and cleared in `end_agent_phase_in_repo`.
/// The signal handler uses `try_lock` to avoid deadlock risk.
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
