//! Protocol framing tests for MCP server.
//!
//! These tests verify the Content-Length framing protocol and the initialize
//! handshake without requiring real filesystem, git, or network operations.

use mcp_server::dispatch::access::{AccessDecision, McpCapability};
use mcp_server::dispatch::host::DirEntry;
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::fake::FakeTransport;
use mcp_server::io::McpStream;
use mcp_server::io::{McpServer, ServerState};
use mcp_server::protocol::JsonRpcRequest;
use mcp_server::ToolRegistry;
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
    fn is_parallel_worker(&self) -> bool {
        false
    }
    fn check_edit_area(&self, _path: &str) -> AccessDecision {
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
    McpServer::new(session, config, workspace, registry)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[test]
fn test_initialize_handshake() {
    let mut transport = FakeTransport::new();
    let server = make_server();

    // Client sends initialize request
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({
            "protocolVersion": "2024-11-05",
            "clientInfo": { "name": "test-client", "version": "1.0" }
        })),
        id: serde_json::json!(1),
    };

    // Inject request and process
    transport.inject_request(init_request);
    let request = transport.read_request().unwrap().unwrap();

    let (response, state) = server.handle_request(request, ServerState::Uninitialized);

    // Verify response
    assert!(response.result.is_some(), "Expected success result");
    assert!(response.error.is_none(), "Expected no error");
    assert_eq!(
        state,
        ServerState::Ready,
        "Server should be in Ready state after initialize"
    );

    // Verify response content
    let result = response.result.unwrap();
    assert_eq!(result["protocolVersion"], "2024-11-05");
    assert!(result["capabilities"]["tools"].is_object());
    assert_eq!(result["serverInfo"]["name"], "ralph-mcp");
}

#[test]
fn test_initialize_with_different_protocol_version() {
    let mut transport = FakeTransport::new();
    let server = make_server();

    // Client requests different protocol version
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({
            "protocolVersion": "2024-10-01"
        })),
        id: serde_json::json!(1),
    };

    transport.inject_request(init_request);
    let request = transport.read_request().unwrap().unwrap();

    let (response, state) = server.handle_request(request, ServerState::Uninitialized);

    // Server should still transition to Ready
    assert!(response.result.is_some());
    assert_eq!(state, ServerState::Ready);
}

#[test]
fn test_initialize_missing_params() {
    let mut transport = FakeTransport::new();
    let server = make_server();

    // Initialize with params but missing required fields
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({})), // Empty params - missing protocolVersion
        id: serde_json::json!(1),
    };

    transport.inject_request(init_request);
    let request = transport.read_request().unwrap().unwrap();

    let (response, _state) = server.handle_request(request, ServerState::Uninitialized);

    // Should return error for missing required params
    assert!(
        response.error.is_some(),
        "Expected error for missing protocolVersion"
    );
}

#[test]
fn test_methods_require_initialization() {
    let server = make_server();

    // Try to call tools/list without initialization
    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: serde_json::json!(1),
    };

    let (response, state) = server.handle_request(request, ServerState::Uninitialized);

    // Should get error about not being initialized
    assert!(response.error.is_some(), "Expected error response");
    let error = response.error.unwrap();
    assert_eq!(error.code, -32001, "Should be 'not initialized' error");
    assert_eq!(state, ServerState::Uninitialized, "State should not change");
}

#[test]
fn test_unknown_method_returns_method_not_found() {
    let server = make_server();

    // First initialize
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: serde_json::json!(1),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    // Then try unknown method
    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "unknown/method".to_string(),
        params: None,
        id: serde_json::json!(2),
    };

    let (response, _) = server.handle_request(request, state);

    assert!(response.error.is_some());
    let error = response.error.unwrap();
    assert_eq!(error.code, -32601, "Should be method not found");
}

#[test]
fn test_ping_after_initialization() {
    let server = make_server();

    // First initialize
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: serde_json::json!(1),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    // Then ping
    let ping_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "ping".to_string(),
        params: None,
        id: serde_json::json!(2),
    };

    let (response, new_state) = server.handle_request(ping_request, state);

    assert!(response.result.is_some());
    assert_eq!(new_state, ServerState::Ready);
}

#[test]
fn test_response_has_correct_jsonrpc_version() {
    let server = make_server();

    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: serde_json::json!(42),
    };

    let (response, _) = server.handle_request(request, ServerState::Uninitialized);

    assert_eq!(response.jsonrpc, "2.0");
    assert_eq!(response.id, serde_json::json!(42));
}
