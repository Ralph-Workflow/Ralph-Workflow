//! Behavioral integration tests for MCP server communication.
//!
//! These tests verify observable behavior at the protocol level: a consumer
//! sends raw JSON-RPC messages and asserts on raw JSON-RPC responses without
//! reaching into internal `McpServer` implementation details.
//!
//! The tests use only:
//! - `ralph_workflow::mcp_server::session_bridge::SessionBridge` (public API)
//! - `ralph_workflow::agents::session::{AgentSession, SessionDrain}` (public API)
//! - `ralph_workflow::workspace::memory_workspace::MemoryWorkspace` (public API)
//! - `ralph_workflow::workspace::Workspace` (public API)
//! - Raw JSON value I/O
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use mcp_server::io::ServerState;
use mcp_server::protocol::JsonRpcRequest;
use ralph_workflow::agents::session::{AgentSession, SessionDrain};
use ralph_workflow::mcp_server::session_bridge::SessionBridge;
use ralph_workflow::workspace::memory_workspace::MemoryWorkspace;
use ralph_workflow::workspace::Workspace;
use std::path::Path;
use std::sync::Arc;
use test_helpers::ProjectHeadGuard;

struct TestConnection<'a> {
    bridge: &'a SessionBridge,
    state: ServerState,
    pending_response: Option<serde_json::Value>,
}

fn start_bridge(
    run_id: &str,
    drain: SessionDrain,
    workspace: Arc<MemoryWorkspace>,
) -> SessionBridge {
    let session = AgentSession::for_drain(run_id.to_string(), drain, 1);
    let ws: Arc<dyn Workspace> = workspace;
    let mut bridge = SessionBridge::new(session, ws);
    bridge.start().expect("SessionBridge::start() must succeed");
    bridge
}

fn connect(bridge: &SessionBridge) -> TestConnection<'_> {
    TestConnection {
        bridge,
        state: ServerState::Uninitialized,
        pending_response: None,
    }
}

fn send_msg(connection: &mut TestConnection<'_>, msg: &serde_json::Value) {
    let request: JsonRpcRequest =
        serde_json::from_value(msg.clone()).expect("request must be valid JSON-RPC");
    let (response, state) = connection
        .bridge
        .handle_request_in_process(request, connection.state);
    connection.state = state;
    connection.pending_response =
        response.map(|resp| serde_json::to_value(resp).expect("response serializes"));
}

fn recv_msg(connection: &mut TestConnection<'_>) -> serde_json::Value {
    connection
        .pending_response
        .take()
        .expect("expected response frame")
}

/// Send initialize and return the parsed response.
fn initialize(connection: &mut TestConnection<'_>) -> serde_json::Value {
    send_msg(
        connection,
        &serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0.1"}
            },
            "id": 1
        }),
    );
    recv_msg(connection)
}

// ============================================================================
// Test 1: consumer_can_initialize
// ============================================================================

/// Verify that a consumer can initialize and receive correct serverInfo
/// and capabilities in the response.
#[test]
fn consumer_can_initialize() {
    with_default_timeout(|| {
        let _guard = ProjectHeadGuard::capture();
        let ws = Arc::new(MemoryWorkspace::new_test());
        let bridge = start_bridge("mcp-init", SessionDrain::Development, ws);
        let mut stream = connect(&bridge);

        let response = initialize(&mut stream);

        assert!(
            response.get("error").is_none(),
            "initialize must not return an error, got: {}",
            response
        );
        let result = &response["result"];
        assert_eq!(
            result["serverInfo"]["name"], "ralph-mcp",
            "serverInfo.name must be 'ralph-mcp'"
        );
        assert_eq!(
            result["protocolVersion"], "2024-11-05",
            "protocolVersion must match MCP_PROTOCOL_VERSION"
        );
        assert!(
            result.get("capabilities").is_some(),
            "capabilities must be present in initialize response"
        );
        assert!(
            result["capabilities"].get("tools").is_some(),
            "capabilities.tools must be present"
        );
    });
}

// ============================================================================
// Test 2: consumer_can_list_tools
// ============================================================================

/// Verify that after initialize, tools/list returns the expected tool names
/// including ralph_submit_artifact and read_file.
#[test]
fn consumer_can_list_tools() {
    with_default_timeout(|| {
        let _guard = ProjectHeadGuard::capture();
        let ws = Arc::new(MemoryWorkspace::new_test());
        let bridge = start_bridge("mcp-tools-list", SessionDrain::Development, ws);
        let mut stream = connect(&bridge);
        initialize(&mut stream);

        send_msg(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": 2
            }),
        );
        let response = recv_msg(&mut stream);

        assert!(
            response.get("error").is_none(),
            "tools/list must not error: {}",
            response
        );
        let tools = response["result"]["tools"]
            .as_array()
            .expect("result.tools must be an array");
        let names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();

        assert!(
            names.contains(&"read_file"),
            "must include read_file, got: {:?}",
            names
        );
        assert!(
            names.contains(&"ralph_submit_artifact"),
            "must include ralph_submit_artifact, got: {:?}",
            names
        );
    });
}

// ============================================================================
// Test 3: consumer_can_call_read_file_tool
// ============================================================================

/// Verify that read_file returns file content without isError when
/// the file exists in the workspace.
#[test]
fn consumer_can_call_read_file_tool() {
    with_default_timeout(|| {
        let _guard = ProjectHeadGuard::capture();
        let ws = Arc::new(MemoryWorkspace::new_test());
        // Pre-seed a file in the workspace
        ws.write(Path::new("test_file.txt"), "Hello, MCP world!")
            .expect("pre-seed test file");

        let bridge = start_bridge("mcp-read-file", SessionDrain::Development, Arc::clone(&ws));
        let mut stream = connect(&bridge);
        initialize(&mut stream);

        send_msg(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "read_file",
                    "arguments": {"path": "test_file.txt"}
                },
                "id": 3
            }),
        );
        let response = recv_msg(&mut stream);

        assert!(
            response.get("error").is_none(),
            "read_file must not return a JSON-RPC error: {}",
            response
        );
        let result = &response["result"];
        assert!(
            result["isError"].as_bool() != Some(true),
            "isError must not be true for successful read, got: {}",
            result
        );
        let content = result["content"]
            .as_array()
            .expect("result.content must be an array");
        let text = content
            .iter()
            .find(|c| c.get("type").and_then(|t| t.as_str()) == Some("text"))
            .expect("must have text content");
        let text_content = text["text"].as_str().expect("text content must be string");
        assert!(
            text_content.contains("Hello, MCP world!"),
            "response must contain file content, got: {}",
            text_content
        );
    });
}

// ============================================================================
// Test 4: consumer_gets_error_for_missing_file
// ============================================================================

/// Verify that read_file returns isError:true when the file does not exist.
#[test]
fn consumer_gets_error_for_missing_file() {
    with_default_timeout(|| {
        let _guard = ProjectHeadGuard::capture();
        let ws = Arc::new(MemoryWorkspace::new_test());
        let bridge = start_bridge("mcp-missing-file", SessionDrain::Development, ws);
        let mut stream = connect(&bridge);
        initialize(&mut stream);

        send_msg(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "read_file",
                    "arguments": {"path": "nonexistent_file_xyz.txt"}
                },
                "id": 4
            }),
        );
        let response = recv_msg(&mut stream);

        // ExecutionError returns JSON-RPC error with code -32000 (per mcp-server protocol)
        assert!(
            response.get("error").is_some(),
            "ExecutionError must return JSON-RPC error, got: {}",
            response
        );
        let error = response["error"]
            .as_object()
            .expect("error must be an object");
        assert_eq!(
            error.get("code").and_then(|c| c.as_i64()).unwrap_or(0),
            -32000,
            "ExecutionError must have code -32000, got: {:#?}",
            error
        );
        assert!(
            error
                .get("message")
                .and_then(|m| m.as_str())
                .map(|m| m.contains("Tool error"))
                .unwrap_or(false),
            "Error message should contain 'Tool error', got: {:#?}",
            error
        );
    });
}

// ============================================================================
// Test 5: consumer_gets_capability_denied_for_write_in_readonly_session
// ============================================================================

/// Verify that Planning session denies write_file with a JSON-RPC protocol error.
/// Per RFC-009, capability denials are protocol-level JSON-RPC errors, not tool-level isError responses.
#[test]
fn consumer_gets_capability_denied_for_write_in_readonly_session() {
    with_default_timeout(|| {
        let _guard = ProjectHeadGuard::capture();
        let ws = Arc::new(MemoryWorkspace::new_test());
        // Pre-seed a file so it's treated as tracked
        ws.write(Path::new("src/lib.rs"), "pub fn foo() {}")
            .expect("pre-seed tracked file");

        let bridge = start_bridge("mcp-cap-denied", SessionDrain::Planning, Arc::clone(&ws));
        let mut stream = connect(&bridge);
        initialize(&mut stream);

        send_msg(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "write_file",
                    "arguments": {"path": "src/lib.rs", "content": "changed"}
                },
                "id": 5
            }),
        );
        let response = recv_msg(&mut stream);

        // Per RFC-009, capability denial must be a JSON-RPC protocol error
        assert!(
            response.get("error").is_some(),
            "capability denied must be a JSON-RPC protocol error, got: {}",
            response
        );
        let error = response["error"]
            .as_object()
            .expect("error must be an object");
        assert!(
            error
                .get("message")
                .and_then(|m| m.as_str())
                .map(|m| m.contains("denied") || m.contains(" Denied "))
                .unwrap_or(false),
            "error message should indicate access denied, got: {:#?}",
            error
        );
    });
}

// ============================================================================
// Test 6: proxy_bridges_consumer_to_server
// ============================================================================

/// Verify that a proxy-style consumer flow can initialize and list tools through
/// the public session bridge seam without depending on OS sockets.
#[test]
fn proxy_bridges_consumer_to_server() {
    with_default_timeout(|| {
        let _guard = ProjectHeadGuard::capture();
        let ws = Arc::new(MemoryWorkspace::new_test());
        let bridge = start_bridge("mcp-proxy-test", SessionDrain::Development, Arc::clone(&ws));
        let mut stream = connect(&bridge);

        let response = initialize(&mut stream);
        assert!(
            response.get("error").is_none(),
            "initialize must not error through proxy, got: {}",
            response
        );
        let result = &response["result"];
        assert!(
            result.get("serverInfo").is_some(),
            "initialize response must contain serverInfo, got: {}",
            result
        );
        assert_eq!(
            result["serverInfo"]["name"], "ralph-mcp",
            "serverInfo.name must be 'ralph-mcp'"
        );
        send_msg(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": 2
            }),
        );
        let response = recv_msg(&mut stream);
        assert!(
            response.get("error").is_none(),
            "tools/list must not error through proxy-style flow, got: {}",
            response
        );
        assert!(
            !response["result"]["tools"]
                .as_array()
                .expect("tools must be an array")
                .is_empty(),
            "proxy-style flow must surface registered tools"
        );
    });
}

// ============================================================================
// Test 7: consumer_can_submit_artifact_through_mcp
// ============================================================================

/// Verify that a consumer can submit an artifact through the MCP protocol.
///
/// This is the primary end-to-end regression test for the ralph_submit_artifact
/// tool. It proves that:
/// 1. The tool is registered and reachable via the MCP protocol
/// 2. A valid plan artifact is accepted and written to the workspace (`.agent/tmp/plan.json`)
/// 3. The response indicates success (accepted: true)
#[test]
fn consumer_can_submit_artifact_through_mcp() {
    with_default_timeout(|| {
        let _guard = ProjectHeadGuard::capture();
        let ws = Arc::new(MemoryWorkspace::new_test());
        let bridge = start_bridge(
            "mcp-submit-artifact",
            SessionDrain::Development,
            Arc::clone(&ws),
        );
        let mut stream = connect(&bridge);
        initialize(&mut stream);

        // Submit a minimal but valid plan artifact (matching the plan schema)
        let valid_plan = serde_json::json!({
            "summary": {
                "context": "MCP integration test plan",
                "scope_items": [
                    {"text": "Verify MCP artifact submission works"},
                    {"text": "Confirm workspace write succeeds"},
                    {"text": "Assert response indicates acceptance"}
                ]
            },
            "steps": [
                {
                    "number": 1,
                    "title": "Submit artifact via MCP",
                    "content": "Call ralph_submit_artifact with a valid plan"
                }
            ],
            "critical_files": {
                "primary_files": [
                    {"path": "src/main.rs", "action": "modify"}
                ]
            },
            "risks_mitigations": [
                {"risk": "Tool not registered", "mitigation": "This test detects that"}
            ],
            "verification_strategy": [
                {"method": "This test", "expected_outcome": "Tool call succeeds"}
            ]
        });

        send_msg(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "ralph_submit_artifact",
                    "arguments": {
                        "artifact_type": "plan",
                        "content": serde_json::to_string(&valid_plan).expect("plan serializes")
                    }
                },
                "id": 10
            }),
        );
        let response = recv_msg(&mut stream);

        // Must not return a JSON-RPC protocol error (tool not found, etc.)
        assert!(
            response.get("error").is_none(),
            "ralph_submit_artifact must not return a JSON-RPC error — \
             this likely means the tool is not registered. Got: {}",
            response
        );

        // Result must exist and indicate acceptance
        let result = &response["result"];
        assert!(
            result.get("content").is_some(),
            "result must contain content array, got: {result}"
        );
        let content = result["content"]
            .as_array()
            .expect("result.content must be an array");
        let text = content
            .iter()
            .find(|c| c.get("type").and_then(|t| t.as_str()) == Some("text"))
            .expect("must have text content");
        let text_str = text["text"].as_str().expect("text must be string");
        assert!(
            text_str.contains("\"accepted\": true"),
            "artifact must be accepted, got response text: {text_str}"
        );

        // Verify artifact was persisted to workspace (.agent/tmp/plan.json per AGENT_TMP constant)
        let artifact_path = Path::new(".agent/tmp/plan.json");
        assert!(
            ws.exists(artifact_path),
            "artifact must be written to .agent/tmp/plan.json in workspace"
        );
    });
}

// ============================================================================
// Test 8: consumer_gets_tool_not_found_for_unknown_tool
// ============================================================================

/// Verify that calling an unknown tool returns a JSON-RPC error with code -32601.
///
/// Per the dispatch layer: unknown tool names map to `ToolError::NotFound`, which the
/// I/O layer converts to JSON-RPC -32601 ("Method not found" semantics). This is
/// distinct from -32000, which is used for `ToolNotAllowed` (blocked by ToolFilter).
#[test]
fn consumer_gets_tool_not_found_for_unknown_tool() {
    with_default_timeout(|| {
        let _guard = ProjectHeadGuard::capture();
        let ws = Arc::new(MemoryWorkspace::new_test());
        let bridge = start_bridge("mcp-unknown-tool", SessionDrain::Development, ws);
        let mut stream = connect(&bridge);
        initialize(&mut stream);

        send_msg(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "nonexistent_tool_xyz",
                    "arguments": {}
                },
                "id": 99
            }),
        );
        let response = recv_msg(&mut stream);

        assert!(
            response.get("error").is_some(),
            "unknown tool must return JSON-RPC error, got: {response}"
        );
        let error = response["error"].as_object().expect("error must be object");
        assert_eq!(
            error.get("code").and_then(|c| c.as_i64()).unwrap_or(0),
            -32601,
            "unknown tool must use JSON-RPC -32601 (method not found), got: {error:#?}"
        );
    });
}
