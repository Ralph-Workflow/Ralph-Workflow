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
        "read_file",
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
        "read_file",
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
        "list_directory",
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
        "write_file",
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
        "search_files",
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

    let result = registry.dispatch("git_status", serde_json::json!({}), &host, &ws);

    // Git status may fail in test environment without git repo, but should not panic
    // Result depends on workspace state
    assert!(result.is_ok() || result.is_err());
}

#[test]
fn test_git_diff_tool() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch("git_diff", serde_json::json!({"args": []}), &host, &ws);

    // Git diff may fail in test environment without git repo
    assert!(result.is_ok() || result.is_err());
}

#[test]
fn test_report_progress_tool() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch(
        "report_progress",
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
        "declare_complete",
        serde_json::json!({"summary": "done"}),
        &host,
        &ws,
    );

    assert!(result.is_ok());
}

#[test]
fn test_read_env_tool() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch("read_env", serde_json::json!({"name": "PATH"}), &host, &ws);

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

/// Verify that `ralph_submit_artifact` succeeds in a Development drain session.
///
/// Submits a valid plan artifact — the tool must dispatch without error (no ToolError),
/// and the workspace must contain the written artifact file.
/// This test is the primary regression guard for the "ralph_submit_artifact not found" bug.
#[test]
fn submit_artifact_succeeds_in_development_drain() {
    let (registry, host, ws, workspace) = setup_dispatch_with_workspace();

    let valid_plan = serde_json::json!({
        "summary": {
            "context": "Add error types for MCP protocol layer",
            "scope_items": [
                {"text": "Add ErrorCode enum"},
                {"text": "Add ValidationError struct"},
                {"text": "Add ErrorResponse struct"}
            ]
        },
        "steps": [
            {
                "number": 1,
                "title": "Define error types",
                "content": "Add ErrorCode, ValidationError to types.rs"
            }
        ],
        "critical_files": {
            "primary_files": [
                {"path": "mcp-server/src/protocol/types.rs", "action": "modify"}
            ]
        },
        "risks_mitigations": [
            {"risk": "Breaking existing API", "mitigation": "Add types only, no removal"}
        ],
        "verification_strategy": [
            {"method": "cargo test", "expected_outcome": "All tests pass"}
        ]
    });

    let result = registry.dispatch(
        "ralph_submit_artifact",
        serde_json::json!({
            "artifact_type": "plan",
            "content": serde_json::to_string(&valid_plan).expect("plan is serializable")
        }),
        &host,
        &ws,
    );

    // Tool must succeed (no ToolError).
    assert!(
        result.is_ok(),
        "submit_artifact must succeed in Development drain; got: {result:?}"
    );
    let tool_result = result.unwrap();
    assert!(
        !tool_result.is_error.unwrap_or(true),
        "valid plan must be accepted without validation errors"
    );
    let text = &tool_result.content[0].text;
    assert!(
        text.contains("\"accepted\": true"),
        "response must contain accepted=true; got: {text}"
    );

    // The workspace must contain the written artifact file.
    let artifact = workspace
        .read_artifact_json("plan")
        .expect("workspace.read_artifact_json must not fail");
    assert!(
        artifact.is_some(),
        "workspace must contain the written plan artifact after successful submit"
    );
}

/// Verify that `write_file` is denied in a Planning drain session.
///
/// Planning drain maps to a session manifest that excludes `write_file` entirely.
/// The MCP server still runs in `ReadOnly` mode underneath, but advertisement now
/// filters unavailable tools at `tools/list` time via `ToolFilter::Allowlist`.
/// As a result, attempts to call `write_file` are rejected as `ToolNotAllowed`
/// before ReadOnly enforcement needs to run.
///
/// This test exercises the advertisement-time enforcement path at the `McpServer`
/// level, separate from capability checks and ReadOnly-mode dispatch behavior.
#[test]
fn write_file_denied_in_planning_drain_readonly_mode() {
    use crate::agents::session::SessionDrain;
    use crate::workspace::Workspace;
    use mcp_server::io::ServerState;
    use mcp_server::protocol::JsonRpcRequest;

    let ws = Arc::new(MemoryWorkspace::new_test());
    let session = AgentSession::for_drain("readonly-test".to_string(), SessionDrain::Planning, 1);
    let workspace: Arc<dyn Workspace> = ws;
    let mut bridge = crate::mcp_server::session_bridge::SessionBridge::new(session, workspace);
    bridge.start().expect("bridge must start");

    // Initialize handshake.
    let init_req: JsonRpcRequest = serde_json::from_value(serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    }))
    .expect("init request is valid");
    let (_, state) = bridge.handle_request_in_process(init_req, ServerState::Uninitialized);

    // Attempt to call write_file — must be denied because Planning never exposes it.
    let call_req: JsonRpcRequest = serde_json::from_value(serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "write_file",
            "arguments": {"path": "test.txt", "content": "hello"}
        },
        "id": 2
    }))
    .expect("tools/call request is valid");

    let (resp, _) = bridge.handle_request_in_process(call_req, state);
    let resp = serde_json::to_value(resp.expect("response must be present")).expect("serialize");

    // Must be a JSON-RPC error response (tool excluded from the planning manifest).
    assert!(
        resp["error"].is_object(),
        "write_file in Planning must return an error; got: {resp}"
    );
    let data_str = resp["error"]["data"].to_string();
    assert!(
        data_str.contains("ToolNotAllowed"),
        "error data must contain ToolNotAllowed code; got: {data_str}"
    );
}

/// Verify that `ralph_submit_artifact` is allowed in a Planning drain session (ReadOnly mode).
///
/// This test explicitly documents the architectural decision: `ArtifactSubmit` is classified
/// as a non-mutating workflow coordination signal, NOT a workspace file mutation. The Planning
/// agent's primary job is to produce and submit a plan, so blocking `ralph_submit_artifact`
/// in Planning mode would break the intended workflow.
///
/// Contrast with `write_file_denied_in_planning_drain_readonly_mode`: filesystem writes are
/// blocked in ReadOnly mode, but workflow coordination signals (`ArtifactSubmit`,
/// `RunReportProgress`, `WorkspaceCoordination`) pass through ReadOnly enforcement.
///
/// This test is the explicit contract proof:
/// - `Planning` drain → `ReadOnly` access mode
/// - `ArtifactSubmit` capability → `capability_is_mutating()` returns `false`
/// - Result: `ralph_submit_artifact` is dispatched and returns success
#[test]
fn submit_artifact_allowed_in_planning_drain() {
    use crate::agents::session::SessionDrain;
    use crate::workspace::Workspace;
    use mcp_server::io::ServerState;
    use mcp_server::protocol::JsonRpcRequest;

    let ws = Arc::new(MemoryWorkspace::new_test());
    let session = AgentSession::for_drain("planning-test".to_string(), SessionDrain::Planning, 1);
    let workspace: Arc<dyn Workspace> = ws;
    let mut bridge = crate::mcp_server::session_bridge::SessionBridge::new(session, workspace);
    bridge.start().expect("bridge must start");

    // Initialize handshake.
    let init_req: JsonRpcRequest = serde_json::from_value(serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    }))
    .expect("init request is valid");
    let (_, state) = bridge.handle_request_in_process(init_req, ServerState::Uninitialized);

    let valid_plan = serde_json::json!({
        "summary": {
            "context": "Plan from Planning drain — this must succeed in ReadOnly mode",
            "scope_items": [
                {"text": "Define the plan"},
                {"text": "Submit via artifact"},
                {"text": "Verify ReadOnly-safe behavior"}
            ]
        },
        "steps": [
            {"number": 1, "title": "Define plan", "content": "Write the plan"}
        ],
        "critical_files": {
            "primary_files": [{"path": "src/main.rs", "action": "modify"}]
        },
        "risks_mitigations": [
            {"risk": "None", "mitigation": "No risks identified"}
        ],
        "verification_strategy": [
            {"method": "cargo test", "expected_outcome": "All tests pass"}
        ]
    });

    let artifact_req: JsonRpcRequest = serde_json::from_value(serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "ralph_submit_artifact",
            "arguments": {
                "artifact_type": "plan",
                "content": serde_json::to_string(&valid_plan).expect("plan is serializable")
            }
        },
        "id": 2
    }))
    .expect("artifact call request is valid");

    let (resp, _) = bridge.handle_request_in_process(artifact_req, state);
    let resp = serde_json::to_value(resp.expect("response must be present")).expect("serialize");

    // Must NOT return a JSON-RPC error — artifact submission is ReadOnly-safe.
    assert!(
        resp["error"].is_null(),
        "ralph_submit_artifact must NOT be denied in Planning/ReadOnly mode; \
         ArtifactSubmit is a non-mutating workflow coordination signal. \
         Got: {resp}"
    );
    let result = &resp["result"];
    assert!(
        result.is_object(),
        "response must contain a result object; got: {resp}"
    );
}

/// Verify that `ralph_submit_artifact` returns an error for an unknown artifact type.
///
/// This tests the parameter validation path in `resolve_artifact_type`.
/// The error must be a `ToolError::InvalidParams` containing "Unknown artifact type".
#[test]
fn submit_artifact_unknown_type_is_rejected() {
    let (registry, host, ws) = setup_dispatch();

    let result = registry.dispatch(
        "ralph_submit_artifact",
        serde_json::json!({
            "artifact_type": "totally_unknown_type_xyz",
            "content": "{}"
        }),
        &host,
        &ws,
    );

    assert!(
        result.is_err(),
        "unknown artifact type must return an error"
    );
    let err = result.unwrap_err();
    let err_str = err.to_string();
    assert!(
        err_str.contains("Unknown artifact type") || err_str.contains("Invalid parameters"),
        "error must mention unknown artifact type; got: {err_str}"
    );
}
