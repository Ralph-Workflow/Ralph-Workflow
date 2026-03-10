//! Tests for git helper functions.
//!
//! Tests that require real git repositories have been moved to `tests/system_tests/git`/.
//! This module contains only tests that can use `MemoryWorkspace`.

use super::*;
use crate::workspace::{MemoryWorkspace, Workspace};
use std::path::Path;

#[test]
fn test_marker_file_operations() {
    // Test marker file operations using MemoryWorkspace
    // The marker now lives at .git/ralph/no_agent_commit (inside git metadata dir)
    let workspace = MemoryWorkspace::new_test();
    let marker_path = Path::new(".git/ralph/no_agent_commit");

    // Create marker using workspace.
    workspace.write(marker_path, "").unwrap();
    assert!(workspace.exists(marker_path));

    // Remove marker using workspace.
    workspace.remove(marker_path).unwrap();
    assert!(!workspace.exists(marker_path));
}

#[test]
fn test_git_helpers_new() {
    let _helpers = GitHelpers::new();
}
