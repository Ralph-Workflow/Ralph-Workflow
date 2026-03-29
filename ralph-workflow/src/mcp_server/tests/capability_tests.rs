//! Capability enforcement tests.
//!
//! Tests that each tool correctly enforces capability requirements.

use crate::agents::session::{AgentSession, SessionDrain};
use crate::mcp_server::tool_registry::{ToolError, ToolRegistry};
use crate::workspace::memory_workspace::MemoryWorkspace;
use std::sync::Arc;

fn dev_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
}

fn planning_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1)
}

fn review_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Review, 1)
}

fn commit_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Commit, 1)
}

fn test_workspace() -> Arc<dyn crate::workspace::Workspace> {
    Arc::new(MemoryWorkspace::new_test())
}

fn tool_registry() -> ToolRegistry {
    ToolRegistry::with_ralph_tools()
}

// Development session capabilities tests

#[test]
fn test_dev_session_has_workspace_read() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    workspace
        .write(std::path::Path::new("test.txt"), "hello")
        .expect("create file");

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_read_file",
        serde_json::json!({"path": "test.txt"}),
    );

    assert!(result.is_ok());
}

#[test]
fn test_dev_session_has_workspace_write() {
    let registry = tool_registry();
    let session = dev_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_write_file",
        serde_json::json!({"path": "new.txt", "content": "hello"}),
    );

    assert!(result.is_ok());
}

// Planning session capability tests

#[test]
fn test_planning_session_has_workspace_read() {
    let registry = tool_registry();
    let session = planning_session();
    let workspace = test_workspace();

    workspace
        .write(std::path::Path::new("test.txt"), "hello")
        .expect("create file");

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_read_file",
        serde_json::json!({"path": "test.txt"}),
    );

    assert!(result.is_ok());
}

#[test]
fn test_planning_session_can_write_ephemeral_new_file() {
    // Planning has WorkspaceWriteEphemeral — new files (non-existing) are treated as ephemeral.
    let registry = tool_registry();
    let session = planning_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_write_file",
        serde_json::json!({"path": "new.txt", "content": "hello"}),
    );

    assert!(
        result.is_ok(),
        "planning session must be able to write new (ephemeral) files"
    );
}

#[test]
fn test_planning_session_denied_write_tracked_file() {
    // Planning has WorkspaceWriteEphemeral but NOT WorkspaceWriteTracked.
    // Existing non-.agent files are treated as tracked — planning must be denied.
    let registry = tool_registry();
    let session = planning_session();
    let workspace = test_workspace();

    // Pre-create a tracked file
    workspace
        .write(std::path::Path::new("tracked.rs"), "fn main() {}")
        .expect("pre-create tracked file");

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_write_file",
        serde_json::json!({"path": "tracked.rs", "content": "changed"}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_planning_session_denied_exec() {
    let registry = tool_registry();
    let session = planning_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "echo", "args": ["hello"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_planning_session_has_git_status() {
    let registry = tool_registry();
    let session = planning_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_git_status",
        serde_json::json!({}),
    );

    // Should not be capability denied
    match result {
        Ok(_) => {}
        Err(e) => {
            assert!(!matches!(e, ToolError::CapabilityDenied(_)));
        }
    }
}

// Review session capability tests

#[test]
fn test_review_session_has_git_diff() {
    let registry = tool_registry();
    let session = review_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_git_diff",
        serde_json::json!({"args": []}),
    );

    // Should not be capability denied
    match result {
        Ok(_) => {}
        Err(e) => {
            assert!(!matches!(e, ToolError::CapabilityDenied(_)));
        }
    }
}

#[test]
fn test_review_session_denied_git_write() {
    let registry = tool_registry();
    let session = review_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_git_commit",
        serde_json::json!({"message": "test commit"}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

// Commit session capability tests

#[test]
fn test_commit_session_has_git_write() {
    let registry = tool_registry();
    let session = commit_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_git_commit",
        serde_json::json!({"message": "test commit"}),
    );

    // Should not be capability denied (commit has git write)
    match result {
        Ok(_) => {}
        Err(e) => {
            assert!(!matches!(e, ToolError::CapabilityDenied(_)));
        }
    }
}

#[test]
fn test_commit_session_denied_exec() {
    let registry = tool_registry();
    let session = commit_session();
    let workspace = test_workspace();

    let result = registry.execute(
        &session,
        workspace.as_ref(),
        "ralph_exec_command",
        serde_json::json!({"command": "echo", "args": ["hello"]}),
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}
