//! End-to-end MCP protocol integration tests.
//!
//! These tests verify the full MCP stack works correctly over Unix socket transport,
//! using Ralph's actual SessionBridge and MemoryWorkspace for isolation.
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

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use ralph_workflow::agents::session::{AgentSession, SessionDrain};
use ralph_workflow::mcp_server::session_bridge::SessionBridge;
use ralph_workflow::workspace::MemoryWorkspace;

use crate::test_timeout::with_default_timeout;

// ---------------------------------------------------------------------------
// Helper: MCP message framing over Unix socket
// ---------------------------------------------------------------------------

/// Send a JSON-RPC request with Content-Length framing and read the response.
fn send_mcp_request(
    stream: &mut std::os::unix::net::UnixStream,
    request: serde_json::Value,
) -> String {
    use std::io::{Read, Write};

    let bytes = serde_json::to_vec(&request).unwrap();
    write!(stream, "Content-Length: {}\r\n\r\n", bytes.len()).unwrap();
    stream.write_all(&bytes).unwrap();
    stream.flush().unwrap();

    // Read headers until blank line
    let mut header = Vec::new();
    loop {
        let mut buf = [0u8; 1];
        stream.read_exact(&mut buf).expect("Read error");
        header.push(buf[0]);
        if header.ends_with(b"\r\n\r\n") {
            break;
        }
    }

    // Parse Content-Length
    let header_str = String::from_utf8_lossy(&header);
    let content_length = header_str
        .lines()
        .find(|l| l.starts_with("Content-Length:"))
        .and_then(|l| {
            l.strip_prefix("Content-Length:")
                .unwrap()
                .trim()
                .parse::<usize>()
                .ok()
        })
        .expect("Missing Content-Length header");

    // Read body
    let mut body = vec![0u8; content_length];
    stream.read_exact(&mut body).unwrap();
    String::from_utf8(body).unwrap()
}

/// Parse a JSON-RPC response.
fn parse_response(response_str: &str) -> mcp_server::protocol::JsonRpcResponse {
    serde_json::from_str(response_str).expect("Response should be valid JSON")
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
    Arc::new(workspace)
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

        // Connect via Unix socket
        let socket_path = bridge.socket_path().clone();
        let mut stream =
            std::os::unix::net::UnixStream::connect(&socket_path).expect("Failed to connect");
        stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
        stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

        // Send initialize request
        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });

        let response_str = send_mcp_request(&mut stream, init_request);
        let response = parse_response(&response_str);

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

        // Cleanup
        drop(stream);
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

        // Connect via Unix socket
        let socket_path = bridge.socket_path().clone();
        let mut stream =
            std::os::unix::net::UnixStream::connect(&socket_path).expect("Failed to connect");
        stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
        stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

        // First initialize
        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        send_mcp_request(&mut stream, init_request);

        // Small delay to ensure server processes initialize
        std::thread::sleep(Duration::from_millis(100));

        // Request tools list
        let list_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2
        });

        let response_str = send_mcp_request(&mut stream, list_request);
        let response = parse_response(&response_str);

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

        // Cleanup
        drop(stream);
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

        // Connect via Unix socket
        let socket_path = bridge.socket_path().clone();
        let mut stream =
            std::os::unix::net::UnixStream::connect(&socket_path).expect("Failed to connect");
        stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
        stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

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

        let response_str = send_mcp_request(&mut stream, call_request);
        let response = parse_response(&response_str);

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

        // Cleanup
        drop(stream);
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
/// This verifies the complete request-response cycle over Unix socket.
#[test]
fn mcp_full_protocol_flow_read_file() {
    with_default_timeout(|| {
        let session = test_session();
        let workspace = test_workspace();

        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("Bridge should start");

        // Connect via Unix socket
        let socket_path = bridge.socket_path().clone();
        let mut stream =
            std::os::unix::net::UnixStream::connect(&socket_path).expect("Failed to connect");
        stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
        stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

        // Step 1: Initialize
        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let response_str = send_mcp_request(&mut stream, init_request);
        let response = parse_response(&response_str);
        assert!(
            response.result.is_some(),
            "initialize should succeed, got error: {:?}",
            response.error
        );

        // Small delay to ensure server processes initialize
        std::thread::sleep(Duration::from_millis(100));

        // Step 2: List tools
        let list_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2
        });
        let response_str = send_mcp_request(&mut stream, list_request);
        let response = parse_response(&response_str);
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
        let response_str = send_mcp_request(&mut stream, call_request);
        let response = parse_response(&response_str);

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

        // Cleanup
        drop(stream);
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

        // Connect via Unix socket
        let socket_path = bridge.socket_path().clone();
        let mut stream =
            std::os::unix::net::UnixStream::connect(&socket_path).expect("Failed to connect");
        stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
        stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

        // Initialize
        let init_request = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        send_mcp_request(&mut stream, init_request);
        std::thread::sleep(Duration::from_millis(100));

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
        let response_str = send_mcp_request(&mut stream, call_request);
        let response = parse_response(&response_str);

        // The call should be denied (not succeed)
        assert!(
            response.error.is_some() || response.result.is_none(),
            "read_file with path outside workspace should be denied, got: {:?}",
            response
        );

        // Small delay to ensure audit record is emitted
        std::thread::sleep(Duration::from_millis(100));

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

        // Cleanup
        drop(stream);
        bridge.shutdown();
    });
}
