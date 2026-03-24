//! # Rebase Edge Case Tests
//!
//! System tests covering edge cases and corner scenarios in git rebase workflows.
//!
//! ## Test Categories
//!
//! - `noop_scenarios` - Basic scenarios that return `NoOp` (already on main, up-to-date, etc.)
//! - `validation` - Precondition validation tests for shallow clones, submodules, sparse checkout
//! - `conflict_scenarios` - Complex merge conflict resolution scenarios (binary files, symlinks, etc.)
//!
//! ## Coverage
//!
//! These tests use real git operations with temporary repositories to verify
//! rebase behavior under unusual conditions that integration tests cannot cover.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../../INTEGRATION_TESTS.md](../../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (rebase skip behavior)
//! - Uses `tempfile::TempDir` to mock at architectural boundary (filesystem)
//! - Tests are deterministic and isolated

use tempfile::TempDir;
use test_helpers::{commit_all, init_git_repo, write_file};

pub(crate) mod conflict_scenarios;
pub(crate) mod noop_scenarios;
pub(crate) mod validation;

/// Initialize a git repository with an initial commit.
///
/// This helper creates a repository with a single "initial.txt" file
/// to establish a baseline commit for testing rebase scenarios.
pub(crate) fn init_repo_with_initial_commit(dir: &TempDir) -> git2::Repository {
    let repo = init_git_repo(dir);
    write_file(dir.path().join("initial.txt"), "initial content");
    let _ = commit_all(&repo, "initial commit");
    repo
}

/// Helper to get the default branch name from the repository head.
///
/// Returns the shorthand name of the current branch, defaulting to "main"
/// if the branch cannot be determined.
pub(crate) fn get_default_branch_name(repo: &git2::Repository) -> String {
    repo.head()
        .ok()
        .and_then(|h| h.shorthand().map(std::string::ToString::to_string))
        .unwrap_or_else(|| "main".to_string())
}
