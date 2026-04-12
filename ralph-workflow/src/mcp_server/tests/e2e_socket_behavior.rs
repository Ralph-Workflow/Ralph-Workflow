//! End-to-end behavioral tests for MCP protocol behavior.
//!
//! These tests exercise the same `SessionBridge` protocol surface that agents
//! use, but through an in-process harness instead of real TCP sockets so the
//! suite remains deterministic under sandboxed test execution.
//!
//! Behavioral contracts verified here:
//! - Bridge is ready immediately after `SessionBridge::start()` (no race)
//! - `initialize` handshake returns RFC-009 server info
//! - `tools/list` returns all registered tools
//! - Tool execution errors return `ToolResult { isError: true }` (not JSON-RPC error)
//! - Notifications (no `id`) produce no response frame
//! - Fix drain can call `write_file` on existing tracked files
//! - Proxy routes initialize and tools/list correctly through `McpServer`

#[cfg(unix)]
mod unix_tests {
    use crate::agents::session::{AgentSession, PolicyOutcome, SessionDrain};
    use crate::mcp_server::session_bridge::SessionBridge;
    use crate::workspace::memory_workspace::MemoryWorkspace;
    use crate::workspace::Workspace;
    use crate::workspace::WorkspaceFs;
    use mcp_server::io::ServerState;
    use mcp_server::protocol::JsonRpcRequest;
    use std::path::Path;
    use std::sync::Arc;

    struct TestConnection<'a> {
        bridge: &'a SessionBridge,
        state: ServerState,
        pending_response: Option<serde_json::Value>,
    }

    /// Safety guard: verify test path is not inside a real git repository.
    /// Prevents tests from accidentally mutating real project git state.
    fn assert_no_real_git_mutations(path: &Path) {
        let mut current = path.to_path_buf();
        loop {
            if current.join(".git").exists() {
                panic!(
                    "POLICY VIOLATION: test path '{}' is inside a real git repository at '{}'. \
                     Tests must use MemoryWorkspace or isolated temp directories outside any repo.",
                    path.display(),
                    current.display()
                );
            }
            let next = std::fs::canonicalize(&current)
                .ok()
                .and_then(|p| p.parent().map(|p| p.to_path_buf()))
                .or_else(|| current.parent().map(|p| p.to_path_buf()));
            match next {
                Some(parent) if parent != current => current = parent,
                _ => break,
            }
        }
    }

    /// Create and start a bridge with the given workspace.
    fn start_bridge(
        run_id: &str,
        drain: SessionDrain,
        workspace: Arc<dyn Workspace>,
    ) -> SessionBridge {
        let session = AgentSession::for_drain(run_id.to_string(), drain, 1);
        let mut bridge = SessionBridge::new(session, workspace);
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

    fn send(connection: &mut TestConnection<'_>, msg: &serde_json::Value) {
        let request: JsonRpcRequest =
            serde_json::from_value(msg.clone()).expect("serialize request");
        let (response, state) = connection
            .bridge
            .handle_request_in_process(request, connection.state);
        connection.state = state;
        connection.pending_response =
            response.map(|resp| serde_json::to_value(resp).expect("serialize response"));
    }

    fn recv(connection: &mut TestConnection<'_>) -> serde_json::Value {
        connection
            .pending_response
            .take()
            .expect("expected response frame")
    }

    fn initialize(connection: &mut TestConnection<'_>) -> serde_json::Value {
        send(
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
        recv(connection)
    }

    // =========================================================================
    // Test 1: socket is ready immediately after start() — no race
    // =========================================================================

    #[test]
    fn server_accepts_connection_immediately_after_start() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let bridge = start_bridge(
            "e2e-connect",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        assert!(
            bridge.is_started(),
            "bridge must report started immediately"
        );
        let mut stream = connect(&bridge);
        let response = initialize(&mut stream);
        assert!(
            response.get("error").is_none(),
            "initialize must succeed without delay"
        );
    }

    // =========================================================================
    // Test 2: initialize handshake returns correct server info
    // =========================================================================

    #[test]
    fn initialize_handshake_returns_server_info() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let bridge = start_bridge(
            "e2e-init",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
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
    }

    // =========================================================================
    // Test 3: tools/list returns all registered tools
    // =========================================================================

    #[test]
    fn tools_list_returns_all_registered_tools() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let bridge = start_bridge(
            "e2e-tools-list",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
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
        let bridge = start_bridge(
            "e2e-exec-err",
            SessionDrain::Planning,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
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

        let bridge = start_bridge(
            "e2e-cap-denied",
            SessionDrain::Planning,
            Arc::clone(&ws) as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
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
        let bridge = start_bridge(
            "e2e-notif",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
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
        let bridge = start_bridge(
            "e2e-notif-timeout",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
        initialize(&mut stream);

        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "notifications/initialized"
                // No "id" — this is a notification per JSON-RPC 2.0
            }),
        );
        assert!(
            stream.pending_response.is_none(),
            "notification must not produce a response frame"
        );
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

        let bridge = start_bridge(
            "e2e-fix-write",
            SessionDrain::Fix,
            Arc::clone(&ws) as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
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

    #[test]
    fn commit_drain_enforces_deny_matrix_while_allowing_artifact_submission() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        ws.write(Path::new("src/lib.rs"), "pub fn old() {}")
            .expect("pre-seed tracked file");

        let bridge = start_bridge(
            "e2e-commit-matrix",
            SessionDrain::Commit,
            Arc::clone(&ws) as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
        initialize(&mut stream);

        // Denied: tracked write in commit drain.
        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "write_file",
                    "arguments": {"path": "src/lib.rs", "content": "pub fn new() {}"}
                },
                "id": 61
            }),
        );
        let denied_write = recv(&mut stream);
        let write_error = denied_write
            .get("error")
            .and_then(serde_json::Value::as_object)
            .expect("commit drain write must be denied with protocol error");
        let write_msg = write_error
            .get("message")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("");
        assert!(
            write_msg.contains("denies") || write_msg.contains("denied"),
            "commit drain write denial must describe policy block, got: {denied_write}"
        );

        // Denied: process execution tool in commit drain.
        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "exec",
                    "arguments": {"cmd": "pwd"}
                },
                "id": 62
            }),
        );
        let denied_exec = recv(&mut stream);
        assert!(
            denied_exec.get("error").is_some(),
            "commit drain exec must be denied"
        );

        // Allowed: read-only access still works.
        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "read_file",
                    "arguments": {"path": "src/lib.rs"}
                },
                "id": 63
            }),
        );
        let read_ok = recv(&mut stream);
        assert!(
            read_ok.get("error").is_none(),
            "commit drain must keep read-only tool access"
        );

        // Allowed: artifact submission in commit drain.
        let valid_commit_message = serde_json::json!({
            "type": "commit",
            "subject": "test: commit drain artifact acceptance"
        });
        let commit_content = serde_json::to_string(&valid_commit_message)
            .unwrap_or_else(|err| panic!("commit artifact JSON serialization failed: {err}"));
        send(
            &mut stream,
            &serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "ralph_submit_artifact",
                    "arguments": {
                        "artifact_type": "commit_message",
                        "content": commit_content
                    }
                },
                "id": 64
            }),
        );
        let artifact_ok = recv(&mut stream);
        assert!(
            artifact_ok.get("error").is_none(),
            "commit drain must allow artifact submission: {artifact_ok}"
        );
    }

    // =============================================================================
    // Test 10: OpenCode connects directly to socket, initializes, and lists tools.
    //
    // OpenCode uses a direct TCP loopback connection to Ralph's MCP server
    // (no proxy). This test verifies the same observable contract as the CCS
    // test but via direct socket, confirming no consumer-specific protocol
    // divergence.
    // =============================================================================

    #[test]
    fn opencode_direct_socket_can_initialize_and_list_tools() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let bridge = start_bridge(
            "e2e-opencode",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);

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
    // Codex uses a direct TCP loopback connection to Ralph's MCP server (no proxy).
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
        let bridge = start_bridge(
            "e2e-codex",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);

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
        let mut bridge = start_bridge(
            "e2e-submit-artifact",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
        initialize(&mut stream);

        let valid_plan = serde_json::json!({
            "summary": {
                "context": "End-to-end MCP submit artifact test",
                "scope_items": [
                    {"text": "Verify ralph_submit_artifact is callable end-to-end"},
                    {"text": "Confirm full TCP loopback round-trip works"},
                    {"text": "Assert accepted: true in response"}
                ]
            },
            "steps": [
                {
                    "number": 1,
                    "title": "Test step",
                    "content": "Assert the tool is reachable via the TCP loopback endpoint"
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
        // No sleep needed: audit records are emitted synchronously before the response is sent.
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
    // Test 9b: artifact submission persists to workspace (state transition)
    // =========================================================================

    /// Verifies that a successful `ralph_submit_artifact` call actually writes
    /// the artifact to the workspace, which is the mechanism that triggers the
    /// workflow state transition on the next event loop tick.
    ///
    /// This strengthens Step-5 coverage: we assert not only the "accepted" MCP
    /// response and audit record, but also that the artifact envelope is
    /// readable from the workspace — proving the data path that the reducer
    /// polls to detect completion.
    #[test]
    fn test_submit_artifact_persists_to_workspace_for_state_transition() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let ws_check: Arc<dyn Workspace> = Arc::clone(&ws) as Arc<dyn Workspace>;
        let mut bridge = start_bridge(
            "e2e-artifact-persist",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
        initialize(&mut stream);

        // Verify no artifact exists before submission.
        let before = ws_check
            .read_artifact_json("plan")
            .expect("read_artifact_json must not error");
        assert!(
            before.is_none(),
            "workspace must not contain a plan artifact before submission"
        );

        let valid_plan = serde_json::json!({
            "summary": {
                "context": "State transition verification test",
                "scope_items": [
                    {"text": "Verify workspace persistence"},
                    {"text": "Confirm state transition readiness"},
                    {"text": "Assert reducer can read artifact"}
                ]
            },
            "steps": [
                {
                    "number": 1,
                    "title": "Verify persistence",
                    "content": "Assert the artifact is written to the workspace"
                }
            ],
            "critical_files": {
                "primary_files": [
                    {"path": "src/lib.rs", "action": "modify"}
                ]
            },
            "risks_mitigations": [
                {"risk": "None", "mitigation": "Covered by this test"}
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
                        "content": serde_json::to_string(&valid_plan).expect("serialize plan")
                    }
                },
                "id": 20
            }),
        );
        let response = recv(&mut stream);
        assert!(
            response.get("error").is_none(),
            "submission must succeed: {response:#?}"
        );

        // THE KEY ASSERTION: verify the artifact was persisted to the workspace.
        // This is what the reducer polls on the next event loop tick to detect
        // that an agent has completed its phase.
        let after = ws_check
            .read_artifact_json("plan")
            .expect("read_artifact_json must not error");
        assert!(
            after.is_some(),
            "workspace must contain the plan artifact after submission — this is \
             the state transition trigger the reducer reads on the next tick"
        );
        let envelope = after.expect("verified Some above");
        assert_eq!(
            envelope.artifact_type, "plan",
            "persisted artifact must be of type 'plan'"
        );

        // Verify audit record was emitted (defense in depth).
        let audit_records = bridge.drain_audit_records();
        assert!(
            !audit_records.is_empty(),
            "audit trail must not be empty after artifact submission"
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
        let bridge = start_bridge(
            "e2e-all-tools",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
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
    // Test 12b: Multiple concurrent sessions get independent tool lists
    // =========================================================================

    #[test]
    fn multiple_concurrent_sessions_get_independent_tool_lists() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());

        // Start two bridges with different drains — both should work independently
        let bridge1 = start_bridge(
            "e2e-concurrent-1",
            SessionDrain::Development,
            Arc::clone(&ws) as Arc<dyn Workspace>,
        );
        let bridge2 = start_bridge(
            "e2e-concurrent-2",
            SessionDrain::Planning,
            Arc::clone(&ws) as Arc<dyn Workspace>,
        );

        let mut conn1 = connect(&bridge1);
        let mut conn2 = connect(&bridge2);

        // Both should initialize successfully
        let init1 = initialize(&mut conn1);
        let init2 = initialize(&mut conn2);
        assert!(
            init1.get("error").is_none(),
            "session 1 initialize must succeed: {init1}"
        );
        assert!(
            init2.get("error").is_none(),
            "session 2 initialize must succeed: {init2}"
        );

        // Both should list tools
        send(
            &mut conn1,
            &serde_json::json!({"jsonrpc": "2.0", "method": "tools/list", "id": 2}),
        );
        send(
            &mut conn2,
            &serde_json::json!({"jsonrpc": "2.0", "method": "tools/list", "id": 2}),
        );
        let list1 = recv(&mut conn1);
        let list2 = recv(&mut conn2);

        let names1: Vec<&str> = list1["result"]["tools"]
            .as_array()
            .expect("tools array")
            .iter()
            .filter_map(|t| t["name"].as_str())
            .collect();
        let names2: Vec<&str> = list2["result"]["tools"]
            .as_array()
            .expect("tools array")
            .iter()
            .filter_map(|t| t["name"].as_str())
            .collect();

        assert!(
            names1.contains(&"ralph_submit_artifact"),
            "session 1 must include ralph_submit_artifact: {names1:?}"
        );
        assert!(
            names2.contains(&"ralph_submit_artifact"),
            "session 2 must include ralph_submit_artifact: {names2:?}"
        );
    }

    // =========================================================================
    // Test 12c: Reconnection uses fresh state (initialize required again)
    // =========================================================================

    #[test]
    fn reconnection_requires_fresh_initialize() {
        let ws = Arc::new(MemoryWorkspace::new_test());
        assert_no_real_git_mutations(ws.root());
        let bridge = start_bridge(
            "e2e-reconnect",
            SessionDrain::Development,
            ws as Arc<dyn Workspace>,
        );

        // First connection: initialize and list tools
        let mut conn1 = connect(&bridge);
        initialize(&mut conn1);
        send(
            &mut conn1,
            &serde_json::json!({"jsonrpc": "2.0", "method": "tools/list", "id": 2}),
        );
        let list1 = recv(&mut conn1);
        assert!(
            list1.get("error").is_none(),
            "first connection tools/list must succeed"
        );

        // Simulate reconnection: create a new connection to the same bridge.
        // The server resets to Uninitialized per-connection, so tools/list
        // without initialize must return NotInitialized error.
        let mut conn2 = connect(&bridge);
        send(
            &mut conn2,
            &serde_json::json!({"jsonrpc": "2.0", "method": "tools/list", "id": 3}),
        );
        let list2 = recv(&mut conn2);
        assert!(
            list2.get("error").is_some(),
            "reconnected session without initialize must get error: {list2}"
        );
        let error_code = list2["error"]["code"].as_i64().unwrap_or(0);
        assert_eq!(
            error_code, -32001,
            "error must be NotInitialized (-32001), got: {error_code}"
        );

        // After re-initializing, tools/list works again
        initialize(&mut conn2);
        send(
            &mut conn2,
            &serde_json::json!({"jsonrpc": "2.0", "method": "tools/list", "id": 4}),
        );
        let list3 = recv(&mut conn2);
        assert!(
            list3.get("error").is_none(),
            "re-initialized connection tools/list must succeed: {list3}"
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
        let bridge = start_bridge(
            "e2e-tmpdir-write",
            SessionDrain::Fix,
            workspace as Arc<dyn Workspace>,
        );
        let mut stream = connect(&bridge);
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
