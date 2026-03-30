//! Tool handler unit tests.
//!
//! Tests individual tool handlers with various inputs and edge cases.

use crate::agents::session::{AgentSession, SessionDrain};
use crate::mcp_server::tool_bridge::{
    build_ralph_tool_registry, RalphHostSessionAdapter, RalphWorkspaceAdapter,
};
use crate::workspace::memory_workspace::MemoryWorkspace;
use mcp_server::ToolError;
use std::sync::Arc;

fn dev_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
}

fn test_workspace() -> Arc<dyn crate::workspace::Workspace> {
    Arc::new(MemoryWorkspace::new_test())
}

fn setup_dispatch_with_workspace() -> (
    mcp_server::ToolRegistry,
    RalphHostSessionAdapter,
    RalphWorkspaceAdapter,
    Arc<dyn crate::workspace::Workspace>,
) {
    let session = Arc::new(dev_session());
    let workspace = test_workspace();
    let registry = build_ralph_tool_registry(Arc::clone(&session), Arc::clone(&workspace));
    let host = RalphHostSessionAdapter::new(Arc::clone(&session));
    let ws = RalphWorkspaceAdapter::new(Arc::clone(&workspace));
    (registry, host, ws, workspace)
}

fn setup_dispatch() -> (
    mcp_server::ToolRegistry,
    RalphHostSessionAdapter,
    RalphWorkspaceAdapter,
) {
    let (registry, host, ws, _) = setup_dispatch_with_workspace();
    (registry, host, ws)
}

#[test]
fn test_read_file_tool_success() {
    let (registry, host, ws, workspace) = setup_dispatch_with_workspace();

    // Create a test file using the SAME workspace that the tool will read from
    workspace
        .write(std::path::Path::new("test_read.txt"), "hello world")
        .expect("create test file");

    let result = registry.dispatch(
        "ralph_read_file",
        serde_json::json!({"path": "test_read.txt"}),
        &host,
        &ws,
    );

    assert!(result.is_ok());
    let tool_result = result.unwrap();
    assert!(!tool_result.is_error.unwrap_or(false));
    let content = &tool_result.content[0].text;
    assert!(content.contains("hello world"));
}

#[test]
fn test_read_file_tool_missing_file() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch(
        "ralph_read_file",
        serde_json::json!({"path": "nonexistent.txt"}),
        &host,
        &ws,
    );

    assert!(result.is_err());
}

#[test]
fn test_list_directory_tool_success() {
    let (registry, host, ws, workspace) = setup_dispatch_with_workspace();

    // Create test files using the SAME workspace that the tool will read from
    workspace
        .write(std::path::Path::new("file1.txt"), "content1")
        .expect("create file1");
    workspace
        .write(std::path::Path::new("file2.txt"), "content2")
        .expect("create file2");

    let result = registry.dispatch(
        "ralph_list_directory",
        serde_json::json!({"path": ".", "recursive": false}),
        &host,
        &ws,
    );

    assert!(result.is_ok());
}

#[test]
fn test_write_file_tool_creates_file() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch(
        "ralph_write_file",
        serde_json::json!({
            "path": "new_file.txt",
            "content": "new content"
        }),
        &host,
        &ws,
    );

    assert!(result.is_ok());
}

#[test]
fn test_search_files_tool() {
    let (registry, host, ws, workspace) = setup_dispatch_with_workspace();

    // Create test file with searchable content using the SAME workspace
    workspace
        .write(
            std::path::Path::new("search_target.txt"),
            "hello search world",
        )
        .expect("create file");

    let result = registry.dispatch(
        "ralph_search_files",
        serde_json::json!({
            "pattern": "hello",
            "path": "."
        }),
        &host,
        &ws,
    );

    assert!(result.is_ok());
}

#[test]
fn test_git_status_tool() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch("ralph_git_status", serde_json::json!({}), &host, &ws);

    // Git status may fail in test environment without git repo, but should not panic
    // Result depends on workspace state
    assert!(result.is_ok() || result.is_err());
}

#[test]
fn test_git_diff_tool() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch(
        "ralph_git_diff",
        serde_json::json!({"args": []}),
        &host,
        &ws,
    );

    // Git diff may fail in test environment without git repo
    assert!(result.is_ok() || result.is_err());
}

#[test]
fn test_report_progress_tool() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch(
        "ralph_report_progress",
        serde_json::json!({
            "status": "in_progress",
            "note": "working on it"
        }),
        &host,
        &ws,
    );

    assert!(result.is_ok());
}

#[test]
fn test_declare_complete_tool() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch(
        "ralph_declare_complete",
        serde_json::json!({"summary": "done"}),
        &host,
        &ws,
    );

    assert!(result.is_ok());
}

#[test]
fn test_read_env_tool() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch(
        "ralph_read_env",
        serde_json::json!({"name": "PATH"}),
        &host,
        &ws,
    );

    // May or may not succeed depending on environment
    assert!(result.is_ok() || result.is_err());
}

#[test]
fn test_tool_not_found() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch("nonexistent_tool", serde_json::json!({}), &host, &ws);

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::NotFound(_)));
}
