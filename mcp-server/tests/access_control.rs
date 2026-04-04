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
    // Assert specific AccessDeniedCode in error data
    assert_eq!(
        error
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("ReadOnlyMode"),
        "Error data should contain ReadOnlyMode code"
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
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    // Assert specific AccessDeniedCode in error data
    assert_eq!(
        error
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("ReadOnlyMode"),
        "Error data should contain ReadOnlyMode code"
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
    // Assert specific AccessDeniedCode in error data
    assert_eq!(
        error
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("OutsideRootDir"),
        "Error data should contain OutsideRootDir code"
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
    // Assert specific AccessDeniedCode in error data
    assert_eq!(
        error
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("CapabilityDenied"),
        "Error data should contain CapabilityDenied code"
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
    // Assert specific AccessDeniedCode in error data
    assert_eq!(
        error
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("ToolNotAllowed"),
        "Error data should contain ToolNotAllowed code (allowlist check runs before access mode)"
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
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    // Assert specific AccessDeniedCode in error data
    assert_eq!(
        error
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("ReadOnlyMode"),
        "Error data should contain ReadOnlyMode code"
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

#[test]
fn empty_allowlist_rejects_all_tools() {
    // An empty Allowlist means NO tools are accessible.
    // This tests the semantics: Allowlist(vec![]) rejects every tool.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_read_tool(Some(&counter_ref));

    // Empty allowlist - no tools should be accessible
    let config =
        McpServerConfig::new(PathBuf::from("/tmp")).with_tool_filter(ToolFilter::Allowlist(vec![]));

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
        0,
        "Handler must NOT be called when allowlist is empty"
    );
    assert!(
        response.error.is_some(),
        "Empty allowlist should reject all tools"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    assert!(
        error.message.contains("not allowed") || error.message.contains("ToolNotAllowed"),
        "Error should indicate tool is not allowed, got: {}",
        error.message
    );
    // Assert specific AccessDeniedCode in error data
    assert_eq!(
        error
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("ToolNotAllowed"),
        "Error data should contain ToolNotAllowed code"
    );
}

#[test]
fn empty_blocklist_allows_all_tools() {
    // An empty Blocklist means ALL tools are accessible.
    // This tests the semantics: Blocklist(vec![]) allows every tool.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_read_tool(Some(&counter_ref));

    // Empty blocklist - all tools should be accessible (this is the default)
    let config =
        McpServerConfig::new(PathBuf::from("/tmp")).with_tool_filter(ToolFilter::Blocklist(vec![]));

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
        "Handler MUST be called when blocklist is empty"
    );
    assert!(
        response.result.is_some(),
        "Empty blocklist should allow all tools (ralph_workspace_read_file should succeed)"
    );
    assert!(response.error.is_none(), "Should have no error");
}

#[test]
fn readonly_mode_blocks_write_tool_even_if_in_allowlist() {
    // ReadOnly mode and tool filter are independent checks. If a mutating tool IS in the
    // allowlist, ReadOnly mode should still block it with ReadOnlyMode denial (not ToolNotAllowed).
    // This verifies that both access_mode and tool_filter checks are truly independent.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_write_tool(Some(&counter_ref));

    // Allowlist contains the write tool, but ReadOnly mode should still block it.
    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadOnly)
        .with_tool_filter(ToolFilter::Allowlist(vec![
            "ralph_workspace_write_file".to_string()
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
    // Should be blocked by ReadOnly mode, not by allowlist (allowlist allows it).
    assert!(
        response.error.is_some(),
        "Should be blocked by ReadOnly mode"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    // The error message should indicate ReadOnly mode, not tool not allowed
    assert!(
        error.message.contains("ReadOnly") || error.message.contains("read only"),
        "Error should indicate ReadOnly mode restriction, got: {}",
        error.message
    );
    // Assert specific AccessDeniedCode in error data - ReadOnlyMode check runs after tool filter
    assert_eq!(
        error
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("ReadOnlyMode"),
        "Error data should contain ReadOnlyMode code (access mode check runs after tool filter)"
    );
}

#[test]
fn root_dir_blocks_escape_via_parent_path() {
    // Path escaping via "../" should be blocked with OutsideRootDir denial.
    // For example, if root_dir is /tmp/project, a request for
    // "/tmp/../etc/passwd" should be rejected.
    let counter = Arc::new(AtomicU32::new(0));
    let counter_ref = Arc::clone(&counter);
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_ralph_read_tool(Some(&counter_ref));

    // Root is /tmp, but path tries to escape via parent reference
    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadWrite);

    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);
    // This path resolves to /etc/passwd via ../ escape
    let response = call_tool(
        &server,
        state,
        "ralph_workspace_read_file",
        serde_json::json!({ "path": "../etc/passwd" }),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "Handler must NOT be called when access is denied"
    );
    assert!(
        response.error.is_some(),
        "Path escape via ../ should be blocked"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    assert!(
        error.message.contains("outside") || error.message.contains("root"),
        "Error should indicate path is outside root directory, got: {}",
        error.message
    );
    // Assert specific AccessDeniedCode in error data
    assert_eq!(
        error
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("OutsideRootDir"),
        "Error data should contain OutsideRootDir code"
    );
}

// ---------------------------------------------------------------------------
// Real registered tool name enforcement tests
//
// These tests use the actual tool names deployed by ralph-workflow (read_file,
// write_file, exec, ralph_submit_artifact) to verify that enforcement works for
// the specific names consumers call, not just the generic enforcement mechanism.
// ---------------------------------------------------------------------------

/// Helper: create a test registry entry for `read_file` (non-mutating, WorkspaceRead).
fn make_real_read_file_tool(counter: &Arc<AtomicU32>) -> ToolRegistry {
    let counter = Arc::clone(counter);
    let handler: ToolHandler = Arc::new(
        move |_session: &dyn mcp_server::HostSession,
              _workspace: &dyn mcp_server::WorkspaceAdapter,
              _params: serde_json::Value|
              -> Result<ToolResult, mcp_server::ToolError> {
            counter.fetch_add(1, Ordering::SeqCst);
            Ok(ToolResult::success(vec![ToolContent::text(
                "contents".to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "read_file".to_string(),
            description: "Read a file".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {"path": {"type": "string"}}}),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None,
    };
    ToolRegistry::new(vec![(metadata, handler)])
}

/// Helper: create a test registry entry for `write_file` (mutating, WorkspaceWriteTracked).
fn make_real_write_file_tool(counter: &Arc<AtomicU32>) -> ToolRegistry {
    let counter = Arc::clone(counter);
    let handler: ToolHandler = Arc::new(
        move |_session: &dyn mcp_server::HostSession,
              _workspace: &dyn mcp_server::WorkspaceAdapter,
              _params: serde_json::Value|
              -> Result<ToolResult, mcp_server::ToolError> {
            counter.fetch_add(1, Ordering::SeqCst);
            Ok(ToolResult::success(vec![ToolContent::text(
                "written".to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "write_file".to_string(),
            description: "Write a file".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}),
        },
        required_capability: McpCapability::WorkspaceWriteTracked,
        is_mutating: None,
    };
    ToolRegistry::new(vec![(metadata, handler)])
}

/// Helper: create a test registry entry for `exec` (mutating, ProcessExecBounded).
fn make_real_exec_tool(counter: &Arc<AtomicU32>) -> ToolRegistry {
    let counter = Arc::clone(counter);
    let handler: ToolHandler = Arc::new(
        move |_session: &dyn mcp_server::HostSession,
              _workspace: &dyn mcp_server::WorkspaceAdapter,
              _params: serde_json::Value|
              -> Result<ToolResult, mcp_server::ToolError> {
            counter.fetch_add(1, Ordering::SeqCst);
            Ok(ToolResult::success(vec![ToolContent::text(
                "output".to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "exec".to_string(),
            description: "Execute a command".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {"command": {"type": "string"}}}),
        },
        required_capability: McpCapability::ProcessExecBounded,
        is_mutating: None,
    };
    ToolRegistry::new(vec![(metadata, handler)])
}

/// Helper: create a test registry entry for `ralph_submit_artifact`
/// (non-mutating workflow signal, ArtifactSubmit).
fn make_real_submit_artifact_tool(counter: &Arc<AtomicU32>) -> ToolRegistry {
    let counter = Arc::clone(counter);
    let handler: ToolHandler = Arc::new(
        move |_session: &dyn mcp_server::HostSession,
              _workspace: &dyn mcp_server::WorkspaceAdapter,
              _params: serde_json::Value|
              -> Result<ToolResult, mcp_server::ToolError> {
            counter.fetch_add(1, Ordering::SeqCst);
            Ok(ToolResult::success(vec![ToolContent::text(
                "accepted".to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_submit_artifact".to_string(),
            description: "Submit a structured artifact".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {"artifact_type": {"type": "string"}, "content": {"type": "string"}}}),
        },
        required_capability: McpCapability::ArtifactSubmit,
        is_mutating: None,
    };
    ToolRegistry::new(vec![(metadata, handler)])
}

/// ReadOnly mode allows `read_file` (non-mutating, real registered name).
#[test]
fn real_read_file_allowed_in_readonly() {
    let counter = Arc::new(AtomicU32::new(0));
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_real_read_file_tool(&counter);
    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadOnly);
    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "read_file",
        serde_json::json!({"path": "README.md"}),
    );

    assert!(
        response.error.is_none(),
        "read_file must be allowed in ReadOnly mode, got error: {:?}",
        response.error
    );
    assert_eq!(
        counter.load(Ordering::SeqCst),
        1,
        "read_file handler must be called exactly once"
    );
}

/// ReadOnly mode blocks `write_file` (mutating, real registered name).
#[test]
fn real_write_file_blocked_in_readonly() {
    let counter = Arc::new(AtomicU32::new(0));
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_real_write_file_tool(&counter);
    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadOnly);
    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "write_file",
        serde_json::json!({"path": "out.txt", "content": "data"}),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "write_file handler must NOT be called when ReadOnly blocks it"
    );
    assert!(
        response.error.is_some(),
        "write_file must be denied in ReadOnly mode"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    assert!(
        error.message.contains("ReadOnly") || error.message.contains("read only"),
        "Error must mention ReadOnly mode, got: {}",
        error.message
    );
}

/// ReadOnly mode blocks `exec` (mutating, real registered name).
#[test]
fn real_exec_blocked_in_readonly() {
    let counter = Arc::new(AtomicU32::new(0));
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_real_exec_tool(&counter);
    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadOnly);
    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "exec",
        serde_json::json!({"command": "echo hello"}),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "exec handler must NOT be called when ReadOnly blocks it"
    );
    assert!(
        response.error.is_some(),
        "exec must be denied in ReadOnly mode"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    assert!(
        error.message.contains("ReadOnly") || error.message.contains("read only"),
        "Error must mention ReadOnly mode, got: {}",
        error.message
    );
}

/// ReadOnly mode allows `ralph_submit_artifact` (non-mutating workflow signal, real registered name).
#[test]
fn real_ralph_submit_artifact_allowed_in_readonly() {
    let counter = Arc::new(AtomicU32::new(0));
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_real_submit_artifact_tool(&counter);
    let config = McpServerConfig::new(PathBuf::from("/tmp"))
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadOnly);
    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_submit_artifact",
        serde_json::json!({"artifact_type": "plan", "content": "{}"}),
    );

    assert!(
        response.error.is_none(),
        "ralph_submit_artifact must be allowed in ReadOnly mode (non-mutating workflow signal), got error: {:?}",
        response.error
    );
    assert_eq!(
        counter.load(Ordering::SeqCst),
        1,
        "ralph_submit_artifact handler must be called exactly once"
    );
}

/// Blocklist blocks `ralph_submit_artifact` (real registered name).
#[test]
fn real_ralph_submit_artifact_blocked_by_blocklist() {
    let counter = Arc::new(AtomicU32::new(0));
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_real_submit_artifact_tool(&counter);
    let config =
        McpServerConfig::new(PathBuf::from("/tmp")).with_tool_filter(ToolFilter::Blocklist(vec![
            "ralph_submit_artifact".to_string(),
        ]));
    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    let response = call_tool(
        &server,
        state,
        "ralph_submit_artifact",
        serde_json::json!({"artifact_type": "plan", "content": "{}"}),
    );

    assert_eq!(
        counter.load(Ordering::SeqCst),
        0,
        "ralph_submit_artifact handler must NOT be called when blocked"
    );
    assert!(
        response.error.is_some(),
        "ralph_submit_artifact must be blocked by blocklist"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    assert_eq!(
        error
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("ToolNotAllowed"),
        "Error code must be ToolNotAllowed for blocklist denial"
    );
}

/// `tools/list` returns the actual registered tool names, not stale or prefixed variants.
///
/// Regression: after the rename commit (d1f09f19) dropped `ralph_` from most tools,
/// consumers depend on `tools/list` returning the exact registered names. This test
/// registers a mix of real tool names and verifies the list response contains them.
#[test]
fn tools_list_returns_real_registered_names() {
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;

    // Build a registry with the four tool names that represent the full naming spectrum:
    // - plain names: read_file, write_file, exec
    // - prefixed workflow name: ralph_submit_artifact
    let counter = Arc::new(AtomicU32::new(0));
    let noop_handler: ToolHandler = {
        let c = Arc::clone(&counter);
        Arc::new(
            move |_session: &dyn mcp_server::HostSession,
                  _workspace: &dyn mcp_server::WorkspaceAdapter,
                  _params: serde_json::Value|
                  -> Result<ToolResult, mcp_server::ToolError> {
                c.fetch_add(1, Ordering::SeqCst);
                Ok(ToolResult::success(vec![ToolContent::text("ok".to_string())]))
            },
        )
    };
    let make_meta = |name: &str, cap: McpCapability| ToolMetadata {
        definition: ToolDefinition {
            name: name.to_string(),
            description: format!("Test tool {}", name),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
        },
        required_capability: cap,
        is_mutating: None,
    };
    let tools = vec![
        (
            make_meta("read_file", McpCapability::WorkspaceRead),
            Arc::clone(&noop_handler),
        ),
        (
            make_meta("write_file", McpCapability::WorkspaceWriteTracked),
            Arc::clone(&noop_handler),
        ),
        (
            make_meta("exec", McpCapability::ProcessExecBounded),
            Arc::clone(&noop_handler),
        ),
        (
            make_meta("ralph_submit_artifact", McpCapability::ArtifactSubmit),
            Arc::clone(&noop_handler),
        ),
    ];
    let registry = ToolRegistry::new(tools);
    let config = McpServerConfig::new(PathBuf::from("/tmp"));
    let server = make_test_server(session, workspace, registry, config);
    let state = initialize_server(&server);

    // Call tools/list
    let list_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };
    let (response, _) = server.handle_request(list_request, state);
    let response = response.expect("tools/list must return a response");

    assert!(
        response.error.is_none(),
        "tools/list must not return an error, got: {:?}",
        response.error
    );
    let result = response.result.expect("tools/list must have a result");
    let tools_array = result
        .get("tools")
        .and_then(|t| t.as_array())
        .expect("tools/list result must contain a 'tools' array");

    let returned_names: Vec<&str> = tools_array
        .iter()
        .filter_map(|t| t.get("name").and_then(|n| n.as_str()))
        .collect();

    // All four registered names must appear in the response exactly as registered.
    for expected in &["read_file", "write_file", "exec", "ralph_submit_artifact"] {
        assert!(
            returned_names.contains(expected),
            "tools/list must return '{}' (registered name), but got: {:?}",
            expected,
            returned_names
        );
    }

    // No stale prefixed names should appear (regression guard).
    for stale in &[
        "ralph_read_file",
        "ralph_write_file",
        "ralph_exec_command",
        "ralph_workspace_read_file",
        "ralph_workspace_write_file",
    ] {
        assert!(
            !returned_names.contains(stale),
            "tools/list must NOT return stale prefixed name '{}', got: {:?}",
            stale,
            returned_names
        );
    }

    assert_eq!(
        returned_names.len(),
        4,
        "tools/list must return exactly the 4 registered tools, got: {:?}",
        returned_names
    );
}
