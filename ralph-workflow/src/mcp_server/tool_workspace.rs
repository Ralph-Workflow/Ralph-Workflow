//! Workspace tool handlers for MCP server.
//!
//! Provides handlers for file read, write, list, and search operations
//! with proper capability checking and edit area enforcement for parallel workers.

use crate::agents::session::{AgentSession, Capability, PolicyOutcome};
use crate::workspace::{DirEntry, Workspace};
use mcp_server::dispatch::registry::ToolError;
use mcp_server::protocol::types::{ToolContent, ToolResult};
use std::path::Path;

fn required_string_param<'a>(
    params: &'a serde_json::Value,
    name: &str,
) -> Result<&'a str, ToolError> {
    params
        .get(name)
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams(format!("Missing '{name}' parameter")))
}

/// Read a file from the workspace by path.
///
/// # Method Identifier
///
/// `read_file`
///
/// # Capability Requirements
///
/// Requires: `McpCapability::WorkspaceRead` — available to all drain types.
///
/// # Access Mode
///
/// ReadOnly-safe. This tool is available in both ReadOnly and ReadWrite configurations.
///
/// # Request Shape
///
/// ```json
/// {"path": "src/main.rs"}
/// ```
///
/// ## Required Fields
///
/// - `path` (`string`): Workspace-relative file path to read.
///
/// # Response Shape
///
/// ```json
/// {"content": [{"type": "text", "text": "<file contents>"}], "isError": false}
/// ```
///
/// # Error Codes
///
/// - JSON-RPC `-32000` (Tool error): File not found or unreadable.
/// - JSON-RPC `-32000` (InvalidParams): Missing `path` parameter.
///
/// # Side Effects
///
/// None. This is a read-only operation.
///
/// # Idempotency
///
/// Fully idempotent.
pub fn handle_read_file(
    _session: &AgentSession,
    workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    let path = params
        .get("path")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'path' parameter".to_string()))?;

    let relative_path = Path::new(path);
    let content = workspace
        .read(relative_path)
        .map_err(|e| ToolError::ExecutionError(format!("Failed to read file '{}': {}", path, e)))?;

    Ok(ToolResult {
        content: vec![ToolContent::text(content)],
        is_error: Some(false),
    })
}

/// List directory entries (non-recursive) and return formatted output.
fn list_dir_flat(workspace: &dyn Workspace, path: &str) -> Result<String, ToolError> {
    let entries = workspace.read_dir(Path::new(path)).map_err(|e| {
        ToolError::ExecutionError(format!("Failed to list directory '{}': {}", path, e))
    })?;
    let mut output = format!("Directory: {}\n", path);
    for entry in entries {
        let entry_type = if entry.is_dir() { "[DIR]" } else { "[FILE]" };
        output.push_str(&format!("  {} {}\n", entry_type, entry.path().display()));
    }
    Ok(output)
}

/// Append one directory entry to the output string, recursing into subdirs.
fn append_dir_entry(
    workspace: &dyn Workspace,
    entry: &DirEntry,
    output: &mut String,
    indent: &str,
    depth: usize,
) -> Result<(), ToolError> {
    let entry_type = if entry.is_dir() { "[DIR]" } else { "[FILE]" };
    output.push_str(&format!(
        "{}{} {}\n",
        indent,
        entry_type,
        entry.path().display()
    ));
    if entry.is_dir() {
        walk_directory_recursive(workspace, entry.path(), output, depth + 1)?;
    }
    Ok(())
}

fn walk_directory_recursive(
    workspace: &dyn Workspace,
    dir: &Path,
    output: &mut String,
    depth: usize,
) -> Result<(), ToolError> {
    let indent = "  ".repeat(depth);
    let entries = workspace.read_dir(dir).map_err(|e| {
        ToolError::ExecutionError(format!(
            "Failed to read directory '{}': {}",
            dir.display(),
            e
        ))
    })?;
    entries
        .iter()
        .try_for_each(|e| append_dir_entry(workspace, e, output, &indent, depth))
}

/// List directory entries (non-recursive by default).
///
/// # Method Identifier
///
/// `list_directory`
///
/// # Capability Requirements
///
/// Requires: `McpCapability::WorkspaceRead` — available to all drain types.
///
/// # Access Mode
///
/// ReadOnly-safe.
///
/// # Request Shape
///
/// ```json
/// {"path": "src", "recursive": false}
/// ```
///
/// ## Required Fields
///
/// - `path` (`string`): Workspace-relative directory path to list.
///
/// ## Optional Fields
///
/// - `recursive` (`bool`, default `false`): If `true`, lists the full tree recursively.
///   For deep trees, prefer `list_directory_recursive`.
///
/// # Response Shape
///
/// ```json
/// {"content": [{"type": "text", "text": "Directory: src\n  main.rs\n  lib.rs\n"}], "isError": false}
/// ```
///
/// # Error Codes
///
/// - JSON-RPC `-32000` (Tool error): Directory not found or unreadable.
///
/// # Side Effects
///
/// None. Read-only.
///
/// # Idempotency
///
/// Fully idempotent.
pub fn handle_list_directory(
    _session: &AgentSession,
    workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    let path = params
        .get("path")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'path' parameter".to_string()))?;
    let recursive = params
        .get("recursive")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let output = if !recursive {
        list_dir_flat(workspace, path)?
    } else {
        let mut out = format!("Directory (recursive): {}\n", path);
        walk_directory_recursive(workspace, Path::new(path), &mut out, 0)?;
        out
    };
    Ok(ToolResult {
        content: vec![ToolContent::text(output)],
        is_error: Some(false),
    })
}

/// List directory contents recursively.
///
/// # Method Identifier
///
/// `list_directory_recursive`
///
/// # Capability Requirements
///
/// Requires: `McpCapability::WorkspaceRead` — available to all drain types.
///
/// # Access Mode
///
/// ReadOnly-safe.
///
/// # Request Shape
///
/// ```json
/// {"path": "src"}
/// ```
///
/// ## Required Fields
///
/// - `path` (`string`): Workspace-relative directory path to traverse recursively.
///
/// # Response Shape
///
/// Indented tree representation of the full directory subtree.
///
/// # Error Codes
///
/// - JSON-RPC `-32000` (Tool error): Directory not found or unreadable.
///
/// # Side Effects
///
/// None. Read-only.
///
/// # Idempotency
///
/// Fully idempotent.
pub fn handle_list_directory_recursive(
    _session: &AgentSession,
    workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    let path = params
        .get("path")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'path' parameter".to_string()))?;
    let mut output = format!("Directory (recursive): {}\n", path);
    walk_directory_recursive(workspace, Path::new(path), &mut output, 0)?;
    Ok(ToolResult {
        content: vec![ToolContent::text(output)],
        is_error: Some(false),
    })
}

/// Check if a filename matches the given search pattern.
fn filename_matches_pattern(path: &Path, pattern: &str) -> bool {
    let filename = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
    filename.contains(pattern) || pattern == "*"
}

/// Collect matching file paths from directory entries.
fn collect_matching_files(entries: impl Iterator<Item = DirEntry>, pattern: &str) -> Vec<String> {
    entries
        .filter(|e| e.is_file())
        .filter(|e| filename_matches_pattern(e.path(), pattern))
        .map(|e| format!("  {}\n", e.path().display()))
        .collect()
}

/// Search for files matching a filename pattern.
///
/// # Method Identifier
///
/// `search_files`
///
/// # Capability Requirements
///
/// Requires: `McpCapability::WorkspaceRead` — available to all drain types.
///
/// # Access Mode
///
/// ReadOnly-safe.
///
/// # Request Shape
///
/// ```json
/// {"pattern": "*.rs", "path": "src"}
/// ```
///
/// ## Required Fields
///
/// - `pattern` (`string`): Filename pattern to match. Matches by substring or `"*"` for all.
/// - `path` (`string`): Workspace-relative directory to search.
///
/// # Response Shape
///
/// ```json
/// {"content": [{"type": "text", "text": "Search pattern: '*.rs' in path: src\nFiles found:\n  src/main.rs\n"}], "isError": false}
/// ```
///
/// # Note
///
/// This handler matches file names only (not file contents). For content search,
/// use `exec` with `grep` or `rg`.
///
/// # Error Codes
///
/// - JSON-RPC `-32000` (Tool error): Directory not found or unreadable.
///
/// # Side Effects
///
/// None. Read-only.
///
/// # Idempotency
///
/// Fully idempotent.
///
/// Build search results output from matching files in a directory.
fn build_search_output(
    workspace: &dyn Workspace,
    pattern: &str,
    path: &str,
) -> Result<String, ToolError> {
    let entries = workspace.read_dir(Path::new(path)).map_err(|e| {
        ToolError::ExecutionError(format!("Failed to read directory '{}': {}", path, e))
    })?;
    let mut output = format!(
        "Search pattern: '{}' in path: {}\nFiles found:\n",
        pattern, path
    );
    for line in collect_matching_files(entries.into_iter(), pattern) {
        output.push_str(&line);
    }
    output.push_str("\nNote: Use exec with grep for actual content search");
    Ok(output)
}

pub fn handle_search_files(
    _session: &AgentSession,
    workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    let pattern = params
        .get("pattern")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'pattern' parameter".to_string()))?;
    let path = params
        .get("path")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'path' parameter".to_string()))?;
    let output = build_search_output(workspace, pattern, path)?;
    Ok(ToolResult {
        content: vec![ToolContent::text(output)],
        is_error: Some(false),
    })
}

/// Determine whether a file path is treated as git-tracked.
fn is_path_git_tracked(workspace: &dyn Workspace, path: &str) -> bool {
    workspace.exists(Path::new(path))
        && !path.contains(".agent/")
        && !path.contains("/target/")
        && !path.contains("node_modules/")
}

/// Check edit area restriction for parallel workers.
fn check_edit_area_restriction(session: &AgentSession, path: &str) -> Result<(), ToolError> {
    if !session.is_parallel_worker() {
        return Ok(());
    }
    let outcome = session.check_edit_area(path);
    if matches!(outcome, PolicyOutcome::Approved) {
        Ok(())
    } else {
        Err(ToolError::CapabilityDenied(format!(
            "Write to '{}' denied: edit area restriction",
            path
        )))
    }
}

/// Select the required write capability based on whether the path is tracked.
fn write_capability_for(is_tracked: bool) -> Capability {
    if is_tracked {
        Capability::WorkspaceWriteTracked
    } else {
        Capability::WorkspaceWriteEphemeral
    }
}

/// Check that the session has write capability appropriate for the file's tracked status.
fn check_write_capability(
    session: &AgentSession,
    path: &str,
    is_tracked: bool,
) -> Result<(), ToolError> {
    let cap = write_capability_for(is_tracked);
    let outcome = session.check_capability(cap);
    if matches!(outcome, PolicyOutcome::Approved) {
        return Ok(());
    }
    Err(ToolError::CapabilityDenied(format!(
        "Write to '{}' requires capability '{}': {:?}",
        path,
        cap.identifier(),
        outcome
    )))
}

/// Write content to a file.
///
/// Requires: `Capability::WorkspaceWriteTracked` OR `Capability::WorkspaceWriteEphemeral`
///
/// For parallel workers: Also checks `session.check_edit_area(path)` to enforce
/// restricted edit areas. Writes outside the allowed area are denied.
///
/// Parameters:
/// - `path`: Target file path
/// - `content`: Content to write
fn write_file_to_workspace(
    workspace: &dyn Workspace,
    path: &str,
    content: &str,
) -> Result<(), ToolError> {
    workspace
        .write(Path::new(path), content)
        .map_err(|e| ToolError::ExecutionError(format!("Failed to write file '{}': {}", path, e)))
}

/// Write content to a file in the workspace.
///
/// # Method Identifier
///
/// `write_file`
///
/// # Capability Requirements
///
/// Requires `McpCapability::WorkspaceWriteTracked` for existing git-tracked files,
/// or `McpCapability::WorkspaceWriteEphemeral` for new or `.agent/`-scoped files.
/// For parallel workers, the path must also satisfy `session.check_edit_area(path)`.
///
/// # Access Mode
///
/// ReadWrite only. Rejected in ReadOnly mode with `AccessDeniedCode::ReadOnlyMode`.
///
/// # Request Shape
///
/// ```json
/// {"path": "src/main.rs", "content": "fn main() {}"}
/// ```
///
/// ## Required Fields
///
/// - `path` (`string`): Workspace-relative target file path.
/// - `content` (`string`): Full content to write (overwrites existing content).
///
/// # Response Shape
///
/// ```json
/// {"content": [{"type": "text", "text": "Successfully wrote 12 bytes to src/main.rs"}], "isError": false}
/// ```
///
/// # Error Codes
///
/// - JSON-RPC `-32000` (CapabilityDenied): Session lacks the required write capability,
///   or parallel worker edit area restriction blocks the write.
/// - JSON-RPC `-32000` (Tool error): Filesystem write failure.
///
/// # Side Effects
///
/// Writes to the workspace. For real filesystem-backed workspaces, creates/overwrites
/// the file at the given path. Does not stage, commit, or trigger any git operations.
///
/// # Idempotency
///
/// Writing the same content twice produces identical state. Multiple writes to the
/// same path with different content are not idempotent.
pub fn handle_write_file(
    session: &AgentSession,
    workspace: &dyn Workspace,
    params: serde_json::Value,
) -> Result<ToolResult, ToolError> {
    let path = required_string_param(&params, "path")?;
    let content = required_string_param(&params, "content")?;
    check_edit_area_restriction(session, path)?;
    check_write_capability(session, path, is_path_git_tracked(workspace, path))?;
    write_file_to_workspace(workspace, path, content)?;
    Ok(ToolResult {
        content: vec![ToolContent::text(format!(
            "Successfully wrote {} bytes to {}",
            content.len(),
            path
        ))],
        is_error: Some(false),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agents::session::{RestrictedEditArea, SessionDrain};
    use crate::workspace::memory_workspace::MemoryWorkspace;
    use std::sync::Arc;

    fn test_session() -> AgentSession {
        AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
    }

    fn test_workspace() -> Arc<dyn Workspace> {
        Arc::new(MemoryWorkspace::new_test())
    }

    #[test]
    fn test_read_file_success() {
        let session = test_session();
        let workspace = test_workspace();

        // Create a test file
        workspace
            .write(std::path::Path::new("test.txt"), "hello world")
            .unwrap();

        let result = handle_read_file(
            &session,
            workspace.as_ref(),
            serde_json::json!({"path": "test.txt"}),
        );

        assert!(result.is_ok());
        let tool_result = result.unwrap();
        assert!(!tool_result.is_error.unwrap_or(false));
        assert!(tool_result.content[0].text.contains("hello world"));
    }

    #[test]
    fn test_read_file_missing_param() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_read_file(&session, workspace.as_ref(), serde_json::json!({}));

        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), ToolError::InvalidParams(_)));
    }

    #[test]
    fn test_list_directory() {
        let session = test_session();
        let workspace = test_workspace();

        // Create some test files
        workspace
            .write(std::path::Path::new("file1.txt"), "content1")
            .unwrap();
        workspace
            .write(std::path::Path::new("file2.txt"), "content2")
            .unwrap();

        let result = handle_list_directory(
            &session,
            workspace.as_ref(),
            serde_json::json!({"path": ".", "recursive": false}),
        );

        assert!(result.is_ok());
        let tool_result = result.unwrap();
        assert!(tool_result.content[0].text.contains("file1.txt"));
        assert!(tool_result.content[0].text.contains("file2.txt"));
    }

    #[test]
    fn test_write_file_denied_for_planning_session() {
        // Planning session doesn't have WorkspaceWriteTracked capability.
        // It only has WorkspaceWriteEphemeral for new files.
        // We test that it CAN write new files (ephemeral) but cannot overwrite tracked files.
        let planning_session =
            AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
        let workspace = test_workspace();

        // Pre-create a file in the workspace to simulate a tracked file
        workspace
            .write(std::path::Path::new("tracked_file.txt"), "initial content")
            .unwrap();

        // Planning session should be denied when trying to write a tracked file
        // (because the file exists and is_tracked=true, requiring WorkspaceWriteTracked)
        let result = handle_write_file(
            &planning_session,
            workspace.as_ref(),
            serde_json::json!({"path": "tracked_file.txt", "content": "hello"}),
        );

        assert!(result.is_err());
        assert!(matches!(
            result.unwrap_err(),
            ToolError::CapabilityDenied(_)
        ));

        // But Planning CAN write new ephemeral files
        let result = handle_write_file(
            &planning_session,
            workspace.as_ref(),
            serde_json::json!({"path": "new_ephemeral.txt", "content": "hello"}),
        );

        assert!(
            result.is_ok(),
            "Planning should be able to write ephemeral files"
        );
    }

    #[test]
    fn test_write_file_allowed_for_dev_session() {
        let session = test_session();
        let workspace = test_workspace();

        let result = handle_write_file(
            &session,
            workspace.as_ref(),
            serde_json::json!({"path": "new_file.txt", "content": "hello"}),
        );

        assert!(result.is_ok());
        let tool_result = result.unwrap();
        assert!(!tool_result.is_error.unwrap_or(false));
    }

    #[test]
    fn test_write_file_edit_area_enforcement() {
        // Create a parallel worker session with restricted edit area
        let worker_identity = crate::agents::session::WorkerIdentity {
            worker_id: "test-worker".to_string(),
            parent_session_id: crate::agents::session::AgentSessionId::new(
                "test-run",
                &SessionDrain::Development,
                0,
            ),
            work_unit_id: "unit-1".to_string(),
            branch_name: "feature/test".to_string(),
        };

        let edit_area = RestrictedEditArea::directory("src/utils");

        let session = AgentSession::for_parallel_worker(
            "test-run".to_string(),
            SessionDrain::Development,
            0,
            worker_identity,
            edit_area,
            std::time::SystemTime::now(),
        );

        let workspace = test_workspace();

        // Write to allowed path should succeed
        let result = handle_write_file(
            &session,
            workspace.as_ref(),
            serde_json::json!({"path": "src/utils/mod.rs", "content": "module"}),
        );
        assert!(result.is_ok(), "Write to allowed path should succeed");

        // Write to disallowed path should fail
        let result = handle_write_file(
            &session,
            workspace.as_ref(),
            serde_json::json!({"path": "src/lib.rs", "content": "main"}),
        );
        assert!(
            result.is_err(),
            "Write to outside edit area should be denied"
        );
        assert!(matches!(
            result.unwrap_err(),
            ToolError::CapabilityDenied(_)
        ));
    }

    #[test]
    fn test_search_files() {
        let session = test_session();
        let workspace = test_workspace();

        // Create test files
        workspace
            .write(std::path::Path::new("test1.txt"), "content")
            .unwrap();
        workspace
            .write(std::path::Path::new("test2.txt"), "content")
            .unwrap();
        workspace
            .write(std::path::Path::new("other.txt"), "content")
            .unwrap();

        let result = handle_search_files(
            &session,
            workspace.as_ref(),
            serde_json::json!({"pattern": "test", "path": "."}),
        );

        assert!(result.is_ok());
        let tool_result = result.unwrap();
        assert!(tool_result.content[0].text.contains("test1.txt"));
        assert!(tool_result.content[0].text.contains("test2.txt"));
        assert!(!tool_result.content[0].text.contains("other.txt"));
    }
}
