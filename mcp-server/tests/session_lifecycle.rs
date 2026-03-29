//! Session lifecycle tests for MCP server.
//!
//! These tests verify server state transitions and lifecycle behavior
//! without requiring real filesystem, git, or network operations.

use mcp_server::dispatch::access::{AccessDecision, McpCapability};
use mcp_server::dispatch::host::DirEntry;
use mcp_server::dispatch::ToolRegistry;
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::{McpServer, ServerState};
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
fn test_server_starts_uninitialized() {
    let server = make_server();

    // Any method except initialize should fail before initialization
    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: serde_json::json!(1),
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
        id: serde_json::json!(1),
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
        id: serde_json::json!(1),
    };
    let (_, state1) = server.handle_request(req1, ServerState::Uninitialized);
    assert_eq!(state1, ServerState::Ready);

    // Second initialize should also work (idempotent)
    let req2 = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: serde_json::json!(2),
    };
    let (response2, state2) = server.handle_request(req2, state1);

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
        id: serde_json::json!(1),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);

    // Now tools/list should work
    let list = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: serde_json::json!(2),
    };

    let (response, new_state) = server.handle_request(list, state);

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
        id: serde_json::json!(1),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);

    // Ping
    let ping = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "ping".to_string(),
        params: None,
        id: serde_json::json!(2),
    };

    let (response, _) = server.handle_request(ping, state);

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
        id: serde_json::json!("unique-id-123"),
    };

    let (response, _) = server.handle_request(init, ServerState::Uninitialized);

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
        id: serde_json::json!(1),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // First tool call
    let tool1 = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({"name": "nonexistent", "arguments": {}})),
        id: serde_json::json!(2),
    };
    let (_, state2) = server.handle_request(tool1, state);
    assert_eq!(state2, ServerState::Ready);

    // Second tool call - should still work (not initialized again)
    let tool2 = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({"name": "nonexistent", "arguments": {}})),
        id: serde_json::json!(3),
    };
    let (response3, _) = server.handle_request(tool2, state2);

    // Should still be in error state (not re-initialized)
    assert!(response3.error.is_some());
}
