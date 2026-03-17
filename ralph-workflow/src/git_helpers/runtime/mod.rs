//! Runtime module for git_helpers - contains OS-boundary code.
//
//! This module satisfies the dylint boundary-module check for code that uses
//! interior mutability (Mutex) for cross-thread/process communication.

pub mod agent_phase_state;
pub mod hooks;
pub mod identity;
pub mod lock;
pub mod wrapper;

pub use agent_phase_state::{AGENT_PHASE_HOOKS_DIR, AGENT_PHASE_RALPH_DIR, AGENT_PHASE_REPO_ROOT};
pub use identity::{get_system_hostname, get_system_username};

// Re-export wrapper public API
pub use wrapper::{
    capture_head_oid, cleanup_agent_phase_protections_silent_at, cleanup_agent_phase_silent,
    cleanup_agent_phase_silent_at, cleanup_orphaned_marker, cleanup_orphaned_wrapper_at,
    clear_agent_phase_global_state, detect_unauthorized_commit, disable_git_wrapper,
    end_agent_phase, end_agent_phase_in_repo, ensure_agent_phase_protections, start_agent_phase,
    start_agent_phase_in_repo, try_remove_ralph_dir, verify_ralph_dir_removed,
    verify_wrapper_cleaned, GitHelpers, ProtectionCheckResult,
};
