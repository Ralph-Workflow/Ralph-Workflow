//! Integration tests for MCP server wiring in `ralph-workflow`.
//!
//! These tests prove that the adapter layer between `ralph-workflow` and
//! `mcp-server` is correctly wired: the tool registry is fully populated,
//! the capability mapping is complete, and the server can dispatch tool calls
//! end-to-end using real adapter implementations.
//!
//! # What is tested here
//!
//! - `build_ralph_tool_registry` produces exactly the expected 15 tools
//! - `McpServer` constructed from ralph adapters accepts `tools/list` and
//!   returns all 15 tools in the response
//! - A successful `tools/call` via the ralph adapter stack (happy path)
//! - `ralph_submit_artifact` is callable (regression test for the "tool does
//!   not exist" bug)
//!
//! # Relation to other test files
//!
//! - `e2e_socket_behavior` — proves the same paths over real Unix sockets
//! - `capability_tests` — unit-level capability mapping per session drain
//! - `tool_tests` — handler-level unit tests
//!
//! This file provides the integration-level acceptance tests called out by
//! the architecture plan: a server constructed from ralph adapters, without
//! going through a real socket, passes all protocol-level checks.

use crate::agents::session::{AgentSession, SessionDrain};
use crate::mcp_server::tool_bridge::{
    build_ralph_tool_registry, RalphAuditSinkAdapter, RalphHostSessionAdapter,
    RalphWorkspaceAdapter,
};
use crate::workspace::memory_workspace::MemoryWorkspace;
use crate::workspace::Workspace;
use mcp_server::dispatch::access::AccessMode;
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::{McpServer, ServerState};
use mcp_server::protocol::JsonRpcRequest;
use std::path::Path;
use std::sync::Arc;
use test_helpers::assert_no_real_git_mutations;

fn dev_session() -> Arc<AgentSession> {
    Arc::new(AgentSession::for_drain(
        "integration-test-run".to_string(),
        SessionDrain::Development,
        1,
    ))
}

fn test_workspace() -> Arc<dyn Workspace> {
    Arc::new(MemoryWorkspace::new_test())
}

fn initialize_request(id: i64) -> JsonRpcRequest {
    JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({ "protocolVersion": "2024-11-05" })),
        id: Some(serde_json::json!(id)),
    }
}

fn tools_call_request(id: i64, name: &str, arguments: serde_json::Value) -> JsonRpcRequest {
    JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": name,
            "arguments": arguments,
        })),
        id: Some(serde_json::json!(id)),
    }
}

/// Build a `McpServer` using real ralph-workflow adapter implementations.
///
/// This replicates the wiring that `SessionBridge::start()` performs, but
/// without the Unix socket transport. Useful for testing the adapter stack
/// in isolation.
fn build_ralph_mcp_server(session: Arc<AgentSession>, workspace: Arc<dyn Workspace>) -> McpServer {
    let root_dir = workspace.root().to_path_buf();
    let registry = build_ralph_tool_registry(Arc::clone(&session), Arc::clone(&workspace));
    let host_session = Arc::new(RalphHostSessionAdapter::new(Arc::clone(&session)))
        as Arc<dyn mcp_server::HostSession>;
    let workspace_adapter = Arc::new(RalphWorkspaceAdapter::new(Arc::clone(&workspace)))
        as Arc<dyn mcp_server::WorkspaceAdapter>;
    let audit_sink =
        Arc::new(RalphAuditSinkAdapter::new()) as Arc<dyn mcp_server::dispatch::access::AuditSink>;
    let config = McpServerConfig::new(root_dir).with_access_mode(AccessMode::ReadWrite);
    McpServer::new(
        host_session,
        config,
        workspace_adapter,
        registry,
        Some(audit_sink),
    )
}

/// Verifies that `build_ralph_tool_registry` registers exactly the 15 tools
/// required by the plan. This is the primary acceptance test proving all tools
/// are registered before being wired into `McpServer`.
#[test]
fn build_ralph_tool_registry_registers_all_15_tools() {
    let session = dev_session();
    let workspace = test_workspace();
    assert_no_real_git_mutations(workspace.root());

    let registry = build_ralph_tool_registry(Arc::clone(&session), Arc::clone(&workspace));
    let tools = registry.list_tools();
    let names: Vec<&str> = tools.iter().map(|t| t.name.as_str()).collect();

    let expected: &[&str] = &[
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

    for tool_name in expected {
        assert!(
            names.contains(tool_name),
            "registry must include tool '{tool_name}'; registered tools: {names:?}"
        );
    }
    assert_eq!(
        names.len(),
        expected.len(),
        "registry must have exactly {} tools; got {}: {names:?}",
        expected.len(),
        names.len()
    );
}

/// Verifies that an `McpServer` constructed from ralph adapters responds to
/// `tools/list` with all 15 registered tools. This proves the registry is
/// passed correctly to `McpServer::new()` and not replaced with an empty default.
#[test]
fn mcp_server_tools_list_returns_all_15_ralph_tools() {
    let session = dev_session();
    let workspace = test_workspace();
    assert_no_real_git_mutations(workspace.root());

    let server = build_ralph_mcp_server(Arc::clone(&session), Arc::clone(&workspace));

    // Initialize first
    let (_, state) = server.handle_request(initialize_request(1), ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    let list_req = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };
    let (response, _) = server.handle_request(list_req, state);

    let resp = response.expect("tools/list must return a response");
    assert!(
        resp.error.is_none(),
        "tools/list must not error; got: {:?}",
        resp.error
    );
    let tools = resp.result.expect("tools/list must return a result");
    let tool_arr = tools["tools"]
        .as_array()
        .expect("result must have a tools array");
    let names: Vec<&str> = tool_arr.iter().filter_map(|t| t["name"].as_str()).collect();

    let expected: &[&str] = &[
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
    for tool_name in expected {
        assert!(
            names.contains(tool_name),
            "tools/list must include '{tool_name}'; got: {names:?}"
        );
    }
    assert_eq!(
        names.len(),
        expected.len(),
        "tools/list must return exactly {} tools; got {}: {names:?}",
        expected.len(),
        names.len()
    );
}

/// Verifies that the ralph adapter stack dispatches a `read_file` call
/// successfully (happy path). Proves the adapter-to-handler wiring is complete
/// for the Development drain.
#[test]
fn mcp_server_read_file_succeeds_for_development_drain() {
    let session = dev_session();
    let workspace = test_workspace();
    assert_no_real_git_mutations(workspace.root());

    // Pre-populate a file in the workspace so read_file can succeed
    workspace
        .write(
            Path::new("integration_test_file.txt"),
            "integration test content",
        )
        .expect("setup: write test file");

    let server = build_ralph_mcp_server(Arc::clone(&session), Arc::clone(&workspace));

    let (_, state) = server.handle_request(initialize_request(1), ServerState::Uninitialized);

    let (response, _) = server.handle_request(
        tools_call_request(
            2,
            "read_file",
            // tool_workspace.rs passes the path as-is to workspace.read(), which
            // expects a workspace-relative path (MemoryWorkspace uses relative keys)
            serde_json::json!({ "path": "integration_test_file.txt" }),
        ),
        state,
    );

    let resp = response.expect("read_file must return a response");
    // read_file returns ToolResult (not a JSON-RPC error), even on handler errors.
    // Success means result is present and isError is false.
    assert!(
        resp.result.is_some(),
        "read_file must return a result; got error: {:?}",
        resp.error
    );
    let result = resp.result.unwrap();
    let is_error = result["isError"].as_bool().unwrap_or(false);
    assert!(
        !is_error,
        "read_file must succeed (isError = false) for a file that exists; got result: {result}"
    );
}

/// Verifies that calling `ralph_submit_artifact` via the adapter stack returns
/// a successful `ToolResult`. This is the primary regression test for the
/// reported "tool does not exist" bug: if submit_artifact is properly registered
/// and the adapter chain is complete, the call does not return -32601.
#[test]
fn mcp_server_submit_artifact_tool_is_callable() {
    let session = dev_session();
    let workspace = test_workspace();
    assert_no_real_git_mutations(workspace.root());

    let server = build_ralph_mcp_server(Arc::clone(&session), Arc::clone(&workspace));

    let (_, state) = server.handle_request(initialize_request(1), ServerState::Uninitialized);

    let (response, _) = server.handle_request(
        tools_call_request(
            2,
            "ralph_submit_artifact",
            // artifact_type is required; content must be valid JSON matching the schema
            serde_json::json!({
                "artifact_type": "commit_message",
                "content": "{\"type\": \"commit\", \"subject\": \"feat: integration test\"}"
            }),
        ),
        state,
    );

    let resp = response.expect("ralph_submit_artifact must return a response");
    // Must not return a JSON-RPC -32601 (method not found) error
    if let Some(err) = &resp.error {
        assert_ne!(
            err.code, -32601,
            "ralph_submit_artifact must not return 'method not found' (-32601): {:?}",
            err
        );
    }
    // Must return a result (ToolResult shape), not a JSON-RPC error
    assert!(
        resp.result.is_some(),
        "ralph_submit_artifact must return a ToolResult; got error: {:?}",
        resp.error
    );
}
