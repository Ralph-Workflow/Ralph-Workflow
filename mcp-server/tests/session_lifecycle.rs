//! Session lifecycle tests for MCP server.
//!
//! These tests verify server state transitions and lifecycle behavior
//! without requiring real filesystem, git, or network operations.

use mcp_server::dispatch::access::{AccessDecision, McpCapability};
use mcp_server::dispatch::host::DirEntry;
use mcp_server::dispatch::ToolRegistry;
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::{session_bridge::SessionBridge, McpServer, ServerState};
use mcp_server::protocol::JsonRpcRequest;
use std::path::Path;
use std::sync::Arc;

// ---------------------------------------------------------------------------
// Mock implementations
// ---------------------------------------------------------------------------

struct MockSession;
impl mcp_server::HostSession for MockSession {
    fn session_id(&self) -> &str {
        "test-session"
    }
    fn check_capability(&self, _cap: McpCapability) -> AccessDecision {
        AccessDecision::Allow
    }
}

struct MockWorkspace;
impl mcp_server::WorkspaceAdapter for MockWorkspace {
    fn read(&self, _path: &Path) -> Result<String, String> {
        Ok("test content".to_string())
    }
    fn write(&self, _path: &Path, _content: &str) -> Result<(), String> {
        Ok(())
    }
    fn exists(&self, _path: &Path) -> bool {
        true
    }
    fn read_dir(&self, _path: &Path) -> Result<Vec<DirEntry>, String> {
        Ok(vec![])
    }
}

fn make_server() -> McpServer {
    let session = Arc::new(MockSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(Path::new("/tmp").to_path_buf());
    let registry = ToolRegistry::new(vec![]);
    McpServer::new(session, config, workspace, registry, None)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[test]
fn test_server_starts_uninitialized() {
    let server = make_server();

    // Any method except initialize should fail before initialization
    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(1)),
    };

    let (_, state) = server.handle_request(request, ServerState::Uninitialized);

    // State should remain Uninitialized
    assert_eq!(state, ServerState::Uninitialized);
}

#[test]
fn test_initialize_transitions_to_ready() {
    let server = make_server();

    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };

    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    assert_eq!(state, ServerState::Ready);
}

#[test]
fn test_multiple_initialize_calls() {
    let server = make_server();

    // First initialize
    let req1 = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state1) = server.handle_request(req1, ServerState::Uninitialized);
    assert_eq!(state1, ServerState::Ready);

    // Second initialize should also work (idempotent)
    let req2 = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(2)),
    };
    let (response2, state2) = server.handle_request(req2, state1);
    let response2 =
        response2.expect("handle_request should return a response for non-notification");

    // Should still be Ready and return success
    assert_eq!(state2, ServerState::Ready);
    assert!(response2.result.is_some());
}

#[test]
fn test_methods_work_in_ready_state() {
    let server = make_server();

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);

    // Now tools/list should work
    let list = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };

    let (response, new_state) = server.handle_request(list, state);
    let response = response.expect("handle_request should return a response for non-notification");

    assert!(response.result.is_some());
    assert_eq!(new_state, ServerState::Ready);
}

#[test]
fn test_ping_works_in_ready_state() {
    let server = make_server();

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);

    // Ping
    let ping = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "ping".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(ping, state);
    let response = response.expect("handle_request should return a response for non-notification");

    assert!(response.result.is_some());
    // Ping returns null result
    assert_eq!(response.result.unwrap(), serde_json::Value::Null);
}

#[test]
fn test_request_id_preserved_in_response() {
    let server = make_server();

    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!("unique-id-123")),
    };

    let (response, _) = server.handle_request(init, ServerState::Uninitialized);
    let response = response.expect("handle_request should return a response for non-notification");

    assert_eq!(response.id, serde_json::json!("unique-id-123"));
}

#[test]
fn test_state_persists_across_requests() {
    let server = make_server();

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // First tool call
    let tool1 = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({"name": "nonexistent", "arguments": {}})),
        id: Some(serde_json::json!(2)),
    };
    let (_, state2) = server.handle_request(tool1, state);
    assert_eq!(state2, ServerState::Ready);

    // Second tool call - should still work (not initialized again)
    let tool2 = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({"name": "nonexistent", "arguments": {}})),
        id: Some(serde_json::json!(3)),
    };
    let (response3, _) = server.handle_request(tool2, state2);
    let response3 =
        response3.expect("handle_request should return a response for non-notification");

    // Should still be in error state (not re-initialized)
    assert!(response3.error.is_some());
}

#[test]
fn test_tools_call_before_initialize_returns_not_initialized_error() {
    let server = make_server();

    // Attempt tools/call before initialize
    let call_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({"name": "read_file", "arguments": {"path": "/tmp/x"}})),
        id: Some(serde_json::json!(1)),
    };

    let (response, state) = server.handle_request(call_request, ServerState::Uninitialized);
    let response = response.expect("tools/call must return a response even when not initialized");

    // Must return an error, not silently succeed
    assert!(
        response.error.is_some(),
        "tools/call before initialize must return an error"
    );
    let error = response.error.as_ref().unwrap();
    // Error code -32001 is NotInitialized per MCP spec
    assert_eq!(
        error.code, -32001,
        "Error code must be -32001 (NotInitialized), got: {}",
        error.code
    );

    // State must remain Uninitialized
    assert_eq!(state, ServerState::Uninitialized);
}

#[test]
fn test_tools_list_before_initialize_returns_not_initialized_error() {
    let server = make_server();

    let list_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(1)),
    };

    let (response, state) = server.handle_request(list_request, ServerState::Uninitialized);
    let response = response.expect("tools/list must return a response even when not initialized");

    assert!(
        response.error.is_some(),
        "tools/list before initialize must return an error"
    );
    let error = response.error.as_ref().unwrap();
    assert_eq!(
        error.code, -32001,
        "Error code must be -32001 (NotInitialized), got: {}",
        error.code
    );
    assert_eq!(state, ServerState::Uninitialized);
}

#[test]
fn test_dispatch_succeeds_only_after_initialize() {
    let server = make_server();

    // Before initialize: tools/list must fail
    let list_before = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(1)),
    };
    let (resp_before, state) = server.handle_request(list_before, ServerState::Uninitialized);
    let resp_before = resp_before.expect("must return response");
    assert!(
        resp_before.error.is_some(),
        "tools/list must fail before initialize"
    );

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(2)),
    };
    let (_, state) = server.handle_request(init, state);
    assert_eq!(state, ServerState::Ready);

    // After initialize: tools/list must succeed
    let list_after = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(3)),
    };
    let (resp_after, _) = server.handle_request(list_after, state);
    let resp_after = resp_after.expect("must return response");
    assert!(
        resp_after.result.is_some(),
        "tools/list must succeed after initialize"
    );
    assert!(
        resp_after.error.is_none(),
        "tools/list must not have error after initialize"
    );
}

#[test]
fn test_socket_cleanup_on_shutdown() {
    let session = Arc::new(MockSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(std::env::temp_dir());
    let registry = ToolRegistry::new(vec![]);

    let socket_path = {
        let bridge = SessionBridge::new(session, config, workspace, registry);
        let socket_path = bridge.socket_path().clone();

        std::fs::write(&socket_path, b"placeholder socket file")
            .expect("test should be able to create placeholder socket file");
        assert!(
            socket_path.exists(),
            "Socket path should exist before shutdown cleanup"
        );
        assert!(!bridge.is_shutdown(), "Bridge should start un-shutdown");

        bridge.shutdown();

        assert!(
            bridge.is_shutdown(),
            "Shutdown should set the bridge shutdown flag"
        );

        socket_path
    };

    assert!(
        !socket_path.exists(),
        "Socket file should be removed when the bridge is dropped"
    );
}
