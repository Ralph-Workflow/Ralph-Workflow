//! Workspace operation helpers in the I/O boundary.
//!
//! This module provides helpers for working with the Workspace trait.

use crate::workspace::Workspace;

/// Check if a path exists in the workspace.
pub fn path_exists(workspace: &dyn Workspace, path: &std::path::Path) -> bool {
    workspace.exists(path)
}

/// Check if a path is a file in the workspace.
pub fn is_file(workspace: &dyn Workspace, path: &std::path::Path) -> bool {
    workspace.is_file(path)
}

/// Check if a path is a directory in the workspace.
pub fn is_dir(workspace: &dyn Workspace, path: &std::path::Path) -> bool {
    workspace.is_dir(path)
}
