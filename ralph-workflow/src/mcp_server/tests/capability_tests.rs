//! Capability enforcement tests.
//!
//! Tests that each tool correctly enforces capability requirements.

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

fn setup_dispatch_with_workspace(
    session: AgentSession,
) -> (
    mcp_server::ToolRegistry,
    RalphHostSessionAdapter,
    RalphWorkspaceAdapter,
    Arc<dyn crate::workspace::Workspace>,
) {
    let session = Arc::new(session);
    let workspace = test_workspace();
    let registry = build_ralph_tool_registry(Arc::clone(&session), Arc::clone(&workspace));
    let host = RalphHostSessionAdapter::new(Arc::clone(&session));
    let ws = RalphWorkspaceAdapter::new(Arc::clone(&workspace));
    (registry, host, ws, workspace)
}

fn setup_dispatch(
    session: AgentSession,
) -> (
    mcp_server::ToolRegistry,
    RalphHostSessionAdapter,
    RalphWorkspaceAdapter,
) {
    let (registry, host, ws, _) = setup_dispatch_with_workspace(session);
    (registry, host, ws)
}

// Development session capabilities tests

#[test]
fn test_dev_session_has_workspace_read() {
    let (registry, host, ws, workspace) = setup_dispatch_with_workspace(dev_session());

    workspace
        .write(std::path::Path::new("test.txt"), "hello")
        .expect("create file");

    let result = registry.dispatch(
        "ralph_read_file",
        serde_json::json!({"path": "test.txt"}),
        &host,
        &ws,
    );

    assert!(result.is_ok());
}

#[test]
fn test_dev_session_has_workspace_write() {
    let (registry, host, ws) = setup_dispatch(dev_session());

    let result = registry.dispatch(
        "ralph_write_file",
        serde_json::json!({"path": "new.txt", "content": "hello"}),
        &host,
        &ws,
    );

    assert!(result.is_ok());
}

// Planning session capability tests

#[test]
fn test_planning_session_has_workspace_read() {
    let (registry, host, ws, workspace) = setup_dispatch_with_workspace(planning_session());

    workspace
        .write(std::path::Path::new("test.txt"), "hello")
        .expect("create file");

    let result = registry.dispatch(
        "ralph_read_file",
        serde_json::json!({"path": "test.txt"}),
        &host,
        &ws,
    );

    assert!(result.is_ok());
}

#[test]
fn test_planning_session_can_write_ephemeral_new_file() {
    // Planning has WorkspaceWriteEphemeral — new files (non-existing) are treated as ephemeral.
    let (registry, host, ws) = setup_dispatch(planning_session());

    let result = registry.dispatch(
        "ralph_write_file",
        serde_json::json!({"path": "new.txt", "content": "hello"}),
        &host,
        &ws,
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
    let (registry, host, ws, workspace) = setup_dispatch_with_workspace(planning_session());

    // Pre-create a tracked file using the SAME workspace that the tool will write to
    workspace
        .write(std::path::Path::new("tracked.rs"), "fn main() {}")
        .expect("pre-create tracked file");

    let result = registry.dispatch(
        "ralph_write_file",
        serde_json::json!({"path": "tracked.rs", "content": "changed"}),
        &host,
        &ws,
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_planning_session_denied_exec() {
    let (registry, host, ws) = setup_dispatch(planning_session());

    let result = registry.dispatch(
        "ralph_exec_command",
        serde_json::json!({"command": "echo", "args": ["hello"]}),
        &host,
        &ws,
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}

#[test]
fn test_planning_session_has_git_status() {
    let (registry, host, ws) = setup_dispatch(planning_session());

    let result = registry.dispatch("ralph_git_status", serde_json::json!({}), &host, &ws);

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
    let (registry, host, ws) = setup_dispatch(review_session());

    let result = registry.dispatch(
        "ralph_git_diff",
        serde_json::json!({"args": []}),
        &host,
        &ws,
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
fn test_commit_session_denied_exec() {
    let (registry, host, ws) = setup_dispatch(commit_session());

    let result = registry.dispatch(
        "ralph_exec_command",
        serde_json::json!({"command": "echo", "args": ["hello"]}),
        &host,
        &ws,
    );

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(matches!(err, ToolError::CapabilityDenied(_)));
}
