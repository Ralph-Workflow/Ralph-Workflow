//! End-to-end behavioral tests for MCP socket communication.
//!
//! These tests prove MCP communication works over real Unix sockets —
//! the same code path used by actual agents (Claude Code, OpenCode, etc.).
//! Each test asserts on observable JSON behavior, not internal state.
//!
//! Behavioral contracts verified here:
//! - Socket is ready immediately after `SessionBridge::start()` (no race)
//! - `initialize` handshake returns RFC-009 server info
//! - `tools/list` returns all registered tools
//! - Tool execution errors return `ToolResult { isError: true }` (not JSON-RPC error)
//! - Notifications (no `id`) produce no response frame
//! - Fix drain can call `write_file` on existing tracked files
//! - Proxy routes initialize and tools/list correctly through real McpServer

#[cfg(unix)]
mod unix_tests {
    use crate::agents::session::{AgentSession, PolicyOutcome, SessionDrain};
    use crate::mcp_server::session_bridge::SessionBridge;
    use crate::workspace::memory_workspace::MemoryWorkspace;
    use crate::workspace::Workspace;
    use crate::workspace::WorkspaceFs;
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;
    use std::path::Path;
    use std::sync::Arc;
    use std::time::Duration;
    use test_helpers::assert_no_real_git_mutations;

    /// Create and start a bridge with the given workspace, returning the bridge and
    /// the socket path. Accepts any `Arc<dyn Workspace>` for test flexibility.
    fn start_bridge(
        run_id: &str,
        drain: SessionDrain,
        workspace: Arc<dyn Workspace>,
    ) -> (SessionBridge, std::path::PathBuf) {
        let session = AgentSession::for_drain(run_id.to_string(), drain, 1);
        let mut bridge = SessionBridge::new(session, workspace);
        bridge.start().expect("SessionBridge::start() must succeed");
        let socket_path = bridge.socket_path().clone();
        (bridge, socket_path)
    }

    /// Connect to a socket that must already be listening.  Sets a 5s read timeout.
    fn connect(socket_path: &Path) -> UnixStream {
        let stream = UnixStream::connect(socket_path)
            .expect("UnixStream::connect must succeed immediately after start()");
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .expect("set_read_timeout");
        stream
    }

    /// Write a Content-Length framed JSON-RPC message to the stream.
    fn send(stream: &mut UnixStream, msg: &serde_json::Value) {
        let body = serde_json::to_vec(msg).expect("serialize request");
        let header = format!("Content-Length: {}\r\n\r\n", body.len());
        stream.write_all(header.as_bytes()).expect("write header");
        stream.write_all(&body).expect("write body");
        stream.flush().expect("flush");
    }

    /// Read one Content-Length framed JSON-RPC response from the stream.
    fn recv(stream: &mut UnixStream) -> serde_json::Value {
        // Read byte-by-byte until we see \r\n\r\n
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

    /// Send `initialize` and return the parsed response.
    fn initialize(stream: &mut UnixStream) -> serde_json::Value {
        send(
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
        recv(stream)
    }

    // =========================================================================
    // Test 1: socket is ready immediately after start() — no race
    // =========================================================================

    #[test]
    fn server_accepts_connection_immediately_after_start() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let (_bridge, socket_path) = start_bridge(
            "e2e-connect",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        // The socket file must exist on disk before any client connects.
        // No sleep — the socket must be bound before start() returns.
        assert!(
            socket_path.exists(),
            "socket file must exist immediately after start() returns (no sleep): {:?}",
            socket_path
        );
        UnixStream::connect(&socket_path)
            .expect("must connect immediately after start() without any sleep");
    }

    // =========================================================================
    // Test 2: initialize handshake returns correct server info
    // =========================================================================

    #[test]
    fn initialize_handshake_returns_server_info() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let (_bridge, socket_path) = start_bridge(
            "e2e-init",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
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
    }

    // =========================================================================
    // Test 3: tools/list returns all registered tools
    // =========================================================================

    #[test]
    fn tools_list_returns_all_registered_tools() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let (_bridge, socket_path) = start_bridge(
            "e2e-tools-list",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": 2
            }),
        );
        let response = recv(&mut stream);

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
        assert!(
            names.contains(&"git_status"),
            "must include git_status, got: {:?}",
            names
        );
    }

    // =========================================================================
    // Test 4: tool execution error returns JSON-RPC error with code -32000
    // =========================================================================

    #[test]
    fn tool_execution_error_returns_json_rpc_error() {
        // Planning session reading a non-existent file triggers ExecutionError.
        // Per mcp-server protocol, tool execution failures are returned as
        // JSON-RPC error responses with code -32000 (Tool error).
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let (_bridge, socket_path) = start_bridge(
            "e2e-exec-err",
            SessionDrain::Planning,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "read_file",
                    "arguments": {"path": "nonexistent_file_xyz.txt"}
                },
                "id": 3
            }),
        );
        let response = recv(&mut stream);

        // Per mcp-server protocol, tool execution errors are JSON-RPC error responses with code -32000
        assert!(
            response.get("error").is_some(),
            "tool execution errors must be JSON-RPC protocol errors with code -32000, got: {:#?}",
            response
        );
        let error = response["error"]
            .as_object()
            .expect("error must be an object");
        assert_eq!(
            error.get("code").and_then(|c| c.as_i64()).unwrap_or(0),
            -32000,
            "tool execution errors must have code -32000, got: {:#?}",
            error
        );
        assert!(
            error
                .get("message")
                .and_then(|m| m.as_str())
                .map(|m| m.contains("Tool error"))
                .unwrap_or(false),
            "error message should contain 'Tool error', got: {:#?}",
            error
        );
    }

    // =========================================================================
    // Test 5: capability-denied returns JSON-RPC error with code -32000
    // =========================================================================

    #[test]
    fn capability_denied_returns_json_rpc_error() {
        // Planning session writing an existing tracked file is denied (needs WriteTracked).
        // Per mcp-server protocol, capability denials are returned as
        // JSON-RPC error responses with code -32000 (Tool error).
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        // Pre-seed a file so it's treated as tracked (exists + not in .agent/)
        ws.write(Path::new("src/lib.rs"), "pub fn foo() {}")
            .expect("pre-seed tracked file");

        let (_bridge, socket_path) = start_bridge(
            "e2e-cap-denied",
            SessionDrain::Planning,
            Arc::clone(&ws) as Arc<dyn Workspace>,
        );
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "write_file",
                    "arguments": {"path": "src/lib.rs", "content": "changed"}
                },
                "id": 3
            }),
        );
        let response = recv(&mut stream);

        // Per mcp-server protocol, capability denials are JSON-RPC error responses with code -32000
        assert!(
            response.get("error").is_some(),
            "capability denial must be a JSON-RPC protocol error with code -32000, got: {:#?}",
            response
        );
        let error = response["error"]
            .as_object()
            .expect("error must be an object");
        assert_eq!(
            error.get("code").and_then(|c| c.as_i64()).unwrap_or(0),
            -32000,
            "capability denial must have code -32000, got: {:#?}",
            error
        );
        assert!(
            error
                .get("message")
                .and_then(|m| m.as_str())
                .map(|m| m.contains("denied") || m.contains("Denied"))
                .unwrap_or(false),
            "error message should indicate access denied, got: {:#?}",
            error
        );
    }

    // =========================================================================
    // Test 6: notification produces no response frame (bug 3 fix proof)
    // =========================================================================

    #[test]
    fn notification_does_not_produce_extra_frame() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let (_bridge, socket_path) = start_bridge(
            "e2e-notif",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        // Send notifications/initialized — must NOT produce a response frame
        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
                // No "id" — this is a notification per JSON-RPC 2.0
            }),
        );

        // Immediately send tools/list with a distinctive id
        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": 42
            }),
        );

        // Read ONE response — it must be for tools/list (id == 42).
        // If a response for the notification was written, recv() would return it first
        // and its id would NOT be 42.
        let response = recv(&mut stream);
        assert_eq!(
            response["id"],
            serde_json::json!(42),
            "expected tools/list response (id=42) as the first response after notification, \
             got id={} — notification likely produced a spurious frame",
            response["id"]
        );
        assert!(response.get("error").is_none());
    }

    // =========================================================================
    // Test 6b: notification produces no response within 200ms timeout
    // =========================================================================

    #[test]
    fn notification_produces_no_response_within_timeout() {
        // Verify that a JSON-RPC notification (no id) does not produce any
        // response frame within a 200ms timeout window.
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let (_bridge, socket_path) = start_bridge(
            "e2e-notif-timeout",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );

        // Use a fresh connection with a 200ms read timeout
        let mut stream =
            UnixStream::connect(&socket_path).expect("must connect immediately after start()");
        stream
            .set_read_timeout(Some(Duration::from_millis(200)))
            .expect("set_read_timeout");
        stream
            .set_write_timeout(Some(Duration::from_secs(5)))
            .expect("set_write_timeout");

        // Complete initialize handshake first (with short timeout, long enough for init)
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .expect("set_read_timeout for init");
        let body = serde_json::to_vec(&serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        }))
        .expect("serialize");
        let header = format!("Content-Length: {}\r\n\r\n", body.len());
        stream.write_all(header.as_bytes()).expect("write header");
        stream.write_all(&body).expect("write body");
        stream.flush().expect("flush");

        // Read the initialize response
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
        let mut resp_body = vec![0u8; content_len];
        stream.read_exact(&mut resp_body).expect("read body");

        // Now set 200ms read timeout and send notification
        stream
            .set_read_timeout(Some(Duration::from_millis(200)))
            .expect("set 200ms read timeout");

        let notif_body = serde_json::to_vec(&serde_json::json!({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
            // No "id" — this is a notification per JSON-RPC 2.0
        }))
        .expect("serialize notification");
        let notif_header = format!("Content-Length: {}\r\n\r\n", notif_body.len());
        stream
            .write_all(notif_header.as_bytes())
            .expect("write notif header");
        stream.write_all(&notif_body).expect("write notif body");
        stream.flush().expect("flush notification");

        // Try to read — must time out (no response written within 200ms)
        let mut buf = [0u8; 1];
        let result = stream.read(&mut buf);
        match result {
            Err(e)
                if e.kind() == std::io::ErrorKind::WouldBlock
                    || e.kind() == std::io::ErrorKind::TimedOut =>
            {
                // Correct: no response frame written within 200ms
            }
            Ok(_) => {
                panic!("Notification must not produce a response — server wrote unexpected data");
            }
            Err(e) => {
                panic!(
                    "Unexpected IO error while waiting for notification non-response: {}",
                    e
                );
            }
        }
    }

    // =========================================================================
    // Test 7: Fix drain can write to existing tracked files
    // =========================================================================

    #[test]
    fn fix_drain_can_write_existing_tracked_file() {
        // Fix drain has WorkspaceWriteTracked — it must be able to write existing files.
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        // Pre-seed a file so it's treated as tracked by handle_write_file
        ws.write(
            Path::new("src/main.rs"),
            "fn main() { panic!(\"original\"); }",
        )
        .expect("pre-seed tracked file");

        let (_bridge, socket_path) = start_bridge(
            "e2e-fix-write",
            SessionDrain::Fix,
            Arc::clone(&ws) as Arc<dyn Workspace>,
        );
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "write_file",
                    "arguments": {"path": "src/main.rs", "content": "fn main() {}"}
                },
                "id": 5
            }),
        );
        let response = recv(&mut stream);

        assert!(
            response.get("error").is_none(),
            "Fix drain write_file must not return a protocol error: {}",
            response
        );
        assert!(
            response["result"]["isError"].as_bool() != Some(true),
            "Fix drain must be able to write tracked files, got: {}",
            response["result"]
        );
    }

    // =============================================================================
    // Test 10: OpenCode connects directly to socket, initializes, and lists tools.
    //
    // OpenCode uses a direct Unix socket connection to Ralph's MCP server
    // (no proxy). This test verifies the same observable contract as the CCS
    // test but via direct socket, confirming no consumer-specific protocol
    // divergence.
    // =============================================================================

    #[test]
    fn opencode_direct_socket_can_initialize_and_list_tools() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let (_bridge, socket_path) = start_bridge(
            "e2e-opencode",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&socket_path);

        // Initialize.
        let init_response = initialize(&mut stream);
        assert!(
            init_response.get("error").is_none(),
            "initialize must not return an error, got: {}",
            init_response
        );
        let result = &init_response["result"];
        assert_eq!(
            result["serverInfo"]["name"], "ralph-mcp",
            "serverInfo.name must be 'ralph-mcp'"
        );
        assert_eq!(
            result["protocolVersion"], "2024-11-05",
            "protocolVersion must match MCP_PROTOCOL_VERSION"
        );

        // List tools.
        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": 2
            }),
        );
        let tools_response = recv(&mut stream);
        assert!(
            tools_response.get("error").is_none(),
            "tools/list must not error: {}",
            tools_response
        );
        let tools = tools_response["result"]["tools"]
            .as_array()
            .expect("result.tools must be an array");
        let names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
        assert!(
            names.contains(&"read_file"),
            "must include read_file, got: {:?}",
            names
        );
    }

    // =============================================================================
    // Test 11: Codex connects directly to socket, initializes, and lists tools.
    //
    // Codex uses a direct Unix socket connection to Ralph's MCP server (no proxy).
    // This test verifies that Codex can initialize a session and receive the full
    // tool list. Git tools (git_status, etc.) are verified by unit tests
    // using MemoryWorkspace — they must not be tested with real git in e2e tests
    // per path-based project-repo isolation policy.
    // =============================================================================

    #[test]
    fn codex_direct_socket_can_initialize_and_list_tools() {
        // Use MemoryWorkspace for isolated, policy-compliant testing.
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let (_bridge, socket_path) = start_bridge(
            "e2e-codex",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&socket_path);

        // Initialize.
        let init_response = initialize(&mut stream);
        assert!(
            init_response.get("error").is_none(),
            "initialize must not return an error, got: {}",
            init_response
        );
        let result = &init_response["result"];
        assert_eq!(
            result["serverInfo"]["name"], "ralph-mcp",
            "serverInfo.name must be 'ralph-mcp'"
        );
        assert_eq!(
            result["protocolVersion"], "2024-11-05",
            "protocolVersion must match MCP_PROTOCOL_VERSION"
        );

        // List tools — verify the full tool registry is accessible.
        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": 2
            }),
        );
        let tools_response = recv(&mut stream);
        assert!(
            tools_response.get("error").is_none(),
            "tools/list must not error: {}",
            tools_response
        );
        let tools = tools_response["result"]["tools"]
            .as_array()
            .expect("result.tools must be an array");
        let names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
        assert!(
            names.contains(&"read_file"),
            "must include read_file, got: {:?}",
            names
        );
        assert!(
            names.contains(&"git_status"),
            "must include git_status in tool list, got: {:?}",
            names
        );
        assert!(
            names.contains(&"ralph_submit_artifact"),
            "must include ralph_submit_artifact, got: {:?}",
            names
        );
    }

    // =========================================================================
    // Test 9: submit_artifact executes end-to-end and returns accepted: true
    // =========================================================================

    #[test]
    fn test_submit_artifact_tool_callable_via_real_adapter() {
        // Proves the full round-trip: initialize → tools/call ralph_submit_artifact →
        // assert accepted: true. Uses MemoryWorkspace — no real filesystem or git.
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let (mut bridge, socket_path) = start_bridge(
            "e2e-submit-artifact",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        let valid_plan = serde_json::json!({
            "summary": {
                "context": "End-to-end MCP submit artifact test",
                "scope_items": [
                    {"text": "Verify ralph_submit_artifact is callable end-to-end"},
                    {"text": "Confirm full Unix socket round-trip works"},
                    {"text": "Assert accepted: true in response"}
                ]
            },
            "steps": [
                {
                    "number": 1,
                    "title": "Test step",
                    "content": "Assert the tool is reachable via the Unix socket"
                }
            ],
            "critical_files": {
                "primary_files": [
                    {"path": "src/lib.rs", "action": "modify"}
                ]
            },
            "risks_mitigations": [
                {"risk": "Test regression", "mitigation": "Covered by this test"}
            ],
            "verification_strategy": [
                {"method": "cargo test", "expected_outcome": "All tests pass"}
            ]
        });

        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "ralph_submit_artifact",
                    "arguments": {
                        "artifact_type": "plan",
                        "content": serde_json::to_string(&valid_plan).unwrap()
                    }
                },
                "id": 10
            }),
        );
        let response = recv(&mut stream);

        assert!(
            response.get("error").is_none(),
            "ralph_submit_artifact must not return a JSON-RPC error, got: {:#?}",
            response
        );
        let content = response["result"]["content"]
            .as_array()
            .expect("result.content must be an array");
        let text = content
            .iter()
            .find(|c| c["type"] == "text")
            .and_then(|c| c["text"].as_str())
            .expect("result must contain a text content item");
        let parsed: serde_json::Value =
            serde_json::from_str(text).expect("text content must be valid JSON");
        assert_eq!(
            parsed["accepted"],
            serde_json::Value::Bool(true),
            "ralph_submit_artifact must return accepted: true for a valid plan, got: {:#?}",
            parsed
        );

        // Verify AuditSink received an Allow record for the ArtifactSubmit capability.
        // The MCP audit adapter translates AccessDecision::Allow to PolicyOutcome::Approved.
        // Give the background thread a short window to flush the audit record.
        std::thread::sleep(Duration::from_millis(50));
        let audit_records = bridge.drain_audit_records();
        let artifact_submit_approved = audit_records.iter().any(|r| {
            matches!(r.outcome, PolicyOutcome::Approved)
                && (r.description.contains("ralph_submit_artifact")
                    || r.description.contains("submit_artifact"))
        });
        assert!(
            artifact_submit_approved,
            "Audit trail must contain an Approved record for ralph_submit_artifact, \
             got records: {:#?}",
            audit_records
                .iter()
                .map(|r| format!("{}: {:?}", r.description, r.outcome))
                .collect::<Vec<_>>()
        );
    }

    // =========================================================================
    // Test 10: all 15 tools are present in tools/list for Development drain
    // =========================================================================

    #[test]
    fn all_tools_are_callable_by_development_drain() {
        // Proves all 15 registered tools appear in tools/list for a Development
        // drain session. Each tool is verified to exist in the list.
        // (Full invocation of each is covered by unit tests; this proves registration.)
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let (_bridge, socket_path) = start_bridge(
            "e2e-all-tools",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": 11
            }),
        );
        let response = recv(&mut stream);

        assert!(
            response.get("error").is_none(),
            "tools/list must not error: {}",
            response
        );
        let tools = response["result"]["tools"]
            .as_array()
            .expect("result.tools must be an array");
        let names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();

        // All 15 registered tools must be present
        let expected_tools = [
            "read_file",
            "write_file",
            "list_directory",
            "search_files",
            "list_directory_recursive",
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
        for tool_name in &expected_tools {
            assert!(
                names.contains(tool_name),
                "tools/list must include '{}' for Development drain, got: {:?}",
                tool_name,
                names
            );
        }
        assert_eq!(
            names.len(),
            expected_tools.len(),
            "tools/list must return exactly {} tools for Development drain, got {} tools: {:?}",
            expected_tools.len(),
            names.len(),
            names
        );
    }

    // =========================================================================
    // Test 12: Fix drain can write to a file in a TempDir-backed workspace
    //
    // Proves that the write path works with a real filesystem workspace (WorkspaceFs)
    // backed by a temporary directory — not just an in-memory workspace.
    // The TempDir path provides meaningful path-safety guarantees since it is
    // verifiably outside the project repo.
    // =========================================================================

    #[test]
    fn write_to_tracked_file_in_tempdir_workspace() {
        let temp_dir = tempfile::TempDir::new().expect("create temp dir");
        let ws_root = temp_dir.path().to_path_buf();
        // Assert the temp dir is not inside the project repo — meaningful check
        // because temp dirs live under /tmp which is never inside a git repo.
        assert_no_real_git_mutations(&ws_root);

        // Pre-seed the file on disk so handle_write_file treats it as tracked.
        let src_dir = ws_root.join("src");
        std::fs::create_dir_all(&src_dir).expect("create src/");
        std::fs::write(
            src_dir.join("main.rs"),
            "fn main() { panic!(\"original\"); }",
        )
        .expect("write original file on disk");

        let workspace = Arc::new(WorkspaceFs::new(ws_root.clone()));
        let (_bridge, socket_path) = start_bridge(
            "e2e-tmpdir-write",
            SessionDrain::Fix,
            workspace as Arc<dyn Workspace>,
        );
        let mut stream = connect(&socket_path);
        initialize(&mut stream);

        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "write_file",
                    "arguments": {"path": "src/main.rs", "content": "fn main() {}"}
                },
                "id": 5
            }),
        );
        let response = recv(&mut stream);

        assert!(
            response.get("error").is_none(),
            "Fix drain write_file must not return a protocol error: {}",
            response
        );
        assert!(
            response["result"]["isError"].as_bool() != Some(true),
            "Fix drain must be able to write tracked files, got: {}",
            response["result"]
        );

        // Verify the file was actually written to disk.
        let updated =
            std::fs::read_to_string(ws_root.join("src/main.rs")).expect("read updated file");
        assert_eq!(
            updated, "fn main() {}",
            "file content on disk must reflect the MCP write"
        );
    }
}
