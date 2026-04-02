//! Access control regression tests for MCP server.
//!
//! These tests verify that access control (ReadOnly mode, allowlists, blocklists,
//! path boundaries, and capability checks) works correctly for Ralph-prefixed tool
//! names (e.g., `ralph_write_file` vs `write_file`).
//!
//! The core bug fixed: `is_mutating_tool()` was hardcoded to check generic tool names
//! like "write_file", but Ralph registers tools with "ralph_" prefix like
//! "ralph_write_file". This caused ReadOnly enforcement to silently pass for all
//! Ralph mutating tools. The fix uses metadata-driven `is_mutating()` from ToolMetadata.

use mcp_server::dispatch::access::{AccessDecision, AccessDeniedCode, McpCapability, ToolFilter};
use mcp_server::dispatch::host::DirEntry;
use mcp_server::dispatch::{ToolHandler, ToolMetadata, ToolRegistry};
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::{McpServer, ServerState};
use mcp_server::protocol::{JsonRpcRequest, ToolContent, ToolDefinition, ToolResult};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

// ---------------------------------------------------------------------------
// Mock implementations
// ---------------------------------------------------------------------------

/// Session that approves all capabilities.
struct ApprovedSession;
impl mcp_server::HostSession for ApprovedSession {
    fn session_id(&self) -> &str {
        "approved-session"
    }
    fn check_capability(&self, _cap: McpCapability) -> AccessDecision {
        AccessDecision::Allow
    }
}

/// Session that only has WorkspaceRead capability.
struct ReadOnlySession;
impl mcp_server::HostSession for ReadOnlySession {
    fn session_id(&self) -> &str {
        "readonly-session"
    }
    fn check_capability(&self, cap: McpCapability) -> AccessDecision {
        if cap == McpCapability::WorkspaceRead {
            AccessDecision::Allow
        } else {
            AccessDecision::Deny {
                reason: format!("Missing capability: {}", cap),
                code: AccessDeniedCode::CapabilityDenied,
            }
        }
    }
}

struct MockWorkspace;
impl mcp_server::WorkspaceAdapter for MockWorkspace {
    fn read(&self, _path: &Path) -> Result<String, String> {
        Ok("mock content".to_string())
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

// ---------------------------------------------------------------------------
// Tool registry helpers
// ---------------------------------------------------------------------------

/// Create a Ralph-prefixed read tool.
fn make_ralph_read_tool(counter: Option<&Arc<AtomicU32>>) -> ToolRegistry {
    let counter = counter.cloned();
    let handler: ToolHandler = Arc::new(
        move |_session: &dyn mcp_server::HostSession,
              workspace: &dyn mcp_server::WorkspaceAdapter,
              params: serde_json::Value|
              -> Result<ToolResult, mcp_server::ToolError> {
            if let Some(c) = &counter {
                c.fetch_add(1, Ordering::SeqCst);
            }
            let path = params
                .get("path")
                .and_then(|v| v.as_str())
                .ok_or_else(|| mcp_server::ToolError::InvalidParams("Missing 'path'".into()))?;
            let content = workspace
                .read(Path::new(path))
                .map_err(mcp_server::ToolError::ExecutionError)?;
            Ok(ToolResult::success(vec![ToolContent::text(content)]))
        },
    );

    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_workspace_read_file".to_string(),
            description: "Read a file from workspace".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string" }
                },
                "required": ["path"]
            }),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None,
    };

    ToolRegistry::new(vec![(metadata, handler)])
}

/// Create a Ralph-prefixed write tool.
fn make_ralph_write_tool(counter: Option<&Arc<AtomicU32>>) -> ToolRegistry {
    let counter = counter.cloned();
    let handler: ToolHandler = Arc::new(
        move |_session: &dyn mcp_server::HostSession,
              workspace: &dyn mcp_server::WorkspaceAdapter,
              params: serde_json::Value|
              -> Result<ToolResult, mcp_server::ToolError> {
            if let Some(c) = &counter {
                c.fetch_add(1, Ordering::SeqCst);
            }
            let path = params
                .get("path")
                .and_then(|v| v.as_str())
                .ok_or_else(|| mcp_server::ToolError::InvalidParams("Missing 'path'".into()))?;
            let content = params
                .get("content")
                .and_then(|v| v.as_str())
                .ok_or_else(|| mcp_server::ToolError::InvalidParams("Missing 'content'".into()))?;
            workspace
                .write(Path::new(path), content)
                .map_err(mcp_server::ToolError::ExecutionError)?;
            Ok(ToolResult::success(vec![ToolContent::text(
                "written".to_string(),
            )]))
        },
    );

    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_workspace_write_file".to_string(),
            description: "Write a file to workspace".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string" },
                    "content": { "type": "string" }
                },
                "required": ["path", "content"]
            }),
        },
        required_capability: McpCapability::WorkspaceWriteEphemeral,
        is_mutating: None,
    };

    ToolRegistry::new(vec![(metadata, handler)])
}

/// Create a Ralph-prefixed exec command tool.
fn make_ralph_exec_tool(counter: Option<&Arc<AtomicU32>>) -> ToolRegistry {
    let counter = counter.cloned();
    let handler: ToolHandler = Arc::new(
        move |_session: &dyn mcp_server::HostSession,
              _workspace: &dyn mcp_server::WorkspaceAdapter,
              params: serde_json::Value|
              -> Result<ToolResult, mcp_server::ToolError> {
            if let Some(c) = &counter {
                c.fetch_add(1, Ordering::SeqCst);
            }
            let cmd = params
                .get("command")
                .and_then(|v| v.as_str())
                .ok_or_else(|| mcp_server::ToolError::InvalidParams("Missing 'command'".into()))?;
            Ok(ToolResult::success(vec![ToolContent::text(format!(
                "ran: {}",
                cmd
            ))]))
        },
    );

    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_exec_command".to_string(),
            description: "Execute a command".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "command": { "type": "string" }
                },
                "required": ["command"]
            }),
        },
        required_capability: McpCapability::ProcessExecBounded,
        is_mutating: None,
    };

    ToolRegistry::new(vec![(metadata, handler)])
}

/// Create a Ralph-prefixed git commit tool.
fn make_ralph_git_commit_tool(counter: Option<&Arc<AtomicU32>>) -> ToolRegistry {
    let counter = counter.cloned();
    let handler: ToolHandler = Arc::new(
        move |_session: &dyn mcp_server::HostSession,
              _workspace: &dyn mcp_server::WorkspaceAdapter,
              _params: serde_json::Value|
              -> Result<ToolResult, mcp_server::ToolError> {
            if let Some(c) = &counter {
                c.fetch_add(1, Ordering::SeqCst);
            }
            Ok(ToolResult::success(vec![ToolContent::text(
                "committed".to_string(),
            )]))
        },
    );

    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_git_commit".to_string(),
            description: "Commit changes".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "message": { "type": "string" }
                },
                "required": ["message"]
            }),
        },
        required_capability: McpCapability::GitWrite,
        is_mutating: None,
    };

    ToolRegistry::new(vec![(metadata, handler)])
}

// ---------------------------------------------------------------------------
// Test server factory
// ---------------------------------------------------------------------------

fn make_test_server(
    session: Arc<dyn mcp_server::HostSession>,
    workspace: Arc<dyn mcp_server::WorkspaceAdapter>,
    registry: ToolRegistry,
    config: McpServerConfig,
) -> McpServer {
    McpServer::new(session, config, workspace, registry, None)
}

fn initialize_server(server: &McpServer) -> ServerState {
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);
    state
}

fn call_tool(
    server: &McpServer,
    state: ServerState,
    name: &str,
    arguments: serde_json::Value,
) -> mcp_server::protocol::JsonRpcResponse {
    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": name,
            "arguments": arguments
        })),
        id: Some(serde_json::json!(2)),
    };
    let (response, _) = server.handle_request(request, state);
    response.expect("handle_request should return a response for non-notification")
}

// ---------------------------------------------------------------------------
// Regression tests for Ralph-prefixed tool names
// ---------------------------------------------------------------------------

#[test]
fn ralph_write_file_blocked_in_readonly() {
    // Regression test: ralph_write_file was NOT blocked in ReadOnly mode because
    // is_mutating_tool() only checked generic "write_file" prefix, not "ralph_write_file".
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_write_tool(Some(&counter_ref));

    // ReadOnly mode should block all mutating tools
    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadOnly);

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_workspace_write_file",
        serde_json::json!({ "path": "test.txt", "content": "hello" }),
    );

    // Assert handler was NOT called (counter must be 0)
    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "Handler must NOT be called when access is denied"
    );

    assert!(
        response.error.is_some(),
        "ralph_write_file should be blocked in ReadOnly mode"
    );
    let error = response.error.unwrap();
    assert_eq!(
        error.code, -32000,
        "Should be tool error (ReadOnlyMode denial uses -32000)"
    );
    assert!(
        error.message.contains("ReadOnly") || error.message.contains("read only"),
        "Error should mention ReadOnly mode restriction"
    );
}

#[test]
fn ralph_exec_command_blocked_in_readonly() {
    // ProcessExecBounded requires ReadWrite access mode.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_exec_tool(Some(&counter_ref));

    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadOnly);

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_exec_command",
        serde_json::json!({ "command": "ls" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "Handler must NOT be called when access is denied"
    );
    assert!(
        response.error.is_some(),
        "ralph_exec_command should be blocked in ReadOnly mode"
    );
}

#[test]
fn ralph_read_file_allowed_in_readonly() {
    // WorkspaceRead is allowed in ReadOnly mode.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_read_tool(Some(&counter_ref));

    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadOnly);

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_workspace_read_file",
        serde_json::json!({ "path": "test.txt" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        1,
        "Handler MUST be called when access is allowed"
    );
    assert!(
        response.result.is_some(),
        "ralph_read_file should be allowed in ReadOnly mode"
    );
    assert!(response.error.is_none(), "Should have no error");
}

#[test]
fn ralph_write_file_blocked_by_allowlist() {
    // Allowlist that doesn't include ralph_write_file should block it.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_write_tool(Some(&counter_ref));

    // Allowlist only has read tool, not write tool
    let config =
        McpServerConfig::new(PathBuf::from("/tmp")).with_tool_filter(ToolFilter::Allowlist(vec![
            "ralph_workspace_read_file".to_string(),
        ]));

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_workspace_write_file",
        serde_json::json!({ "path": "test.txt", "content": "hello" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "Handler must NOT be called when access is denied"
    );
    assert!(
        response.error.is_some(),
        "ralph_write_file should be blocked by allowlist"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    assert!(
        error.message.contains("not allowed") || error.message.contains("ToolNotAllowed"),
        "Error should indicate tool is not allowed by filter"
    );
}

#[test]
fn ralph_write_file_blocked_by_blocklist() {
    // Blocklist that includes ralph_write_file should block it.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_write_tool(Some(&counter_ref));

    // Blocklist has the write tool
    let config =
        McpServerConfig::new(PathBuf::from("/tmp")).with_tool_filter(ToolFilter::Blocklist(vec![
            "ralph_workspace_write_file".to_string(),
        ]));

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_workspace_write_file",
        serde_json::json!({ "path": "test.txt", "content": "hello" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "Handler must NOT be called when access is denied"
    );
    assert!(
        response.error.is_some(),
        "ralph_write_file should be blocked by blocklist"
    );
}

#[test]
fn ralph_read_file_blocked_outside_root() {
    // Path outside root_dir should be blocked.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_read_tool(Some(&counter_ref));

    // Root is /tmp, but we request /etc/passwd
    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadWrite);

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_workspace_read_file",
        serde_json::json!({ "path": "/etc/passwd" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "Handler must NOT be called when access is denied"
    );
    assert!(
        response.error.is_some(),
        "Reading outside root_dir should be blocked"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    assert!(
        error.message.contains("outside") || error.message.contains("root"),
        "Error should indicate path is outside root directory"
    );
}

#[test]
fn ralph_read_file_blocked_by_capability() {
    // ReadOnlySession only has WorkspaceRead, so GitWrite should be denied.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ReadOnlySession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_git_commit_tool(Some(&counter_ref));

    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadWrite);

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_git_commit",
        serde_json::json!({ "message": "fix bug" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "Handler must NOT be called when access is denied"
    );
    assert!(
        response.error.is_some(),
        "ralph_git_commit should be blocked when session lacks GitWrite capability"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    assert!(
        error.message.contains("capability") || error.message.contains("denied"),
        "Error should indicate capability denial"
    );
}

#[test]
fn readonly_and_allowlist_both_checked_independently() {
    // ReadOnly mode and allowlist are independent checks. Allowlist is checked first
    // (priority 1), then access mode (priority 2).
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_write_tool(Some(&counter_ref));

    // Allowlist excludes the tool, so it should be blocked before access mode is even checked.
    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadOnly)
        .with_tool_filter(ToolFilter::Allowlist(vec!["some_other_tool".to_string()]));

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_workspace_write_file",
        serde_json::json!({ "path": "test.txt", "content": "hello" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "Handler must NOT be called when access is denied"
    );
    // Should be blocked by allowlist first (ToolNotAllowed), not by ReadOnly mode.
    assert!(response.error.is_some(), "Should be blocked by allowlist");
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    // The error message should indicate tool not allowed, not read-only
    assert!(
        error.message.contains("not allowed") || error.message.contains("ToolNotAllowed"),
        "Error should indicate tool is not allowed (allowlist check runs first)"
    );
}

#[test]
fn ralph_git_commit_blocked_in_readonly() {
    // GitWrite requires ReadWrite access mode.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_git_commit_tool(Some(&counter_ref));

    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadOnly);

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_git_commit",
        serde_json::json!({ "message": "fix bug" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "Handler must NOT be called when access is denied"
    );
    assert!(
        response.error.is_some(),
        "ralph_git_commit should be blocked in ReadOnly mode"
    );
}

#[test]
fn allowlist_permits_listed_tool() {
    // Allowlist containing the tool should permit it.
    // This is the inverse of ralph_write_file_blocked_by_allowlist.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_read_tool(Some(&counter_ref));

    // Allowlist contains the read tool - should be permitted
    let config =
        McpServerConfig::new(PathBuf::from("/tmp")).with_tool_filter(ToolFilter::Allowlist(vec![
            "ralph_workspace_read_file".to_string(),
        ]));

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_workspace_read_file",
        serde_json::json!({ "path": "test.txt" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        1,
        "Handler MUST be called when access is allowed"
    );
    assert!(
        response.result.is_some(),
        "Allowlist should permit listed tool (ralph_workspace_read_file)"
    );
    assert!(response.error.is_none(), "Should have no error");
}

#[test]
fn blocklist_permits_unlisted_tool() {
    // Blocklist that does NOT contain the tool should permit it.
    // This is the inverse of ralph_write_file_blocked_by_blocklist.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_read_tool(Some(&counter_ref));

    // Blocklist contains only "ralph_exec_command" - read tool should NOT be blocked
    let config =
        McpServerConfig::new(PathBuf::from("/tmp")).with_tool_filter(ToolFilter::Blocklist(vec![
            "ralph_exec_command".to_string(),
        ]));

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_workspace_read_file",
        serde_json::json!({ "path": "test.txt" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        1,
        "Handler MUST be called when access is allowed"
    );
    assert!(
        response.result.is_some(),
        "Blocklist should permit unlisted tool (ralph_workspace_read_file is not blocked)"
    );
    assert!(response.error.is_none(), "Should have no error");
}
