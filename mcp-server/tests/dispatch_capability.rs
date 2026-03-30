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
    fn is_parallel_worker(&self) -> bool {
        false
    }
    fn check_edit_area(&self, _path: &str) -> AccessDecision {
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
    fn is_parallel_worker(&self) -> bool {
        false
    }
    fn check_edit_area(&self, _path: &str) -> AccessDecision {
        AccessDecision::Allow
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
    assert_eq!(error.code, -32000, "Should be tool error");
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
