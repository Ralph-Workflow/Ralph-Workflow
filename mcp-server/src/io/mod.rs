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
    route_dispatch, DispatchTarget, HostSession, ToolError, ToolRegistry, WorkspaceAdapter,
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
    required_capability: Option<crate::dispatch::access::McpCapability>,
    capability_outcome: Option<crate::dispatch::access::AccessDecision>,
    audit_sink: &dyn AuditSink,
) -> AccessDecision {
    // Thin wiring: directly construct EnforcementContext with all inputs.
    // No branching here - all conditional logic is in the caller (check_enforcement).
    let ctx = crate::io::access::EnforcementContext {
        config,
        tool_name,
        path,
        is_mutating,
        required_capability,
        capability_outcome,
        audit_sink,
    };
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
    audit_sink: Arc<dyn AuditSink>,
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

/// Parameters for [`check_enforcement`].
///
/// Bundles enforcement inputs to avoid clippy's 7-parameter limit.
struct CheckEnforcementParams<'a> {
    config: &'a crate::io::access::McpServerConfig,
    registry: &'a ToolRegistry,
    session: &'a dyn HostSession,
    name: &'a str,
    args: &'a serde_json::Value,
    request_id: serde_json::Value,
    state: ServerState,
    audit_sink: &'a dyn AuditSink,
}

/// Check enforcement or return error response.
///
/// Looks up tool metadata from the registry to determine `is_mutating` and
/// `required_capability` for enforcement checks. Also consults the host session
/// for capability-based access decisions (priority 4 in the enforcement chain).
fn check_enforcement(
    params: CheckEnforcementParams,
) -> Result<(), Box<(JsonRpcResponse, ServerState)>> {
    let path = params.args.get("path").and_then(|v| v.as_str());
    let path_for_check = path.map(Path::new);

    // Look up metadata from registry - this replaces the hardcoded is_mutating_tool() check.
    // The metadata's is_mutating field is derived from required_capability at registration time,
    // which correctly handles all tool name prefixes (e.g., "ralph_write_file", "write_file").
    let (is_mutating, required_capability) = params
        .registry
        .get_metadata(params.name)
        .map(|m| (m.is_mutating(), Some(m.required_capability)))
        .unwrap_or((false, None));

    // Check capability with host session (priority 4 in enforcement chain).
    // This is the only enforcement check that delegates to the host.
    let capability_outcome = required_capability.map(|cap| params.session.check_capability(cap));

    match check_tool_enforcement(
        params.config,
        params.name,
        path_for_check,
        is_mutating,
        required_capability,
        capability_outcome,
        params.audit_sink,
    ) {
        AccessDecision::Allow => Ok(()),
        AccessDecision::Deny { reason, code } => Err(Box::new((
            JsonRpcResponse::error(
                JsonRpcError::tool_error_with_data(
                    format!("Access denied: {}", reason),
                    serde_json::json!({ "reason": reason, "code": code }),
                ),
                params.request_id,
            ),
            params.state,
        ))),
    }
}

/// Dispatch tool call or return error response.
///
/// Tool errors are categorized as:
/// - `NotFound` → JSON-RPC error -32601 (method not found semantics - tool doesn't exist)
/// - `CapabilityDenied` → JSON-RPC error -32000 (tool error - access denied)
/// - `InvalidParams` → JSON-RPC error -32000 (tool error - invalid tool parameters)
/// - `ExecutionError` → JSON-RPC error -32000 (tool error - tool execution failure)
///
/// Per mcp-server protocol, tool errors are returned as JSON-RPC error responses with
/// code -32000 (Tool error). This includes capability denials, invalid parameters,
/// and execution failures.
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
        .map_err(move |e: ToolError| {
            Box::new(match e {
                // NotFound is a JSON-RPC error because the tool literally doesn't exist
                ToolError::NotFound(_msg) => (
                    JsonRpcResponse::error(JsonRpcError::method_not_found(), request_id_inner),
                    state,
                ),
                // Tool errors (capability denied, invalid params, execution failure) are
                // returned as JSON-RPC error responses with code -32000 (Tool error).
                ToolError::CapabilityDenied(msg) => (
                    JsonRpcResponse::error(
                        JsonRpcError::tool_error_with_data(
                            format!("Access denied: {}", msg),
                            serde_json::json!({ "reason": msg, "code": "CapabilityDenied" }),
                        ),
                        request_id_inner,
                    ),
                    state,
                ),
                ToolError::InvalidParams(msg) => (
                    JsonRpcResponse::error(
                        JsonRpcError::tool_error_with_data(
                            format!("Invalid params: {}", msg),
                            serde_json::json!({ "error": msg }),
                        ),
                        request_id_inner,
                    ),
                    state,
                ),
                ToolError::ExecutionError(msg) => (
                    JsonRpcResponse::error(
                        JsonRpcError::tool_error_with_data(
                            format!("Tool error: {}", msg),
                            serde_json::json!({ "error": msg }),
                        ),
                        request_id_inner,
                    ),
                    state,
                ),
            })
        })
}

// ---------------------------------------------------------------------------
// Pure helpers for error responses
// ---------------------------------------------------------------------------

/// Pure: make not-ready error response.
fn make_not_ready_error(
    request_id: serde_json::Value,
    state: ServerState,
) -> (Option<JsonRpcResponse>, ServerState) {
    (
        Some(JsonRpcResponse::error(
            JsonRpcError::not_initialized(),
            request_id,
        )),
        state,
    )
}

/// Pure: make method-not-found error response.
fn make_method_not_found_error(
    request_id: serde_json::Value,
    state: ServerState,
) -> (Option<JsonRpcResponse>, ServerState) {
    (
        Some(JsonRpcResponse::error(
            JsonRpcError::method_not_found(),
            request_id,
        )),
        state,
    )
}

impl McpServer {
    /// Create a new MCP server.
    ///
    /// # Arguments
    ///
    /// * `session` - Host session for capability checking
    /// * `config` - Server configuration (root_dir, access_mode, tool_filter)
    /// * `workspace` - Workspace adapter for file operations
    /// * `registry` - Tool registry with registered tools
    /// * `audit_sink` - Audit sink for recording access decisions (defaults to NoOpAuditSink if None)
    pub fn new(
        session: Arc<dyn HostSession>,
        config: crate::io::access::McpServerConfig,
        workspace: Arc<dyn WorkspaceAdapter>,
        registry: ToolRegistry,
        audit_sink: Option<Arc<dyn AuditSink>>,
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
            audit_sink: audit_sink.unwrap_or_else(|| Arc::new(NoOpAuditSink)),
        }
    }

    /// Handle an incoming JSON-RPC request (thin wiring).
    ///
    /// Returns `None` for notifications (requests without an id) since JSON-RPC 2.0
    /// requires no response for notifications.
    ///
    /// # State Machine
    ///
    /// The server maintains a `ServerState` that governs which requests are accepted:
    ///
    /// - **`Uninitialized`** — Server created but not initialized. Only `initialize`
    ///   is accepted. All other methods return `-32001 NotInitialized`.
    /// - **`Ready`** — Normal operational state after successful `initialize` handshake.
    ///   All methods are accepted.
    /// - **`Shutdown`** — Server has shut down. No requests are accepted (currently
    ///   treated same as `Ready` for routing purposes).
    ///
    /// # DispatchTarget Branches
    ///
    /// | Target | Condition | Response | New State |
    /// |--------|-----------|----------|-----------|
    /// | `Initialize` | `initialize` method, any state | `InitializeResult` JSON | `Ready` (if success) |
    /// | `NotReady` | Any tool method, `state != Ready` | `NotInitialized` error `-32001` | unchanged |
    /// | `Ping` | `ping` method, `state == Ready` | `null` | unchanged |
    /// | `ToolsList` | `tools/list` method, `state == Ready` | `{tools: [...]}` | unchanged |
    /// | `ToolsCall` | `tools/call` method, `state == Ready` | Tool result or error | unchanged |
    /// | `Unknown` | Unknown method, any state | `MethodNotFound` error `-32601` | unchanged |
    ///
    /// # Response Contract
    ///
    /// - Returns `(Some(response), new_state)` for requests with an `id` field
    /// - Returns `(None, state)` for notifications (no `id` field) — no response sent
    /// - Error responses use `JsonRpcResponse::error()` with appropriate `JsonRpcError` code
    /// - Success responses use `JsonRpcResponse::success()` with result value
    pub fn handle_request(
        &self,
        request: JsonRpcRequest,
        state: ServerState,
    ) -> (Option<JsonRpcResponse>, ServerState) {
        // Notifications (no id) don't get a response per JSON-RPC 2.0 spec
        let request_id = match request.id.clone() {
            Some(id) => id,
            None => return (None, state),
        };

        let target = route_dispatch(request.method.as_str(), state == ServerState::Ready);
        self.route_to_target(target, request, request_id, state)
    }

    /// Route to the appropriate handler or error helper (pure policy).
    fn route_to_target(
        &self,
        target: DispatchTarget,
        request: JsonRpcRequest,
        request_id: serde_json::Value,
        state: ServerState,
    ) -> (Option<JsonRpcResponse>, ServerState) {
        match target {
            DispatchTarget::Initialize => self.handle_initialize(request, request_id),
            DispatchTarget::NotReady => make_not_ready_error(request_id, state),
            DispatchTarget::Ping => self.handle_ping(request_id, state),
            DispatchTarget::ToolsList => self.handle_tools_list(request_id, state),
            DispatchTarget::ToolsCall => self.handle_tools_call(request, request_id, state),
            DispatchTarget::Unknown => make_method_not_found_error(request_id, state),
        }
    }

    fn handle_initialize(
        &self,
        request: JsonRpcRequest,
        request_id: serde_json::Value,
    ) -> (Option<JsonRpcResponse>, ServerState) {
        let params = match parse_initialize_params(request.params, request_id.clone()) {
            Ok(p) => p,
            Err(e) => {
                let (response, st) = *e;
                return (Some(response), st);
            }
        };
        let version = negotiate_protocol_version(params.protocol_version);
        let result = build_initialize_result(version, self.server_info.clone());
        (
            Some(JsonRpcResponse::success(
                serde_json::to_value(result).unwrap(),
                request_id,
            )),
            ServerState::Ready,
        )
    }

    fn handle_ping(
        &self,
        request_id: serde_json::Value,
        state: ServerState,
    ) -> (Option<JsonRpcResponse>, ServerState) {
        (
            Some(JsonRpcResponse::success(
                serde_json::Value::Null,
                request_id,
            )),
            state,
        )
    }

    fn handle_tools_list(
        &self,
        request_id: serde_json::Value,
        state: ServerState,
    ) -> (Option<JsonRpcResponse>, ServerState) {
        let tools = self.registry.list_tools();
        (
            Some(JsonRpcResponse::success(
                serde_json::json!({ "tools": tools }),
                request_id,
            )),
            state,
        )
    }

    fn handle_tools_call(
        &self,
        request: JsonRpcRequest,
        request_id: serde_json::Value,
        state: ServerState,
    ) -> (Option<JsonRpcResponse>, ServerState) {
        // Chain: parse -> check enforcement (with capability check) -> dispatch
        let result =
            parse_tools_call_params(request.params, request_id.clone(), state).and_then(|params| {
                check_enforcement(CheckEnforcementParams {
                    config: &self.config,
                    registry: &self.registry,
                    session: self.session.as_ref(),
                    name: &params.name,
                    args: &params.arguments,
                    request_id: request_id.clone(),
                    state,
                    audit_sink: self.audit_sink.as_ref(),
                })
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
            .map(|(resp, st)| (Some(resp), st))
            .unwrap_or_else(|e| {
                let (response, st) = *e;
                (Some(response), st)
            }),
            Err(box_val) => {
                let (response, st) = *box_val;
                (Some(response), st)
            }
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
        let server = McpServer::new(session, config, workspace, registry, None);

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "initialize".to_string(),
            params: Some(serde_json::json!({
                "protocolVersion": "2024-11-05",
                "clientInfo": { "name": "test", "version": "1.0" }
            })),
            id: Some(serde_json::json!(1)),
        };

        let (response, state) = server.handle_request(request, ServerState::Uninitialized);
        let response = response.expect("initialize should return a response");
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
        let server = McpServer::new(session, config, workspace, registry, None);

        // Initialize first
        let init_request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "initialize".to_string(),
            params: Some(serde_json::json!({ "protocolVersion": "2024-11-05" })),
            id: Some(serde_json::json!(1)),
        };
        let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

        // List tools
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            method: "tools/list".to_string(),
            params: None,
            id: Some(serde_json::json!(2)),
        };

        let (response, _) = server.handle_request(request, state);
        let response = response.expect("tools/list should return a response");
        assert!(response.result.is_some());
    }
}
