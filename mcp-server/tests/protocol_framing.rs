//! Protocol framing tests for MCP server.
//!
//! These tests verify the Content-Length framing protocol and the initialize
//! handshake without requiring real filesystem, git, or network operations.

use mcp_server::dispatch::access::{AccessDecision, McpCapability};
use mcp_server::dispatch::host::DirEntry;
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::fake::{FakeTransport, FakeTransportPair};
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
        id: Some(serde_json::json!(1)),
    };

    // Inject request and process
    transport.inject_request(init_request);
    let request = transport.read_request().unwrap().unwrap();

    let (response, state) = server.handle_request(request, ServerState::Uninitialized);
    let response = response.expect("handle_request should return a response for non-notification");

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
        id: Some(serde_json::json!(1)),
    };

    transport.inject_request(init_request);
    let request = transport.read_request().unwrap().unwrap();

    let (response, state) = server.handle_request(request, ServerState::Uninitialized);
    let response = response.expect("handle_request should return a response for non-notification");

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
        id: Some(serde_json::json!(1)),
    };

    transport.inject_request(init_request);
    let request = transport.read_request().unwrap().unwrap();

    let (response, _state) = server.handle_request(request, ServerState::Uninitialized);
    let response = response.expect("handle_request should return a response for non-notification");

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
        id: Some(serde_json::json!(1)),
    };

    let (response, state) = server.handle_request(request, ServerState::Uninitialized);
    let response = response.expect("handle_request should return a response for non-notification");

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
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    // Then try unknown method
    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "unknown/method".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(request, state);
    let response = response.expect("handle_request should return a response for non-notification");

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
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    // Then ping
    let ping_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "ping".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };

    let (response, new_state) = server.handle_request(ping_request, state);
    let response = response.expect("handle_request should return a response for non-notification");

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
        id: Some(serde_json::json!(42)),
    };

    let (response, _) = server.handle_request(request, ServerState::Uninitialized);
    let response = response.expect("handle_request should return a response for non-notification");

    assert_eq!(response.jsonrpc, "2.0");
    assert_eq!(response.id, serde_json::json!(42));
}

#[test]
fn test_notification_returns_no_response() {
    let server = make_server();

    // Send a notification (id is null) - should NOT get a response
    let notification = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "ping".to_string(),
        params: None,
        id: None, // null id means notification per JSON-RPC 2.0 spec
    };

    let (response, state) = server.handle_request(notification, ServerState::Ready);

    // Notifications should not get a response
    assert!(
        response.is_none(),
        "Notifications (id: null) should not receive a response"
    );
    // State should remain unchanged
    assert_eq!(state, ServerState::Ready);
}

#[test]
fn test_notification_does_not_transition_state() {
    let server = make_server();

    // Send initialize as notification - should NOT transition state
    let notification = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: None, // null id means notification
    };

    let (response, state) = server.handle_request(notification, ServerState::Uninitialized);

    // No response for notification
    assert!(
        response.is_none(),
        "Initialize notification should not receive a response"
    );
    // State should NOT change because initialize as notification doesn't process
    assert_eq!(
        state,
        ServerState::Uninitialized,
        "Notification should not transition server state"
    );
}

#[test]
fn test_large_payload_framing() {
    use std::io::Cursor;

    // Create a payload larger than 64KB by using a large string in params
    let large_string = "x".repeat(70_000);
    let request_payload = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "ralph_write_file",
            "arguments": {
                "path": "/tmp/large_file.txt",
                "content": large_string
            }
        },
        "id": 1
    });

    let body = serde_json::to_vec(&request_payload).unwrap();
    assert!(
        body.len() > 65_536,
        "Test payload should be > 64KB, got {} bytes",
        body.len()
    );

    // Format with Content-Length framing
    let framed = format!("Content-Length: {}\r\n\r\n", body.len());
    let mut input = framed.into_bytes();
    input.extend_from_slice(&body);

    let mut cursor = Cursor::new(input);
    let result = mcp_server::io::transport::read_framed_jsonrpc(&mut cursor);
    assert!(
        result.is_ok(),
        "Should handle large (>64KB) payloads correctly"
    );
    let request = result.unwrap().unwrap();
    assert_eq!(request.method, "tools/call");
    assert_eq!(request.id, Some(serde_json::json!(1)));

    let parsed_content = request
        .params
        .as_ref()
        .and_then(|params| params.get("arguments"))
        .and_then(|arguments| arguments.get("content"))
        .and_then(serde_json::Value::as_str)
        .expect("Expected params.arguments.content to be a string");

    assert_eq!(
        parsed_content.len(),
        large_string.len(),
        "Parsed payload content length must match original"
    );
    assert_eq!(
        parsed_content, large_string,
        "Parsed payload content must match original exactly"
    );
}

/// Test that FakeTransportPair drives a full initialize → tools/list exchange in-process.
///
/// This test proves the full bidirectional transport layer works correctly:
/// - Client injects requests into the pair's shared queues
/// - Server reads from the queue, processes the request, writes response
/// - Client reads back the response
///
/// No real OS sockets, filesystem, or processes are used. This is a deterministic
/// in-process test of the full protocol framing + JSON-RPC dispatch chain.
#[test]
fn fake_transport_pair_drives_initialize_and_tools_list_exchange() {
    let server = make_server();

    // Create a bidirectional pair: client side injects requests, server side implements McpStream
    let mut pair = FakeTransportPair::new();

    // Step 1: Client injects initialize request
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({
            "protocolVersion": "2024-11-05",
            "clientInfo": { "name": "fake-transport-test-client", "version": "1.0" }
        })),
        id: Some(serde_json::json!(1)),
    };
    pair.client.inject_request(init_request);

    // Step 2: Server reads the initialize request and handles it
    let req = pair
        .server
        .read_request()
        .expect("server transport must not error")
        .expect("server must receive the initialize request");

    let (init_resp, state) = server.handle_request(req, ServerState::Uninitialized);
    let init_resp = init_resp.expect("initialize must produce a response");

    // Step 3: Server writes response back to client via pair
    pair.server
        .write_response(&init_resp)
        .expect("server must write response without error");

    // Step 4: Client reads the initialize response
    let client_init_resp = pair
        .client
        .read_response()
        .expect("client must receive initialize response");

    assert!(
        client_init_resp.result.is_some(),
        "initialize must succeed, got error: {:?}",
        client_init_resp.error
    );
    assert_eq!(state, ServerState::Ready);
    let result = client_init_resp.result.unwrap();
    assert_eq!(
        result["protocolVersion"], "2024-11-05",
        "protocolVersion must match"
    );
    assert_eq!(
        result["serverInfo"]["name"], "ralph-mcp",
        "serverInfo.name must be 'ralph-mcp'"
    );

    // Step 5: Client injects tools/list request
    let list_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };
    pair.client.inject_request(list_request);

    // Step 6: Server reads tools/list and handles it
    let req = pair
        .server
        .read_request()
        .expect("server transport must not error")
        .expect("server must receive tools/list request");

    let (list_resp, _) = server.handle_request(req, state);
    let list_resp = list_resp.expect("tools/list must produce a response");

    // Step 7: Server writes response
    pair.server
        .write_response(&list_resp)
        .expect("server must write tools/list response");

    // Step 8: Client reads tools/list response
    let client_list_resp = pair
        .client
        .read_response()
        .expect("client must receive tools/list response");

    assert!(
        client_list_resp.result.is_some(),
        "tools/list must succeed, got error: {:?}",
        client_list_resp.error
    );
    let result = client_list_resp.result.unwrap();
    let tools = result["tools"].as_array().expect("tools must be an array");
    // Empty registry — no tools registered in make_server() — so list must be empty
    assert_eq!(
        tools.len(),
        0,
        "empty registry must return empty tools list, got: {tools:#?}"
    );

    // Verify no pending responses remain (clean queue state)
    assert!(
        !pair.client.has_pending_responses(),
        "no responses should remain in client queue after all reads"
    );
}

/// Test that fragmented reads are handled correctly.
///
/// This verifies that when the body bytes arrive in multiple chunks (partial reads),
/// the transport correctly reconstructs the full message before JSON parsing.
#[test]
fn test_content_length_partial_read() {
    use std::io::{BufRead, Read};

    // Create a simple request
    let request_payload = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "ping",
        "params": {},
        "id": 1
    });

    let body = serde_json::to_vec(&request_payload).unwrap();
    let content_length = body.len();

    // Format with Content-Length framing
    let header = format!("Content-Length: {}\r\n\r\n", content_length);
    let mut framed_data = header.into_bytes();
    framed_data.extend_from_slice(&body);

    // Create a custom BufRead that yields bytes in small chunks
    // to simulate partial reads / fragmented network packets
    let framed_data = Arc::new(framed_data);
    let chunk_size = 5; // Small chunks to force multiple reads

    struct ChunkReader {
        data: Arc<Vec<u8>>,
        pos: usize,
        chunk_size: usize,
        fill_end: usize, // tracked end of fill_buf buffer
    }

    impl ChunkReader {
        fn new(data: Arc<Vec<u8>>, chunk_size: usize) -> Self {
            Self {
                data: data.clone(),
                pos: 0,
                chunk_size,
                fill_end: 0,
            }
        }
    }

    impl Read for ChunkReader {
        fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
            if self.pos >= self.data.len() {
                return Ok(0);
            }
            let remaining = self.data.len() - self.pos;
            let to_read = std::cmp::min(self.chunk_size, remaining).min(buf.len());
            buf[..to_read].copy_from_slice(&self.data[self.pos..self.pos + to_read]);
            self.pos += to_read;
            Ok(to_read)
        }
    }

    impl BufRead for ChunkReader {
        fn fill_buf(&mut self) -> std::io::Result<&[u8]> {
            let end = std::cmp::min(self.pos + self.chunk_size, self.data.len());
            self.fill_end = end;
            Ok(&self.data[self.pos..end])
        }

        fn consume(&mut self, n: usize) {
            self.pos = std::cmp::min(self.pos + n, self.fill_end);
        }
    }

    let mut chunk_reader = ChunkReader::new(framed_data, chunk_size);
    let result = mcp_server::io::transport::read_framed_jsonrpc(&mut chunk_reader);

    assert!(
        result.is_ok(),
        "Should handle partial reads correctly, got: {:?}",
        result
    );
    let request = result.unwrap().unwrap();
    assert_eq!(request.method, "ping");
    assert_eq!(request.id, Some(serde_json::json!(1)));
}
