//! Capability dispatch tests for MCP server.
//!
//! These tests verify tool dispatch and capability gating without requiring
//! real filesystem, git, or network operations.

use mcp_server::dispatch::access::{AccessDecision, AccessDeniedCode, McpCapability};
use mcp_server::dispatch::host::DirEntry;
use mcp_server::dispatch::{ToolHandler, ToolMetadata, ToolRegistry};
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::{McpServer, ServerState};
use mcp_server::protocol::{JsonRpcRequest, ToolContent, ToolDefinition, ToolResult};
use std::path::Path;
use std::sync::Arc;

// ---------------------------------------------------------------------------
// Mock implementations
// ---------------------------------------------------------------------------

struct ApprovedSession;
impl mcp_server::HostSession for ApprovedSession {
    fn session_id(&self) -> &str {
        "approved-session"
    }
    fn check_capability(&self, _cap: McpCapability) -> AccessDecision {
        AccessDecision::Allow
    }
}

struct DeniedSession;
impl mcp_server::HostSession for DeniedSession {
    fn session_id(&self) -> &str {
        "denied-session"
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
    fn read(&self, path: &Path) -> Result<String, String> {
        if path.as_os_str() == "test.txt" {
            Ok("Hello, World!".to_string())
        } else {
            Err(format!("File not found: {}", path.display()))
        }
    }
    fn write(&self, path: &Path, _content: &str) -> Result<(), String> {
        if path.to_string_lossy().contains("readonly") {
            Err("Read-only filesystem".to_string())
        } else {
            Ok(())
        }
    }
    fn exists(&self, path: &Path) -> bool {
        path.as_os_str() == "test.txt"
    }
    fn read_dir(&self, _path: &Path) -> Result<Vec<DirEntry>, String> {
        Ok(vec![DirEntry {
            path: "test.txt".to_string(),
            is_dir: false,
        }])
    }
}

fn make_registry_with_read_tool() -> ToolRegistry {
    let read_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         workspace: &dyn mcp_server::WorkspaceAdapter,
         params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
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
            name: "test_read".to_string(),
            description: "Read a file".to_string(),
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

    ToolRegistry::new(vec![(metadata, read_handler)])
}

fn make_test_server(
    session: Arc<dyn mcp_server::HostSession>,
    workspace: Arc<dyn mcp_server::WorkspaceAdapter>,
    registry: ToolRegistry,
) -> McpServer {
    let config = McpServerConfig::new(Path::new("/tmp").to_path_buf());
    McpServer::new(session, config, workspace, registry, None)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[test]
fn test_tool_dispatch_success() {
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_registry_with_read_tool();
    let server = make_test_server(session, workspace, registry);

    // Initialize first
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    // Call the tool
    let tool_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "test_read",
            "arguments": { "path": "test.txt" }
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(tool_request, state);
    let response = response.expect("handle_request should return a response for non-notification");

    assert!(response.result.is_some(), "Expected success result");
    assert!(response.error.is_none(), "Expected no error");
}

#[test]
fn test_tool_not_found() {
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]); // Empty registry
    let server = make_test_server(session, workspace, registry);

    // Initialize first
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    // Call non-existent tool
    let tool_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "nonexistent_tool",
            "arguments": {}
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(tool_request, state);
    let response = response.expect("handle_request should return a response for non-notification");

    assert!(response.error.is_some(), "Expected error for unknown tool");
    let error = response.error.unwrap();
    assert_eq!(error.code, -32601, "Should be method not found error");
}

#[test]
fn test_capability_denied_returns_error() {
    // Create a session that denies most capabilities
    let session = Arc::new(DeniedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;

    // Create a tool that requires a different capability (GitStatusRead)
    let git_status_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "git status".to_string(),
            )]))
        },
    );

    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "git_status".to_string(),
            description: "Get git status".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {}
            }),
        },
        required_capability: McpCapability::GitStatusRead,
        is_mutating: None,
    };

    let registry = ToolRegistry::new(vec![(metadata, git_status_handler)]);
    let server = make_test_server(session, workspace, registry);

    // Initialize first
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    // Call tool that requires capability we don't have (DeniedSession only has WorkspaceRead)
    let tool_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "git_status",
            "arguments": {}
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(tool_request, state);
    let response = response.expect("handle_request should return a response for non-notification");

    assert!(
        response.error.is_some(),
        "Expected error for capability denied"
    );
}

#[test]
fn test_tools_list_shows_available_tools() {
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = make_registry_with_read_tool();
    let server = make_test_server(session, workspace, registry);

    // Initialize first
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    // List tools
    let list_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(list_request, state);
    let response = response.expect("handle_request should return a response for non-notification");

    assert!(response.result.is_some());
    let result = response.result.unwrap();
    let tools = result["tools"].as_array().unwrap();
    assert_eq!(tools.len(), 1);
    assert_eq!(tools[0]["name"], "test_read");
}

#[test]
fn test_tool_call_invalidates_state_on_error() {
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);
    let server = make_test_server(session, workspace, registry);

    // Initialize
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // Call tool - should fail but state should remain Ready
    let tool_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "nonexistent",
            "arguments": {}
        })),
        id: Some(serde_json::json!(2)),
    };

    let (_, new_state) = server.handle_request(tool_request, state);

    // State should remain Ready even after tool error
    assert_eq!(new_state, ServerState::Ready);
}

// ---------------------------------------------------------------------------
// Minimum SPEC capability variant tests (FileRead, FileWrite, GitRead, ArtifactSubmit)
// These ensure the baseline capability dispatch is correct per SPEC requirements.
// ---------------------------------------------------------------------------

/// Create a tool that requires a specific capability.
fn make_tool_with_capability(name: &str, cap: McpCapability) -> (ToolMetadata, ToolHandler) {
    let name_string = name.to_string();
    let handler: ToolHandler = Arc::new(
        move |_session: &dyn mcp_server::HostSession,
              _workspace: &dyn mcp_server::WorkspaceAdapter,
              _params: serde_json::Value|
              -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(format!(
                "Tool '{}' executed successfully",
                name_string
            ))]))
        },
    );

    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: name.to_string(),
            description: format!("Tool requiring {:?}", cap),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {}
            }),
        },
        required_capability: cap,
        is_mutating: None,
    };

    (metadata, handler)
}

fn make_server_with_capability_tool(
    session: Arc<dyn mcp_server::HostSession>,
    workspace: Arc<dyn mcp_server::WorkspaceAdapter>,
    cap: McpCapability,
) -> McpServer {
    let (metadata, handler) = make_tool_with_capability("test_tool", cap);
    let registry = ToolRegistry::new(vec![(metadata, handler)]);
    make_test_server(session, workspace, registry)
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

fn call_tool(_server: &McpServer, _state: ServerState) -> JsonRpcRequest {
    JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "test_tool",
            "arguments": {}
        })),
        id: Some(serde_json::json!(2)),
    }
}

#[test]
fn test_file_read_capability_allowed() {
    // FileRead should be allowed by ApprovedSession
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server = make_server_with_capability_tool(session, workspace, McpCapability::FileRead);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.result.is_some(),
        "FileRead should be allowed by ApprovedSession"
    );
    assert!(response.error.is_none());
}

#[test]
fn test_file_read_capability_denied() {
    // FileRead should be denied by DeniedSession (only WorkspaceRead is allowed)
    let session = Arc::new(DeniedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server = make_server_with_capability_tool(session, workspace, McpCapability::FileRead);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.error.is_some(),
        "FileRead should be denied by DeniedSession"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
}

#[test]
fn test_file_write_capability_allowed() {
    // FileWrite should be allowed by ApprovedSession
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server = make_server_with_capability_tool(session, workspace, McpCapability::FileWrite);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.result.is_some(),
        "FileWrite should be allowed by ApprovedSession"
    );
    assert!(response.error.is_none());
}

#[test]
fn test_file_write_capability_denied() {
    // FileWrite should be denied by DeniedSession (only WorkspaceRead is allowed)
    let session = Arc::new(DeniedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server = make_server_with_capability_tool(session, workspace, McpCapability::FileWrite);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.error.is_some(),
        "FileWrite should be denied by DeniedSession"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
}

#[test]
fn test_git_read_capability_allowed() {
    // GitRead should be allowed by ApprovedSession
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server = make_server_with_capability_tool(session, workspace, McpCapability::GitRead);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.result.is_some(),
        "GitRead should be allowed by ApprovedSession"
    );
    assert!(response.error.is_none());
}

#[test]
fn test_git_read_capability_denied() {
    // GitRead should be denied by DeniedSession (only WorkspaceRead is allowed)
    let session = Arc::new(DeniedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server = make_server_with_capability_tool(session, workspace, McpCapability::GitRead);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.error.is_some(),
        "GitRead should be denied by DeniedSession"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
}

#[test]
fn test_artifact_submit_capability_allowed() {
    // ArtifactSubmit should be allowed by ApprovedSession
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server =
        make_server_with_capability_tool(session, workspace, McpCapability::ArtifactSubmit);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.result.is_some(),
        "ArtifactSubmit should be allowed by ApprovedSession"
    );
    assert!(response.error.is_none());
}

#[test]
fn test_artifact_submit_capability_denied() {
    // ArtifactSubmit should be denied by DeniedSession (only WorkspaceRead is allowed)
    let session = Arc::new(DeniedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server =
        make_server_with_capability_tool(session, workspace, McpCapability::ArtifactSubmit);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.error.is_some(),
        "ArtifactSubmit should be denied by DeniedSession"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
}

#[test]
fn test_workspace_coordination_capability_allowed_by_approved() {
    // WorkspaceCoordination should be allowed by ApprovedSession
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server =
        make_server_with_capability_tool(session, workspace, McpCapability::WorkspaceCoordination);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.result.is_some(),
        "WorkspaceCoordination should be allowed by ApprovedSession"
    );
    assert!(response.error.is_none());
}

#[test]
fn test_workspace_coordination_capability_denied_by_denied_session() {
    // WorkspaceCoordination is denied by DeniedSession (only WorkspaceRead is allowed).
    // The "always allow" policy for WorkspaceCoordination is a ralph-workflow adapter
    // choice, not built into the standalone mcp-server.
    let session = Arc::new(DeniedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server =
        make_server_with_capability_tool(session, workspace, McpCapability::WorkspaceCoordination);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    // DeniedSession only grants WorkspaceRead — WorkspaceCoordination is denied
    assert!(
        response.error.is_some(),
        "WorkspaceCoordination should be denied by DeniedSession"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
}

#[test]
fn test_process_exec_capability_allowed() {
    // ProcessExec should be allowed by ApprovedSession
    let session = Arc::new(ApprovedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server = make_server_with_capability_tool(session, workspace, McpCapability::ProcessExec);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.result.is_some(),
        "ProcessExec should be allowed by ApprovedSession"
    );
    assert!(response.error.is_none());
}

#[test]
fn test_process_exec_capability_denied() {
    // ProcessExec should be denied by DeniedSession (only WorkspaceRead is allowed)
    let session = Arc::new(DeniedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let server = make_server_with_capability_tool(session, workspace, McpCapability::ProcessExec);
    let state = initialize_server(&server);

    let (response, _) = server.handle_request(call_tool(&server, state), state);
    let response = response.expect("handle_request should return a response");

    assert!(
        response.error.is_some(),
        "ProcessExec should be denied by DeniedSession"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
}

// ---------------------------------------------------------------------------
// Ordering tests: capability checks fire AFTER tool-filter and access-mode gates
// ---------------------------------------------------------------------------

#[test]
fn test_tool_filter_fires_before_capability_check() {
    // A DeniedSession that would deny all capabilities — but the tool is also blocked
    // by an Allowlist that doesn't contain it.
    // The error should indicate ToolNotAllowed (from tool-filter), NOT CapabilityDenied.
    use mcp_server::dispatch::access::AccessDeniedCode;
    use mcp_server::io::access::McpServerConfig;

    let session = Arc::new(DeniedSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn mcp_server::WorkspaceAdapter>;

    // Register test_tool but Allowlist only has "other_tool"
    let (metadata, handler) = make_tool_with_capability("test_tool", McpCapability::ArtifactSubmit);
    let registry = ToolRegistry::new(vec![(metadata, handler)]);

    let config = McpServerConfig::new(std::path::Path::new("/tmp").to_path_buf()).with_tool_filter(
        mcp_server::dispatch::access::ToolFilter::Allowlist(vec!["other_tool".to_string()]),
    );

    let server = McpServer::new(session, config, workspace, registry, None);
    let state = initialize_server(&server);

    let tool_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "test_tool",
            "arguments": {}
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(tool_request, state);
    let response = response.expect("handle_request should return a response");

    assert!(response.error.is_some(), "Expected error from tool filter");
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    // The denial should be ToolNotAllowed, not CapabilityDenied
    assert!(
        error.message.contains("not allowed") || error.message.contains("ToolNotAllowed"),
        "Error should indicate ToolNotAllowed (tool filter fires before capability check), got: {}",
        error.message
    );
    // Verify it does NOT say CapabilityDenied (which would indicate wrong ordering)
    let denied_code = error.data.as_ref().and_then(|d| d.as_str());
    if let Some(code) = denied_code {
        assert_ne!(
            code,
            format!("{:?}", AccessDeniedCode::CapabilityDenied),
            "Capability check must not fire before tool-filter check"
        );
    }
}
