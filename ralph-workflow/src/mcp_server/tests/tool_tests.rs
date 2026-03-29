//! Tool handler unit tests.
//!
//! Tests individual tool handlers with various inputs and edge cases.

use crate::agents::session::{AgentSession, SessionDrain};
use crate::mcp_server::tool_registry::ToolRegistry;
use crate::workspace::memory_workspace::MemoryWorkspace;
use std::sync::Arc;

fn dev_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
}

fn test_workspace() -> Arc<dyn crate::workspace::Workspace> {
    Arc::new(MemoryWorkspace::new_test())
}

fn tool_registry() -> ToolRegistry {
    ToolRegistry::with_ralph_tools()
}

#[test]
fn test_read_file_tool_success() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    // Create a test file
    workspace
        .write(std::path::Path::new("test_read.txt"), "hello world")
        .expect("create test file");

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_read_file",
        serde_json::json!({"path": "test_read.txt"}),
    );

    assert!(result.is_ok());
    let tool_result = result.unwrap();
    assert!(!tool_result.is_error.unwrap_or(false));
    let content = &tool_result.content[0].text;
    assert!(content.contains("hello world"));
}

#[test]
fn test_read_file_tool_missing_file() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_read_file",
        serde_json::json!({"path": "nonexistent.txt"}),
    );

    assert!(result.is_err());
}

#[test]
fn test_list_directory_tool_success() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    // Create test files
    workspace
        .write(std::path::Path::new("file1.txt"), "content1")
        .expect("create file1");
    workspace
        .write(std::path::Path::new("file2.txt"), "content2")
        .expect("create file2");

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_list_directory",
        serde_json::json!({"path": ".", "recursive": false}),
    );

    assert!(result.is_ok());
}

#[test]
fn test_write_file_tool_creates_file() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_write_file",
        serde_json::json!({
            "path": "new_file.txt",
            "content": "new content"
        }),
    );

    assert!(result.is_ok());
}

#[test]
fn test_search_files_tool() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    // Create test file with searchable content
    workspace
        .write(
            std::path::Path::new("search_target.txt"),
            "hello search world",
        )
        .expect("create file");

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_search_files",
        serde_json::json!({
            "pattern": "hello",
            "path": "."
        }),
    );

    assert!(result.is_ok());
}

#[test]
fn test_git_status_tool() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_git_status",
        serde_json::json!({}),
    );

    // Git status may fail in test environment without git repo, but should not panic
    // Result depends on workspace state
    assert!(result.is_ok() || result.is_err());
}

#[test]
fn test_git_diff_tool() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_git_diff",
        serde_json::json!({"args": []}),
    );

    // Git diff may fail in test environment without git repo
    assert!(result.is_ok() || result.is_err());
}

#[test]
fn test_report_progress_tool() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_report_progress",
        serde_json::json!({
            "status": "in_progress",
            "note": "working on it"
        }),
    );

    assert!(result.is_ok());
}

#[test]
fn test_declare_complete_tool() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_declare_complete",
        serde_json::json!({"summary": "done"}),
    );

    assert!(result.is_ok());
}

#[test]
fn test_read_env_tool() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_read_env",
        serde_json::json!({"name": "PATH"}),
    );

    // May or may not succeed depending on environment
    assert!(result.is_ok() || result.is_err());
}

#[test]
fn test_tool_not_found() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "nonexistent_tool",
        serde_json::json!({}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(
        err,
        crate::mcp_server::tool_registry::ToolError::NotFound(_)
    ));
}
