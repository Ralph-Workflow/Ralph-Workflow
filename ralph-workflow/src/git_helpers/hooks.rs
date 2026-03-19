//! Git hook installation and management.
//!
//! This module handles the lifecycle of Ralph-managed git hooks, including:
//!
//! - **Installation**: Creating `pre-commit`, `pre-push`, `pre-merge-commit`, and `commit-msg`
//!   hooks that block git operations during the agent phase. Hooks check both the enforcement
//!   marker (`<git-dir>/ralph/no_agent_commit`) and the wrapper track file
//!   (`<git-dir>/ralph/git-wrapper-dir.txt`), both embedded as absolute paths at install time.
//! - **Backup**: Preserving existing hooks as `.ralph.orig` files before overwriting
//! - **Restoration**: Restoring original hooks when uninstalling Ralph hooks
//!
//! Hooks are identified by a marker string (`RALPH_RUST_MANAGED_HOOK`) embedded
//! in the hook script, allowing safe detection and removal of Ralph-managed hooks
//! without affecting user-created hooks.
//!
//! Note: This module uses libgit2 (via the repo module) for locating the hooks
//! directory, avoiding CLI dependencies.
//!
//! # Architecture Note
//!
//! Hook installation uses `std::fs` directly rather than the `Workspace` trait.
//! This is acceptable per AGENTS.md because:
//!
//! 1. `.git/hooks/` is managed by git, not the workspace abstraction
//! 2. Hook installation is a bootstrap operation that occurs before pipeline execution
//! 3. Tests that need hook behavior use workspace-aware test utilities
//!    (`file_contains_marker_with_workspace`, `verify_hook_integrity_with_workspace`)
//!
//! The workspace abstraction is designed for files within the repository working
//! tree, not for git internals.

#[cfg(any(test, feature = "test-utils"))]
pub use super::install::{install_hook, install_hooks};
pub use super::install::{install_hooks_in_repo, HOOK_MARKER, RALPH_HOOK_NAMES};
pub use super::uninstall::{
    uninstall_hook, uninstall_hooks, uninstall_hooks_in_repo, uninstall_hooks_silent_at,
    uninstall_hooks_silent_in_hooks_dir,
};
pub use super::verify::{
    enforce_hook_permissions, reinstall_hooks_if_tampered, verify_hooks_removed,
};
#[cfg(any(test, feature = "test-utils"))]
pub use super::verify::{
    file_contains_marker_with_workspace, verify_hook_integrity_with_workspace,
};
