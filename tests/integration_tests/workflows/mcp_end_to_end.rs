//! End-to-end MCP protocol integration tests.
//!
//! These tests verify the full MCP stack works correctly using Ralph's actual
//! SessionBridge and MemoryWorkspace for isolation, while routing requests
//! through an in-process protocol seam instead of OS sockets.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[`INTEGRATION_TESTS.md`](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (MCP protocol responses, audit records)
//! - Uses `MemoryWorkspace` to avoid real filesystem operations
//! - Uses `AgentSession::for_drain` for session creation with proper capabilities
//! - Tests are deterministic and isolated

use mcp_server::io::ServerState;
use mcp_server::protocol::{JsonRpcRequest, JsonRpcResponse};
use ralph_workflow::agents::session::{AgentSession, SessionDrain};
use ralph_workflow::mcp_server::session_bridge::SessionBridge;
use ralph_workflow::workspace::MemoryWorkspace;
use ralph_workflow::Workspace;
use std::path::PathBuf;
use std::sync::Arc;
use test_helpers::assert_no_real_git_mutations;

use crate::test_timeout::with_default_timeout;

// ---------------------------------------------------------------------------
// Helper: MCP message flow through the public session bridge seam
// ---------------------------------------------------------------------------

struct TestConnection {
    bridge: SessionBridge,
    state: ServerState,
}

fn connect(bridge: &SessionBridge) -> TestConnection {
    TestConnection {
        bridge: bridge.clone(),
        state: ServerState::Uninitialized,
    }
}

fn send_mcp_request(stream: &mut TestConnection, request: serde_json::Value) -> JsonRpcResponse {
    let request: JsonRpcRequest =
        serde_json::from_value(request).expect("request should be valid JSON-RPC");
    let (response, state) = stream
        .bridge
        .handle_request_in_process(request, stream.state);
    stream.state = state;
    response.expect("request should return a response")
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/// Create a test session with development drain and all capabilities.
fn test_session() -> AgentSession {
    AgentSession::for_drain(
        "mcp-e2e-test-session".to_string(),
        SessionDrain::Development,
        1,
    )
}

/// Create a test workspace with some files.
fn test_workspace() -> Arc<dyn ralph_workflow::workspace::Workspace> {
    let workspace = MemoryWorkspace::new(PathBuf::from("/test/repo"))
        .with_file("PROMPT.md", "# Test\n## Goal\nTest\n## Acceptance\n- Pass")
        .with_file("test.txt", "Hello, World!");
    let ws = Arc::new(workspace);
    // Guard: no real git mutations allowed in MCP integration tests.
    assert_no_real_git_mutations(ws.root());
    ws
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

/// Test that MCP initialize handshake succeeds with protocol version negotiation.
#[test]
fn mcp_initialize_handshake_succeeds() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        // Send initialize request
        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });

        let response = send_mcp_request(&mut stream, init_request);

        // Verify success
        assert!(
            response.result.is_some(),
            "initialize should succeed, got error: {:?}",
            response.error
        );

        let result = response.result.unwrap();
        assert_eq!(
            result["protocolVersion"], "2024-11-05",
            "Protocol version should be echoed back"
        );

        bridge.shutdown();
    });
}

/// Test that tools/list returns the registered Ralph tools.
#[test]
fn mcp_tools_list_returns_ralph_tools() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        // First initialize
        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        // Request tools list
        let list_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2
        });

        let response = send_mcp_request(&mut stream, list_request);

        assert!(
            response.result.is_some(),
            "tools/list should succeed, got error: {:?}",
            response.error
        );

        let result = response.result.unwrap();
        let tools = result["tools"]
            .as_array()
            .expect("tools should be an array");

        // Verify we have Ralph tools registered (at minimum, workspace read should be present)
        let tool_names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();

        assert!(
            !tool_names.is_empty(),
            "Should have at least one tool registered, got: {:?}",
            tool_names
        );

        // Verify some expected Ralph tools are present
        // The exact tool names come from the Ralph tool registry
        let has_workspace_tool = tool_names
            .iter()
            .any(|n| n.contains("workspace") || n.contains("read") || n.contains("file"));
        assert!(
            has_workspace_tool,
            "Expected workspace-related tool in list, got: {:?}",
            tool_names
        );

        bridge.shutdown();
    });
}

/// Test that calling a tool before initialize returns NotInitialized error.
#[test]
fn mcp_tool_call_before_initialize_returns_error() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        // Try to call a tool WITHOUT initializing first
        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {"path": "test.txt"}
            },
            "id": 1
        });

        let response = send_mcp_request(&mut stream, call_request);

        // Should get an error response (not initialized)
        assert!(
            response.error.is_some(),
            "Should return error when calling tool before initialize"
        );

        let error = response.error.unwrap();
        assert!(
            error.code == -32001 || error.message.contains("NotInitialized"),
            "Error code should be -32001 (NotInitialized), got: {} - {}",
            error.code,
            error.message
        );

        bridge.shutdown();
    });
}

/// Test that MCP endpoint URI is properly formatted for agent environment.
#[test]
fn mcp_endpoint_uri_is_properly_formatted() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let bridge = SessionBridge::new(session, workspace);

        let uri = bridge.endpoint_uri();
        assert!(
            uri.starts_with("unix://"),
            "Endpoint URI should start with 'unix://', got: {}",
            uri
        );
        assert!(
            uri.ends_with(".sock"),
            "Endpoint URI should end with '.sock', got: {}",
            uri
        );

        let env_var = bridge.endpoint_env_var();
        assert_eq!(env_var, "RALPH_MCP_ENDPOINT");
    });
}

/// Test that session bridge audit trail can be drained after operations.
#[test]
fn mcp_audit_trail_draining_works() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);

        // Initially empty
        let initial_records = bridge.drain_audit_records();
        assert!(
            initial_records.is_empty(),
            "Audit records should be empty initially"
        );

        // After starting, still empty (no operations yet)
        bridge.start().expect("Bridge should start");
        let after_start = bridge.drain_audit_records();
        assert!(
            after_start.is_empty(),
            "Audit records should be empty after start with no operations"
        );

        bridge.shutdown();
    });
}

/// Test that bridge shutdown properly stops the server.
#[test]
fn mcp_bridge_shutdown_stops_server() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        assert!(
            bridge.is_started(),
            "Bridge should be started after start()"
        );

        bridge.shutdown();

        assert!(
            bridge.is_shutdown(),
            "Bridge should be shutdown after shutdown()"
        );
    });
}

/// Test full MCP protocol flow: initialize → tools/list → tools/call (read_file).
#[test]
fn mcp_full_protocol_flow_read_file() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        // Step 1: Initialize
        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let response = send_mcp_request(&mut stream, init_request);
        assert!(
            response.result.is_some(),
            "initialize should succeed, got error: {:?}",
            response.error
        );

        // Step 2: List tools
        let list_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2
        });
        let response = send_mcp_request(&mut stream, list_request);
        assert!(
            response.result.is_some(),
            "tools/list should succeed, got error: {:?}",
            response.error
        );

        let result = response.result.unwrap();
        let tools = result["tools"]
            .as_array()
            .expect("tools should be an array");
        let tool_names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();

        // Find the read_file tool name (Ralph-prefixed)
        let read_file_tool = tool_names
            .iter()
            .find(|n| n.contains("read_file") || n.contains("workspace") && n.contains("read"))
            .expect("Should have a read file tool");

        // Step 3: Call read_file tool
        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": read_file_tool,
                "arguments": {"path": "test.txt"}
            },
            "id": 3
        });
        let response = send_mcp_request(&mut stream, call_request);

        // Verify the call succeeded
        assert!(
            response.result.is_some(),
            "tools/call should succeed, got error: {:?}",
            response.error
        );

        let result = response.result.unwrap();
        let content = result["content"]
            .as_array()
            .expect("content should be an array");

        // The test workspace has "Hello, World!" in test.txt
        let text_content = content
            .iter()
            .find(|c| c["type"] == "text")
            .expect("Should have text content");

        assert!(
            text_content["text"]
                .as_str()
                .unwrap()
                .contains("Hello, World!"),
            "Expected 'Hello, World!' in file content, got: {}",
            text_content["text"]
        );

        bridge.shutdown();
    });
}

// ---------------------------------------------------------------------------
// Helpers for ReadOnly session tests
// ---------------------------------------------------------------------------

/// Create a ReadOnly test session using Planning drain.
///
/// Planning drain maps to ReadOnly access mode (per RFC-009). Use this in tests
/// that verify ReadOnly mode enforcement or that ReadOnly-safe tools work correctly.
fn planning_session() -> AgentSession {
    AgentSession::for_drain(
        "mcp-e2e-planning-session".to_string(),
        SessionDrain::Planning,
        1,
    )
}

// ---------------------------------------------------------------------------
// Artifact tool tests (ralph_submit_artifact, declare_complete, report_progress, coordinate)
// ---------------------------------------------------------------------------

/// Regression test: `ralph_submit_artifact` must be callable end-to-end.
///
/// Before the workspace_root fix, agents reported `ralph_submit_artifact` as
/// "tool does not exist". This test proves the tool is dispatched correctly.
#[test]
fn mcp_ralph_submit_artifact_is_callable() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        // Initialize
        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let init_response = send_mcp_request(&mut stream, init_request);
        assert!(
            init_response.result.is_some(),
            "initialize should succeed, got: {:?}",
            init_response.error
        );

        // Call ralph_submit_artifact with partial: true so minimal content is accepted
        // and the artifact is recorded even without full validation.
        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "ralph_submit_artifact",
                "arguments": {
                    "artifact_type": "plan",
                    "content": "{\"summary\":{\"context\":\"test\",\"scope_items\":[]},\"steps\":[],\"critical_files\":{\"primary_files\":[]},\"risks_mitigations\":[],\"verification_strategy\":[]}",
                    "partial": true
                }
            },
            "id": 2
        });
        let response = send_mcp_request(&mut stream, call_request);

        // Primary assertion: the tool MUST be dispatched (not "tool not found").
        // Before the fix, this returned an error because the server failed to start
        // with a valid config. Now it must return a result.
        assert!(
            response.result.is_some(),
            "ralph_submit_artifact must be callable (tool was dispatched), \
             got error: {:?}. This indicates the tool is missing from the registry \
             or the server failed to initialize with a valid config.",
            response.error
        );

        bridge.shutdown();
    });
}

/// Verify `declare_complete` is callable end-to-end.
///
/// declare_complete is a ReadOnly-safe workflow signal. It must succeed in
/// both ReadWrite and ReadOnly sessions.
#[test]
fn mcp_declare_complete_is_callable() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "declare_complete",
                "arguments": {
                    "summary": "Integration test: declare_complete is callable"
                }
            },
            "id": 2
        });
        let response = send_mcp_request(&mut stream, call_request);

        assert!(
            response.result.is_some(),
            "declare_complete must succeed, got error: {:?}",
            response.error
        );

        bridge.shutdown();
    });
}

/// Verify `report_progress` is callable end-to-end.
///
/// report_progress is a ReadOnly-safe workflow signal. Development sessions have
/// the RunReportProgress capability and can call it successfully.
#[test]
fn mcp_report_progress_is_callable() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "report_progress",
                "arguments": {
                    "status": "Integration test: report_progress is callable"
                }
            },
            "id": 2
        });
        let response = send_mcp_request(&mut stream, call_request);

        assert!(
            response.result.is_some(),
            "report_progress must succeed for Development sessions, got error: {:?}",
            response.error
        );

        bridge.shutdown();
    });
}

/// Verify `coordinate` is callable end-to-end.
///
/// coordinate is a ReadOnly-safe workflow signal requiring ArtifactSubmit capability.
/// Development sessions have this capability and can call coordinate successfully.
#[test]
fn mcp_coordinate_is_callable() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "coordinate",
                "arguments": {
                    "action": "status"
                }
            },
            "id": 2
        });
        let response = send_mcp_request(&mut stream, call_request);

        assert!(
            response.result.is_some(),
            "coordinate must succeed for Development sessions, got error: {:?}",
            response.error
        );

        bridge.shutdown();
    });
}

/// Verify that workflow-signal tools succeed in ReadOnly (Planning) sessions.
///
/// `ralph_submit_artifact` requires ArtifactSubmit capability and is classified
/// as ReadOnly-safe: it does not mutate filesystem or git state, only emits a
/// signal to the workflow host. It must NOT be blocked by ReadOnly mode.
///
/// This is the primary regression test for the original bug where agents received
/// "tool does not exist" for `ralph_submit_artifact`. It must pass in both
/// ReadWrite AND ReadOnly sessions.
#[test]
fn mcp_artifact_tools_allowed_in_readonly_session() {
    with_default_timeout(|| {
        // Planning drain → ReadOnly access mode (per RFC-009)
        let session = planning_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        // ralph_submit_artifact is non-mutating (ArtifactSubmit capability, ReadOnly-safe).
        // Planning sessions have ArtifactSubmit, so this must succeed in ReadOnly mode.
        // Before the workspace_root fix, this returned "tool does not exist" because
        // the server failed to start. Now it must be dispatched and succeed.
        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "ralph_submit_artifact",
                "arguments": {
                    "artifact_type": "plan",
                    "content": "{\"summary\":{\"context\":\"readonly session test\",\"scope_items\":[]},\"steps\":[],\"critical_files\":{\"primary_files\":[]},\"risks_mitigations\":[],\"verification_strategy\":[]}",
                    "partial": true
                }
            },
            "id": 2
        });
        let response = send_mcp_request(&mut stream, call_request);

        assert!(
            response.result.is_some(),
            "ralph_submit_artifact must succeed in ReadOnly (Planning) sessions — \
             it is classified as non-mutating (ArtifactSubmit capability). \
             Got error: {:?}. \
             If this fails with ReadOnlyMode, ArtifactSubmit is incorrectly \
             classified as mutating in McpCapability::is_read(). \
             If this fails with ToolNotAllowed or tool-not-found, the tool is \
             missing from the registry or the server failed to initialize.",
            response.error
        );

        bridge.shutdown();
    });
}

/// Verify that tools/list includes all 15 registered Ralph tools.
///
/// All 15 tools must appear in the tools/list response. This guards against
/// tools being dropped from the registry during refactoring.
#[test]
fn mcp_tools_list_includes_all_fifteen_tools() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        let list_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2
        });
        let response = send_mcp_request(&mut stream, list_request);

        assert!(
            response.result.is_some(),
            "tools/list must succeed, got error: {:?}",
            response.error
        );

        let result = response.result.unwrap();
        let tools = result["tools"]
            .as_array()
            .expect("tools/list result must have a tools array");

        let tool_names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();

        // All 15 Ralph tools must be present.
        let expected_tools = [
            "read_file",
            "write_file",
            "list_directory",
            "list_directory_recursive",
            "search_files",
            "git_status",
            "git_diff",
            "git_log",
            "git_show",
            "exec",
            "ralph_submit_artifact",
            "report_progress",
            "declare_complete",
            "read_env",
            "coordinate",
        ];

        let missing: Vec<&str> = expected_tools
            .iter()
            .copied()
            .filter(|name| !tool_names.contains(name))
            .collect();

        assert!(
            missing.is_empty(),
            "tools/list is missing {} tool(s): {:?}. Found tools: {:?}",
            missing.len(),
            missing,
            tool_names
        );

        assert_eq!(
            tool_names.len(),
            15,
            "Expected exactly 15 tools in list, found {}: {:?}",
            tool_names.len(),
            tool_names
        );

        bridge.shutdown();
    });
}

// ---------------------------------------------------------------------------
// Mutation tool tests (write_file, exec) and ReadOnly rejection
// ---------------------------------------------------------------------------

/// Verify that `write_file` succeeds in a ReadWrite (Development) session.
///
/// After a successful write_file call, the MemoryWorkspace must contain the
/// written content at the specified path. This verifies the full round-trip:
/// protocol dispatch → handler → workspace write → observable state.
#[test]
fn mcp_write_file_succeeds_in_readwrite_session() {
    with_default_timeout(|| {
        let session = test_session();

        // Keep a direct reference to the MemoryWorkspace for post-write assertions.
        // The Arc<MemoryWorkspace> is coerced to Arc<dyn Workspace> for the bridge,
        // but we retain the typed reference to call get_file() after the write.
        let mem_ws = Arc::new(
            MemoryWorkspace::new(PathBuf::from("/test/repo"))
                .with_file("PROMPT.md", "# Test\n## Goal\nTest\n## Acceptance\n- Pass")
                .with_file("test.txt", "Hello, World!"),
        );
        // Guard: no real git mutations allowed in MCP integration tests.
        assert_no_real_git_mutations(mem_ws.root());
        let workspace: Arc<dyn ralph_workflow::workspace::Workspace> = mem_ws.clone();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        let expected_content = "hello from write_file e2e test";
        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "write_file",
                "arguments": {
                    "path": "output.txt",
                    "content": expected_content
                }
            },
            "id": 2
        });
        let response = send_mcp_request(&mut stream, call_request);

        assert!(
            response.result.is_some(),
            "write_file must succeed in ReadWrite sessions, got error: {:?}",
            response.error
        );

        // Assert the content was actually written to the MemoryWorkspace.
        let written = mem_ws.get_file("output.txt");
        assert!(
            written.is_some(),
            "write_file must write to the workspace: 'output.txt' not found in MemoryWorkspace"
        );
        assert_eq!(
            written.as_deref(),
            Some(expected_content),
            "write_file must store the correct content in the workspace"
        );

        bridge.shutdown();
    });
}

/// Verify that `write_file` is rejected in a ReadOnly (Planning) session.
///
/// write_file is a mutating operation. ReadOnly mode must block it before any
/// capability check. The error code must be ReadOnlyMode.
#[test]
fn mcp_write_file_rejected_in_readonly_session() {
    with_default_timeout(|| {
        let session = planning_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "write_file",
                "arguments": {
                    "path": "output.txt",
                    "content": "should be rejected"
                }
            },
            "id": 2
        });
        let response = send_mcp_request(&mut stream, call_request);

        // Must return an error (not succeed)
        assert!(
            response.error.is_some(),
            "write_file must be rejected in ReadOnly sessions, got result: {:?}",
            response.result
        );

        let error = response.error.unwrap();
        assert_eq!(
            error.code, -32000,
            "ReadOnly denial must use code -32000, got: {}",
            error.code
        );

        // Error data must contain ReadOnlyMode code
        let data_str = serde_json::to_string(&error.data.unwrap_or_default()).unwrap_or_default();
        assert!(
            data_str.contains("ReadOnlyMode"),
            "Error data must contain 'ReadOnlyMode' code, got: {data_str}"
        );

        bridge.shutdown();
    });
}

/// Verify that `exec` is rejected in a ReadOnly (Planning) session.
///
/// exec is a mutating operation (ProcessExecBounded). ReadOnly mode must block it
/// before any capability check. The error code must be ReadOnlyMode.
#[test]
fn mcp_exec_rejected_in_readonly_session() {
    with_default_timeout(|| {
        let session = planning_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "exec",
                "arguments": {
                    "command": "echo",
                    "args": ["should be rejected"]
                }
            },
            "id": 2
        });
        let response = send_mcp_request(&mut stream, call_request);

        assert!(
            response.error.is_some(),
            "exec must be rejected in ReadOnly sessions, got result: {:?}",
            response.result
        );

        let error = response.error.unwrap();
        assert_eq!(
            error.code, -32000,
            "ReadOnly denial must use code -32000, got: {}",
            error.code
        );

        let data_str = serde_json::to_string(&error.data.unwrap_or_default()).unwrap_or_default();
        assert!(
            data_str.contains("ReadOnlyMode"),
            "Error data must contain 'ReadOnlyMode' code, got: {data_str}"
        );

        bridge.shutdown();
    });
}

/// Verify that file access outside the workspace root is rejected with OutsideRootDir.
///
/// The path boundary check enforces that all file operations resolve within root_dir.
/// An absolute path outside the workspace root must be rejected regardless of session type.
#[test]
fn mcp_path_outside_root_dir_rejected() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        // /etc/passwd is outside the workspace root /test/repo.
        // The path boundary check must deny this before any handler is invoked.
        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {"path": "/etc/passwd"}
            },
            "id": 2
        });
        let response = send_mcp_request(&mut stream, call_request);

        assert!(
            response.error.is_some(),
            "read_file with path outside workspace root must return an error, \
             got result: {:?}",
            response.result
        );

        let error = response.error.unwrap();
        assert_eq!(
            error.code, -32000,
            "Path boundary denial must use code -32000, got: {}",
            error.code
        );

        let data_str = serde_json::to_string(&error.data.unwrap_or_default()).unwrap_or_default();
        assert!(
            data_str.contains("OutsideRootDir"),
            "Error data must contain 'OutsideRootDir' code, got: {data_str}"
        );

        bridge.shutdown();
    });
}

/// Test that audit records are emitted when tool access is denied and can be drained.
///
/// Audit records are only emitted for denied operations (correct access control behavior).
/// This test triggers a denial by accessing a path outside the workspace root.
#[test]
fn mcp_audit_records_emitted_when_access_denied() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        let mut stream = connect(&bridge);

        // Initialize
        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let _ = send_mcp_request(&mut stream, init_request);

        // Drain any records from initialization
        let _initial_records = bridge.drain_audit_records();

        // Call read_file with a path OUTSIDE the workspace root to trigger a denial.
        // The test workspace root is /test/repo, so /etc/passwd is outside the root.
        let call_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {"path": "/etc/passwd"}
            },
            "id": 2
        });
        let response = send_mcp_request(&mut stream, call_request);

        // The call should be denied (not succeed)
        assert!(
            response.error.is_some() || response.result.is_none(),
            "read_file with path outside workspace should be denied, got: {:?}",
            response
        );

        // Drain audit records after denied tool call
        let records = bridge.drain_audit_records();

        // Verify audit record was emitted for the denied access
        assert!(
            !records.is_empty(),
            "Audit records should be emitted for denied tool calls, got empty vector"
        );

        // Verify record structure and that it contains session_id from the test
        for record in &records {
            assert!(
                record.session_id.as_str().contains("mcp-e2e"),
                "Audit record should have session_id containing 'mcp-e2e', got: {:?}",
                record
            );
        }

        bridge.shutdown();
    });
}
