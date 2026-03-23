// git_helpers/phase_state/io.rs — boundary module for process-global agent phase state.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

// Process-global state for the agent phase lifecycle.
//
// These statics hold path information set during `start_agent_phase` and cleared
// in `end_agent_phase_in_repo`. Signal handlers read them via `try_lock` to clean
// up without re-computing paths through libgit2.
//
// Defined here (not in the `runtime` boundary) so that non-boundary modules
// (`wrapper`) can import them without crossing a boundary dependency.

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
