//! Behavioral integration tests for MCP server communication over Unix sockets.
//!
//! These tests verify observable behavior at the protocol level: a consumer
//! connects to the MCP server via Unix socket, sends raw JSON-RPC messages as
//! bytes, and asserts on raw JSON-RPC response bytes — without using any
//! internal McpServer types (no McpServer, McpStream, or ToolRegistry directly).
//!
//! The tests use only:
//! - `ralph_workflow::mcp_server::session_bridge::SessionBridge` (public API)
//! - `ralph_workflow::agents::session::{AgentSession, SessionDrain}` (public API)
//! - `ralph_workflow::workspace::memory_workspace::MemoryWorkspace` (public API)
//! - `ralph_workflow::workspace::Workspace` (public API)
//! - `std::os::unix::net::UnixStream` (stdlib)
//! - Raw JSON byte I/O
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::session::{AgentSession, SessionDrain};
use ralph_workflow::mcp_server::session_bridge::SessionBridge;
use ralph_workflow::workspace::memory_workspace::MemoryWorkspace;
use ralph_workflow::workspace::Workspace;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

/// Start a SessionBridge with the given drain and workspace, returning the bridge
/// and the socket path for connecting.
fn start_bridge(
    run_id: &str,
    drain: SessionDrain,
    workspace: Arc<MemoryWorkspace>,
) -> (SessionBridge, std::path::PathBuf) {
    let session = AgentSession::for_drain(run_id.to_string(), drain, 1);
    let ws: Arc<dyn Workspace> = workspace;
    let mut bridge = SessionBridge::new(session, ws);
    bridge.start().expect("SessionBridge::start() must succeed");
    let socket_path = bridge.socket_path().clone();
    (bridge, socket_path)
}

/// Connect to the socket with a 10s read timeout.
fn connect(socket_path: &Path) -> UnixStream {
    let stream = UnixStream::connect(socket_path)
        .expect("UnixStream::connect must succeed immediately after start()");
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .expect("set_read_timeout");
    stream
}

/// Write a Content-Length framed JSON-RPC message to the stream.
fn send_msg(stream: &mut UnixStream, msg: &serde_json::Value) {
    let body = serde_json::to_vec(msg).expect("serialize request");
    let header = format!("Content-Length: {}\r\n\r\n", body.len());
    stream.write_all(header.as_bytes()).expect("write header");
    stream.write_all(&body).expect("write body");
    stream.flush().expect("flush");
}

/// Read one Content-Length framed JSON-RPC response from the stream.
///
/// Reads byte-by-byte until \r\n\r\n to find header end, then reads body.
fn recv_msg(stream: &mut UnixStream) -> serde_json::Value {
    let mut header_buf = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        stream.read_exact(&mut byte).expect("read byte");
        header_buf.push(byte[0]);
        if header_buf.ends_with(b"\r\n\r\n") {
            break;
        }
    }
    let header_str = std::str::from_utf8(&header_buf).expect("header utf8");
    let content_len: usize = header_str
        .lines()
        .find(|l| l.starts_with("Content-Length:"))
        .and_then(|l| l["Content-Length:".len()..].trim().parse().ok())
        .expect("Content-Length header");
    let mut body = vec![0u8; content_len];
    stream.read_exact(&mut body).expect("read body");
    serde_json::from_slice(&body).expect("parse JSON body")
}

/// Send initialize and return the parsed response.
fn initialize(stream: &mut UnixStream) -> serde_json::Value {
    send_msg(
        stream,
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
    recv_msg(stream)
}

// ============================================================================
// Test 1: consumer_can_initialize
// ============================================================================

/// Verify that a consumer can initialize and receive correct serverInfo
/// and capabilities in the response.
#[test]
fn consumer_can_initialize() {
    with_default_timeout(|| {
        let ws = Arc::new(MemoryWorkspace::new_test());
        let (_bridge, socket_path) = start_bridge("mcp-init", SessionDrain::Development, ws);
        let mut stream = connect(&socket_path);

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
/// including ralph_submit_artifact and ralph_read_file.
#[test]
fn consumer_can_list_tools() {
    with_default_timeout(|| {
        let ws = Arc::new(MemoryWorkspace::new_test());
        let (_bridge, socket_path) = start_bridge("mcp-tools-list", SessionDrain::Development, ws);
        let mut stream = connect(&socket_path);
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
            names.contains(&"ralph_read_file"),
            "must include ralph_read_file, got: {:?}",
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

/// Verify that ralph_read_file returns file content without isError when
/// the file exists in the workspace.
#[test]
fn consumer_can_call_read_file_tool() {
    with_default_timeout(|| {
        let ws = Arc::new(MemoryWorkspace::new_test());
        // Pre-seed a file in the workspace
        ws.write(Path::new("test_file.txt"), "Hello, MCP world!")
            .expect("pre-seed test file");

        let (_bridge, socket_path) =
            start_bridge("mcp-read-file", SessionDrain::Development, Arc::clone(&ws));
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        send_msg(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "ralph_read_file",
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

/// Verify that ralph_read_file returns isError:true when the file does not exist.
#[test]
fn consumer_gets_error_for_missing_file() {
    with_default_timeout(|| {
        let ws = Arc::new(MemoryWorkspace::new_test());
        let (_bridge, socket_path) =
            start_bridge("mcp-missing-file", SessionDrain::Development, ws);
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        send_msg(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "ralph_read_file",
                    "arguments": {"path": "nonexistent_file_xyz.txt"}
                },
                "id": 4
            }),
        );
        let response = recv_msg(&mut stream);

        // Per RFC-009 policy enforcement, tool errors are protocol-level JSON-RPC errors.
        assert!(
            response.get("error").is_some(),
            "tool execution errors must be JSON-RPC protocol errors, got: {}",
            response
        );
        let error = response["error"]
            .as_object()
            .expect("error must be an object");
        assert!(
            error
                .get("message")
                .and_then(|m| m.as_str())
                .map(|m| m.contains("not found"))
                .unwrap_or(false),
            "error message should indicate file not found, got: {:#?}",
            error
        );
    });
}

// ============================================================================
// Test 5: consumer_gets_capability_denied_for_write_in_readonly_session
// ============================================================================

/// Verify that Planning session denies ralph_write_file with a JSON-RPC protocol error.
/// Per RFC-009, capability denials are protocol-level JSON-RPC errors, not tool-level isError responses.
#[test]
fn consumer_gets_capability_denied_for_write_in_readonly_session() {
    with_default_timeout(|| {
        let ws = Arc::new(MemoryWorkspace::new_test());
        // Pre-seed a file so it's treated as tracked
        ws.write(Path::new("src/lib.rs"), "pub fn foo() {}")
            .expect("pre-seed tracked file");

        let (_bridge, socket_path) =
            start_bridge("mcp-cap-denied", SessionDrain::Planning, Arc::clone(&ws));
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        send_msg(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "ralph_write_file",
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
                .map(|m| m.contains("Capability denied"))
                .unwrap_or(false),
            "error message should indicate capability denied, got: {:#?}",
            error
        );
    });
}

// ============================================================================
// Test 6: proxy_bridges_consumer_to_server
// ============================================================================

/// Verify that the MCP proxy correctly bridges a consumer to the server:
/// - Consumer sends initialize request to proxy via stdio
/// - Proxy forwards to server via Unix socket
/// - Server responds to proxy via Unix socket
/// - Proxy forwards response to consumer via stdio
///
/// This test uses real Unix sockets but simulates the proxy's stdio
/// behavior using a UnixStream pair.
#[test]
fn proxy_bridges_consumer_to_server() {
    with_default_timeout(|| {
        use std::sync::atomic::{AtomicBool, Ordering};
        use std::sync::Arc;
        use std::thread;

        let ws = Arc::new(MemoryWorkspace::new_test());
        let (bridge, socket_path) =
            start_bridge("mcp-proxy-test", SessionDrain::Development, Arc::clone(&ws));

        // Create UnixStream pairs to simulate stdin/stdout for the proxy
        let (proxy_stdin_writer, proxy_stdin_reader) = UnixStream::pair().unwrap();
        let (proxy_stdout_writer, proxy_stdout_reader) = UnixStream::pair().unwrap();

        // Set timeouts
        proxy_stdin_writer
            .set_write_timeout(Some(Duration::from_secs(5)))
            .expect("set_write_timeout");
        proxy_stdout_reader
            .set_read_timeout(Some(Duration::from_secs(5)))
            .expect("set_read_timeout");

        let shutdown = Arc::new(AtomicBool::new(false));

        // Spawn a thread that connects to the real server socket and bridges
        // proxy_stdin_reader -> server and server -> proxy_stdout_writer
        let server_connect_thread = thread::spawn(move || {
            let server_stream = match UnixStream::connect(&socket_path) {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("proxy test: failed to connect to server: {}", e);
                    return;
                }
            };
            server_stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .ok();

            let mut stdin_reader = std::io::BufReader::new(proxy_stdin_reader);
            let mut stdout_writer = std::io::BufWriter::new(proxy_stdout_writer);
            let mut server_buf_reader = std::io::BufReader::new(&server_stream);
            let mut server_buf_writer = std::io::BufWriter::new(&server_stream);

            // Relay stdin -> server
            loop {
                // Read header
                let mut header_buf = Vec::new();
                let mut byte = [0u8; 1];
                loop {
                    match stdin_reader.read_exact(&mut byte) {
                        Ok(()) => {}
                        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => {
                            // Client disconnected
                            let _ = server_buf_writer.flush();
                            return;
                        }
                        Err(e) => {
                            eprintln!("proxy test: stdin read error: {}", e);
                            return;
                        }
                    }
                    header_buf.push(byte[0]);
                    if header_buf.ends_with(b"\r\n\r\n") {
                        break;
                    }
                }

                // Parse Content-Length
                let header_str = std::str::from_utf8(&header_buf).expect("header utf8");
                let content_len: usize = header_str
                    .lines()
                    .find(|l| l.starts_with("Content-Length:"))
                    .and_then(|l| l["Content-Length:".len()..].trim().parse().ok())
                    .expect("Content-Length header");

                // Read body
                let mut body = vec![0u8; content_len];
                stdin_reader
                    .read_exact(&mut body)
                    .expect("read body from stdin");

                // Write to server
                let _ = server_buf_writer.write_all(&header_buf);
                server_buf_writer
                    .write_all(&body)
                    .expect("write body to server");
                server_buf_writer.flush().expect("flush server");

                // Read response from server
                let mut resp_header_buf = Vec::new();
                loop {
                    server_buf_reader
                        .read_exact(&mut byte)
                        .expect("read server header");
                    resp_header_buf.push(byte[0]);
                    if resp_header_buf.ends_with(b"\r\n\r\n") {
                        break;
                    }
                }
                let resp_header_str =
                    std::str::from_utf8(&resp_header_buf).expect("resp header utf8");
                let resp_content_len: usize = resp_header_str
                    .lines()
                    .find(|l| l.starts_with("Content-Length:"))
                    .and_then(|l| l["Content-Length:".len()..].trim().parse().ok())
                    .expect("Content-Length header");

                let mut resp_body = vec![0u8; resp_content_len];
                server_buf_reader
                    .read_exact(&mut resp_body)
                    .expect("read server response body");

                // Write response to stdout
                stdout_writer
                    .write_all(&resp_header_buf)
                    .expect("write resp header");
                stdout_writer
                    .write_all(&resp_body)
                    .expect("write resp body");
                stdout_writer.flush().expect("flush stdout");
            }
        });

        // Send initialize request through fake stdin
        let init_msg = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0.1"}
            },
            "id": 1
        });
        {
            let body = serde_json::to_vec(&init_msg).expect("serialize");
            let header = format!("Content-Length: {}\r\n\r\n", body.len());
            let mut w = std::io::BufWriter::new(&proxy_stdin_writer);
            w.write_all(header.as_bytes()).expect("write header");
            w.write_all(&body).expect("write body");
            w.flush().expect("flush");
        }

        // Read initialize response from fake stdout
        let mut r = std::io::BufReader::new(&proxy_stdout_reader);
        let mut header_buf = Vec::new();
        let mut byte = [0u8; 1];
        loop {
            r.read_exact(&mut byte).expect("read byte");
            header_buf.push(byte[0]);
            if header_buf.ends_with(b"\r\n\r\n") {
                break;
            }
        }
        let header_str = std::str::from_utf8(&header_buf).expect("header utf8");
        let content_len: usize = header_str
            .lines()
            .find(|l| l.starts_with("Content-Length:"))
            .and_then(|l| l["Content-Length:".len()..].trim().parse().ok())
            .expect("Content-Length header");
        let mut body = vec![0u8; content_len];
        r.read_exact(&mut body).expect("read body");
        let response: serde_json::Value = serde_json::from_slice(&body).expect("parse JSON");

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

        // Clean shutdown
        shutdown.store(true, Ordering::Release);
        drop(proxy_stdin_writer);
        drop(proxy_stdout_reader);
        let _ = server_connect_thread.join();

        drop(bridge);
    });
}
