//! Runtime module for git_helpers - contains OS-boundary code.
//!
//! This module satisfies the dylint boundary-module check for code that uses
//! interior mutability (Mutex) for cross-thread/process communication.

pub mod agent_phase_state;
pub mod cleanup;
pub mod config_state;
pub mod hooks;
pub mod hooks_dir;
pub mod identity;
pub mod install;
pub mod lock;
pub mod marker;
pub mod path_wrapper;
pub mod phase;
pub mod script;
pub mod uninstall;
pub mod verify;
pub mod worktree;
pub mod wrapper;

pub use agent_phase_state::{AGENT_PHASE_HOOKS_DIR, AGENT_PHASE_RALPH_DIR, AGENT_PHASE_REPO_ROOT};
pub use hooks::{
    enforce_hook_permissions, install_hooks_in_repo, reinstall_hooks_if_tampered, uninstall_hook,
    uninstall_hooks, uninstall_hooks_in_repo, uninstall_hooks_silent_at,
    uninstall_hooks_silent_in_hooks_dir, verify_hooks_removed, HOOK_MARKER, RALPH_HOOK_NAMES,
};
#[cfg(any(test, feature = "test-utils"))]
pub use hooks::{
    file_contains_marker_with_workspace, install_hook, install_hooks,
    verify_hook_integrity_with_workspace,
};
pub use identity::{get_system_hostname, get_system_username};

pub use wrapper::{
    capture_head_oid, cleanup_agent_phase_protections_silent_at, cleanup_agent_phase_silent,
    cleanup_agent_phase_silent_at, cleanup_orphaned_marker, cleanup_orphaned_wrapper_at,
    clear_agent_phase_global_state, detect_unauthorized_commit, disable_git_wrapper,
    end_agent_phase, end_agent_phase_in_repo, ensure_agent_phase_protections, start_agent_phase,
    start_agent_phase_in_repo, try_remove_ralph_dir, verify_ralph_dir_removed,
    verify_wrapper_cleaned, GitHelpers, ProtectionCheckResult,
};
