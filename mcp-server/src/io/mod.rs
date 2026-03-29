//! I/O boundary module for MCP server.
//!
//! This module is designated as a boundary module per Dylint rules. It contains
//! all actual I/O effects: transport framing, socket handling, and stdio.
//!
//! # Boundary Classification
//!
//! Code in `io/` is exempt from functional purity restrictions because its
//! primary purpose is performing real side effects (network I/O, stdio).
//!
//! # Module Structure
//!
//! - [`access`] - Access control types that require I/O (SystemTime, Mutex, std::fs)
//! - [`transport`] - Content-Length framed JSON-RPC transport for stdio and Unix sockets
//! - [`session_bridge`] - MCP server lifecycle management with Unix socket transport
//! - [`fake`] - In-memory fake transport for deterministic testing
//! - [`McpServer`] - MCP server state and request handling

pub mod access;
pub mod session_bridge;
pub mod transport;

// fake module is always available for testing utilities
pub mod fake;

pub use session_bridge::{SessionBridge, SessionBridgeError, MCP_ENDPOINT_ENV};
pub use transport::{McpStream, StdioTransport, TransportError, UnixSocketTransport};

use crate::dispatch::access::{AccessDecision, AuditSink, NoOpAuditSink};
use crate::dispatch::{
    route_dispatch, DispatchTarget, HostSession, ToolRegistry, WorkspaceAdapter,
};
use crate::protocol::{
    JsonRpcError, JsonRpcRequest, JsonRpcResponse, ServerCapabilities, ServerInfo, ToolsCapability,
    MCP_PROTOCOL_VERSION,
};
use std::path::Path;
use std::sync::Arc;

/// Check tool enforcement - boundary function that creates and checks EnforcementContext.
///
/// This function exists to keep enforcement logic in the boundary module while allowing
/// lib.rs (non-boundary) to invoke it without importing I/O types directly.
pub fn check_tool_enforcement(
    config: &crate::io::access::McpServerConfig,
    tool_name: &str,
    path: Option<&Path>,
    is_mutating: bool,
    audit_sink: &dyn AuditSink,
) -> AccessDecision {
    let mut ctx = crate::io::access::EnforcementContext::new(config, tool_name, audit_sink);
    if let Some(p) = path {
        ctx = ctx.with_path(p);
    }
    if is_mutating {
        ctx = ctx.with_mutating(true);
    }
    ctx.check()
}

/// Server state for MCP protocol.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ServerState {
    /// Server created but not initialized.
    Uninitialized,
    /// Server initialized after successful handshake.
    Ready,
    /// Server shutting down.
    Shutdown,
}

/// MCP Server instance.
///
/// Manages the MCP protocol lifecycle, request dispatch, and tool execution.
///
/// # Example
///
/// ```ignore
/// use mcp_server::io::{McpServer, ServerState};
/// use mcp_server::dispatch::{HostSession, WorkspaceAdapter, ToolRegistry};
/// use mcp_server::io::access::McpServerConfig;
/// use std::sync::Arc;
///
/// let session: Arc<dyn HostSession> = Arc::new(MySession);
/// let workspace: Arc<dyn WorkspaceAdapter> = Arc::new(MyWorkspace);
/// let config = McpServerConfig::new("/tmp");
/// let registry = ToolRegistry::new(vec![]);
/// let server = McpServer::new(session, config, workspace, registry);
/// let (response, state) = server.handle_request(request, ServerState::Uninitialized);
/// ```
pub struct McpServer {
    session: Arc<dyn HostSession>,
    config: crate::io::access::McpServerConfig,
    workspace: Arc<dyn WorkspaceAdapter>,
    registry: ToolRegistry,
    server_info: ServerInfo,
}

// ---------------------------------------------------------------------------
// Boundary request handlers (thin wiring)
// ---------------------------------------------------------------------------

/// Parse initialize params or return error response.
fn parse_initialize_params(
    params: Option<serde_json::Value>,
    request_id: serde_json::Value,
) -> Result<crate::protocol::InitializeParams, Box<(JsonRpcResponse, ServerState)>> {
    serde_json::from_value(params.unwrap_or(serde_json::json!({}))).map_err(|e| {
        Box::new((
            JsonRpcResponse::error(
                JsonRpcError::invalid_params(format!("Invalid initialize params: {}", e)),
                request_id,
            ),
            ServerState::Uninitialized,
        ))
    })
}

/// Determine protocol version (pure).
fn negotiate_protocol_version(client_version: String) -> String {
    if client_version != MCP_PROTOCOL_VERSION {
        MCP_PROTOCOL_VERSION.to_string()
    } else {
        client_version
    }
}

/// Build initialize result (pure).
fn build_initialize_result(
    protocol_version: String,
    server_info: ServerInfo,
) -> crate::protocol::InitializeResult {
    crate::protocol::InitializeResult {
        protocol_version,
        capabilities: ServerCapabilities {
            tools: Some(ToolsCapability { list_changed: true }),
        },
        server_info,
    }
}

/// Parse tools/call params or return error response.
#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct ToolsCallParams {
    name: String,
    #[serde(default)]
    arguments: serde_json::Value,
}

fn parse_tools_call_params(
    params: Option<serde_json::Value>,
    request_id: serde_json::Value,
    state: ServerState,
) -> Result<ToolsCallParams, Box<(JsonRpcResponse, ServerState)>> {
    serde_json::from_value(params.unwrap_or(serde_json::json!({}))).map_err(|e| {
        Box::new((
            JsonRpcResponse::error(
                JsonRpcError::invalid_params(format!("Invalid tools/call params: {}", e)),
                request_id,
            ),
            state,
        ))
    })
}

/// Check if tool is mutating (pure).
fn is_mutating_tool(name: &str) -> bool {
    matches!(name, "write_file" | "delete_file" | "move_file")
}

/// Check enforcement or return error response.
fn check_enforcement(
    config: &crate::io::access::McpServerConfig,
    name: &str,
    args: &serde_json::Value,
    request_id: serde_json::Value,
    state: ServerState,
) -> Result<(), Box<(JsonRpcResponse, ServerState)>> {
    let path = args.get("path").and_then(|v| v.as_str());
    let mutating = is_mutating_tool(name);
    let path_for_check = path.map(Path::new);
    match check_tool_enforcement(config, name, path_for_check, mutating, &NoOpAuditSink) {
        AccessDecision::Allow => Ok(()),
        AccessDecision::Deny { reason, .. } => Err(Box::new((
            JsonRpcResponse::error(JsonRpcError::tool_error(reason), request_id),
            state,
        ))),
    }
}

/// Dispatch tool call or return error response.
fn dispatch_tool(
    registry: &ToolRegistry,
    name: &str,
    arguments: serde_json::Value,
    session: &dyn HostSession,
    workspace: &dyn WorkspaceAdapter,
    request_id: serde_json::Value,
    state: ServerState,
) -> Result<(JsonRpcResponse, ServerState), Box<(JsonRpcResponse, ServerState)>> {
    let request_id_inner = request_id.clone();
    registry
        .dispatch(name, arguments, session, workspace)
        .map(move |result| {
            (
                JsonRpcResponse::success(serde_json::to_value(result).unwrap(), request_id),
                state,
            )
        })
        .map_err(move |e| {
            Box::new((
                JsonRpcResponse::error(JsonRpcError::tool_error(e.to_string()), request_id_inner),
                state,
            ))
        })
}

impl McpServer {
    /// Create a new MCP server.
    pub fn new(
        session: Arc<dyn HostSession>,
        config: crate::io::access::McpServerConfig,
        workspace: Arc<dyn WorkspaceAdapter>,
        registry: ToolRegistry,
    ) -> Self {
        Self {
            session,
            config,
            workspace,
            registry,
            server_info: ServerInfo {
                name: "ralph-mcp".to_string(),
                version: env!("CARGO_PKG_VERSION").to_string(),
            },
        }
    }

    /// Handle an incoming JSON-RPC request (thin wiring).
    pub fn handle_request(
        &self,
        request: JsonRpcRequest,
        state: ServerState,
    ) -> (JsonRpcResponse, ServerState) {
        let target = route_dispatch(request.method.as_str(), state == ServerState::Ready);
        match target {
            DispatchTarget::Initialize => self.handle_initialize(request),
            DispatchTarget::NotReady => (
                JsonRpcResponse::error(JsonRpcError::not_initialized(), request.id),
                state,
            ),
            DispatchTarget::Ping => self.handle_ping(request, state),
            DispatchTarget::ToolsList => self.handle_tools_list(request, state),
            DispatchTarget::ToolsCall => self.handle_tools_call(request, state),
            DispatchTarget::Unknown => (
                JsonRpcResponse::error(JsonRpcError::method_not_found(), request.id),
                state,
            ),
        }
    }

    fn handle_initialize(&self, request: JsonRpcRequest) -> (JsonRpcResponse, ServerState) {
        let request_id = request.id.clone();
        let params = match parse_initialize_params(request.params, request_id.clone()) {
            Ok(p) => p,
            Err(e) => return *e,
        };
        let version = negotiate_protocol_version(params.protocol_version);
        let result = build_initialize_result(version, self.server_info.clone());
        (
            JsonRpcResponse::success(serde_json::to_value(result).unwrap(), request_id),
            ServerState::Ready,
        )
    }

    fn handle_ping(
        &self,
        request: JsonRpcRequest,
        state: ServerState,
    ) -> (JsonRpcResponse, ServerState) {
        (
            JsonRpcResponse::success(serde_json::Value::Null, request.id),
            state,
        )
    }

    fn handle_tools_list(
        &self,
        request: JsonRpcRequest,
        state: ServerState,
    ) -> (JsonRpcResponse, ServerState) {
        let tools = self.registry.list_tools();
        (
            JsonRpcResponse::success(serde_json::json!({ "tools": tools }), request.id),
            state,
        )
    }

    fn handle_tools_call(
        &self,
        request: JsonRpcRequest,
        state: ServerState,
    ) -> (JsonRpcResponse, ServerState) {
        let request_id = request.id.clone();
        // Chain: parse -> check enforcement -> dispatch
        let result =
            parse_tools_call_params(request.params, request_id.clone(), state).and_then(|params| {
                check_enforcement(
                    &self.config,
                    &params.name,
                    &params.arguments,
                    request_id.clone(),
                    state,
                )
                .map(|_| params)
            });

        match result {
            Ok(params) => dispatch_tool(
                &self.registry,
                &params.name,
                params.arguments,
                self.session.as_ref(),
                self.workspace.as_ref(),
                request_id,
                state,
            )
            .unwrap_or_else(|e| *e),
            Err(box_val) => *box_val,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dispatch::access::{AccessDecision, McpCapability};
    use crate::dispatch::host::DirEntry;
    use crate::dispatch::ToolRegistry;
    use std::path::Path;

    struct MockSession;
    impl HostSession for MockSession {
        fn session_id(&self) -> &str {
            "test-session"
        }
        fn check_capability(&self, cap: McpCapability) -> AccessDecision {
            if cap == McpCapability::WorkspaceRead {
                AccessDecision::Allow
            } else {
                AccessDecision::Deny {
                    reason: format!("Missing capability: {}", cap),
                    code: crate::dispatch::access::AccessDeniedCode::CapabilityDenied,
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
    impl WorkspaceAdapter for MockWorkspace {
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

    #[test]
    fn test_server_initialization() {
        let session = Arc::new(MockSession) as Arc<dyn HostSession>;
        let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
        let registry = ToolRegistry::new(vec![]);
        let config = crate::io::access::McpServerConfig::new(std::env::temp_dir());
        let server = McpServer::new(session, config, workspace, registry);

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "initialize".to_string(),
            params: Some(serde_json::json!({
                "protocolVersion": "2024-11-05",
                "clientInfo": { "name": "test", "version": "1.0" }
            })),
            id: serde_json::json!(1),
        };

        let (response, state) = server.handle_request(request, ServerState::Uninitialized);
        assert!(response.result.is_some());
        assert!(response.error.is_none());
        assert_eq!(state, ServerState::Ready);
    }

    #[test]
    fn test_tools_list() {
        let session = Arc::new(MockSession) as Arc<dyn HostSession>;
        let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
        let registry = ToolRegistry::new(vec![]);
        let config = crate::io::access::McpServerConfig::new(std::env::temp_dir());
        let server = McpServer::new(session, config, workspace, registry);

        // Initialize first
        let init_request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "initialize".to_string(),
            params: Some(serde_json::json!({ "protocolVersion": "2024-11-05" })),
            id: serde_json::json!(1),
        };
        let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

        // List tools
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "tools/list".to_string(),
            params: None,
            id: serde_json::json!(2),
        };

        let (response, _) = server.handle_request(request, state);
        assert!(response.result.is_some());
    }
}
