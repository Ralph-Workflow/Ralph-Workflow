//! File operations in the I/O boundary.
//!
//! This module provides file operation helpers that bridge domain code
//! with the filesystem. All functions here may use mutation and I/O.

use crate::workspace::Workspace;

/// Read a file's contents using the workspace.
pub fn read_workspace_file(
    workspace: &dyn Workspace,
    path: &std::path::Path,
) -> std::io::Result<String> {
    workspace.read(path)
}

/// Write contents to a file using the workspace.
pub fn write_workspace_file(
    workspace: &dyn Workspace,
    path: &std::path::Path,
    contents: &str,
) -> std::io::Result<()> {
    workspace.write(path, contents)
}
