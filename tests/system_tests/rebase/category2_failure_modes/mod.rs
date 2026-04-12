//! # Category 2: Rebase Starts but Stops
//!
//! Tests for failure modes where rebase starts but stops in interrupted state:
//! - Content conflicts
//! - Patch application failures
//! - Interactive todo-driven stops
//! - Empty or redundant commits
//! - Autostash and stash reapplication failures
//! - Commit creation failures mid-rebase
//! - Reference update failures
//!
//! ## Test Organization
//!
//! - `basic_conflicts` - Basic conflict types (content, patch, add-add, modify-delete, binary)
//! - `advanced_conflicts` - Complex scenarios (rename conflicts, directory-file, symlinks, line endings)
//! - `hook_failures` - Hook rejection during rebase (pre-rebase and mid-rebase hooks)
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../../INTEGRATION_TESTS.md](../../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (rebase state, conflict markers)
//! - Uses `tempfile::TempDir` to mock at architectural boundary (filesystem)
//! - Tests are deterministic and isolated

use tempfile::TempDir;
use test_helpers::{commit_all, init_git_repo, write_file};

pub(crate) mod advanced_conflicts;
pub(crate) mod basic_conflicts;
pub(crate) mod hook_failures;

/// Initialize a git repository with an initial commit.
pub(crate) fn init_repo_with_initial_commit(dir: &TempDir) -> git2::Repository {
    let repo = init_git_repo(dir);
    write_file(dir.path().join("initial.txt"), "initial content");
    let _ = commit_all(&repo, "initial commit");
    repo
}

/// Helper to get the default branch name from the repository head.
pub(crate) fn get_default_branch_name(repo: &git2::Repository) -> String {
    repo.head()
        .ok()
        .and_then(|h| h.shorthand().map(std::string::ToString::to_string))
        .unwrap_or_else(|| "main".to_string())
}
