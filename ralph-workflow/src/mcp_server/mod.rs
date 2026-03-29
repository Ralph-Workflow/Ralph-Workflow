//! MCP server implementation for RFC-009 Phase 3.
//!
//! This module provides the core MCP server that runs alongside each agent
//! process, brokerling all tool calls through Ralph's capability system.
//!
//! # Architecture
//!
//! ```text
//! Agent Process <--JSON-RPC--> Ralph MCP Server <--> AgentSession (capabilities)
//!                                    |
//!                                    +--> ToolRegistry (handler dispatch)
//!                                    |
//!                                    +--> AuditTrail (record all calls)
//! ```
//!
//! # Session Binding
//!
//! The MCP server is created with a reference to an `AgentSession` that
//! defines the capabilities and policy for this agent invocation. Every
//! tool call goes through:
//!
//! 1. Parse JSON-RPC request
//! 2. Check tool exists in registry
//! 3. Check session has required capabilities
//! 4. Execute handler
//! 5. Record audit entry
//! 6. Return result or error

pub mod proxy;
pub mod session_bridge;
pub mod tool_artifact;
pub mod tool_coordination;
pub mod tool_exec;
pub mod tool_git_read;
pub mod tool_registry;
pub mod tool_workspace;
pub mod transport;
pub mod types;

#[cfg(test)]
mod tests;

use crate::agents::session::{AgentSession, AuditRecord, AuditTrail, Capability, PolicyOutcome};
use crate::mcp_server::tool_registry::{ToolError, ToolRegistry};
use crate::mcp_server::transport::{McpStream, StdioTransport, UnixSocketTransport};
use crate::mcp_server::types::{
    InitializeParams, InitializeResult, JsonRpcRequest, JsonRpcResponse, ToolContent, ToolResult,
};
use crate::workspace::Workspace;
use anyhow::Result;
use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::Arc;
use std::time::Duration;

/// MCP server errors.
#[derive(Debug, thiserror::Error)]
pub enum McpServerError {
    #[error("Transport error: {0}")]
    Transport(#[from] std::io::Error),

    #[error("JSON-RPC error: {0}")]
    JsonRpc(String),

    #[error("Tool error: {0}")]
    Tool(#[from] ToolError),

    #[error("Unexpected method: {0}")]
    UnexpectedMethod(String),

    #[error("Server not initialized")]
    NotInitialized,
}

impl From<McpServerError> for types::JsonRpcError {
    fn from(err: McpServerError) -> Self {
        match err {
            McpServerError::JsonRpc(msg) => types::JsonRpcError::internal_error(&msg),
            McpServerError::Tool(tool_err) => {
                types::JsonRpcError::tool_error(&tool_err.to_string())
            }
            McpServerError::UnexpectedMethod(method) => {
                types::JsonRpcError::method_not_found(&method)
            }
            McpServerError::NotInitialized => {
                types::JsonRpcError::internal_error("Server not initialized")
            }
            other => types::JsonRpcError::internal_error(&other.to_string()),
        }
    }
}

/// MCP server state.
#[derive(Debug, Clone)]
enum ServerState {
    /// Server has not been initialized by client.
    Uninitialized,
    /// Server is initialized and ready to process tool calls.
    Ready,
    /// Server has been shut down.
    Shutdown,
}

/// MCP server that brokers tool calls for an agent session.
pub struct McpServer {
    /// The agent session this server is bound to.
    session: Arc<AgentSession>,
    /// Workspace for file operations.
    workspace: Arc<dyn Workspace>,
    /// Tool registry with all available tools.
    registry: ToolRegistry,
    /// Audit trail for recording tool calls.
    audit_trail: Arc<AuditTrail>,
    /// Server state.
    state: ServerState,
    /// Shared shutdown flag. Set to true to signal the server to shutdown.
    /// This is checked in the run loop to allow external shutdown.
    shutdown_flag: Arc<AtomicBool>,
    /// Optional channel sender for streaming audit records to the caller.
    audit_sender: Option<mpsc::Sender<AuditRecord>>,
}

impl McpServer {
    /// Create a new MCP server bound to the given session and workspace.
    ///
    /// The server will have all Ralph MCP tools registered.
    /// Uses a shared shutdown flag that can be set externally to signal shutdown.
    pub fn new(
        session: AgentSession,
        workspace: Arc<dyn Workspace>,
        shutdown_flag: Arc<AtomicBool>,
    ) -> Self {
        Self {
            session: Arc::new(session),
            workspace,
            registry: ToolRegistry::with_ralph_tools(),
            audit_trail: Arc::new(AuditTrail::new()),
            state: ServerState::Uninitialized,
            shutdown_flag,
            audit_sender: None,
        }
    }

    /// Create a new MCP server with a custom tool registry.
    pub fn with_registry(
        session: AgentSession,
        workspace: Arc<dyn Workspace>,
        registry: ToolRegistry,
        shutdown_flag: Arc<AtomicBool>,
    ) -> Self {
        Self {
            session: Arc::new(session),
            workspace,
            registry,
            audit_trail: Arc::new(AuditTrail::new()),
            state: ServerState::Uninitialized,
            shutdown_flag,
            audit_sender: None,
        }
    }

    /// Create a new MCP server with an audit channel sender.
    ///
    /// Audit records produced by tool calls are sent through `audit_sender` so the
    /// caller can accumulate them after the server thread exits.
    pub fn new_with_audit_sender(
        session: AgentSession,
        workspace: Arc<dyn Workspace>,
        shutdown_flag: Arc<AtomicBool>,
        audit_sender: mpsc::Sender<AuditRecord>,
    ) -> Self {
        Self {
            session: Arc::new(session),
            workspace,
            registry: ToolRegistry::with_ralph_tools(),
            audit_trail: Arc::new(AuditTrail::new()),
            state: ServerState::Uninitialized,
            shutdown_flag,
            audit_sender: Some(audit_sender),
        }
    }

    /// Get the session this server is bound to.
    pub fn session(&self) -> &AgentSession {
        &self.session
    }

    /// Get the audit trail.
    pub fn audit_trail(&self) -> &AuditTrail {
        &self.audit_trail
    }

    /// Run the server on stdin/stdout.
    ///
    /// This method blocks until the agent sends a shutdown notification
    /// or closes its input stream.
    pub fn run_stdio(&mut self) -> Result<()> {
        let mut transport = StdioTransport::with_default_stdio();
        self.run_loop(&mut transport)
    }

    /// Run the server with a Unix socket transport.
    ///
    /// This method:
    /// 1. Creates a Unix socket at the given path
    /// 2. Listens for agent connections
    /// 3. Handles MCP JSON-RPC requests from the agent
    /// 4. Runs until shutdown is called or the agent disconnects
    pub fn run_socket(&mut self, socket_path: &std::path::Path) -> Result<()> {
        let listener =
            UnixSocketTransport::new_with_shutdown(socket_path, Arc::clone(&self.shutdown_flag))?;
        self.run_accept_loop(listener)
    }

    /// Run the server with a Unix socket transport, sending a ready signal after the socket is
    /// bound and before the first accept call.
    ///
    /// This eliminates the race condition in `SessionBridge::start()` where the agent could be
    /// launched before the socket is listening. The caller blocks on `ready_tx` to ensure the
    /// socket is bound before proceeding.
    pub fn run_socket_with_ready(
        &mut self,
        socket_path: &std::path::Path,
        ready_tx: std::sync::mpsc::Sender<Result<(), String>>,
    ) {
        let listener = match UnixSocketTransport::new_with_shutdown(
            socket_path,
            Arc::clone(&self.shutdown_flag),
        ) {
            Ok(transport) => {
                // Socket is now bound and listening — unblock start() in the calling thread.
                let _ = ready_tx.send(Ok(()));
                transport
            }
            Err(e) => {
                let _ = ready_tx.send(Err(e.to_string()));
                return;
            }
        };
        if let Err(e) = self.run_accept_loop(listener) {
            eprintln!("MCP server accept loop error: {}", e);
        }
    }

    /// Check if the server should stop (shutdown state or flag).
    fn is_shutting_down(&self) -> bool {
        matches!(self.state, ServerState::Shutdown) || self.shutdown_flag.load(Ordering::Acquire)
    }

    /// Try to accept one connection and handle it.
    /// Returns Ok(true) if a connection was accepted, Ok(false) if none available.
    fn try_accept_and_handle(&mut self, listener: &UnixSocketTransport) -> Result<bool> {
        match listener.accept()? {
            None => Ok(false),
            Some(mut stream) => {
                self.handle_socket_stream(&mut stream)?;
                Ok(true)
            }
        }
    }

    /// Run one accept iteration. Returns false if the loop should stop.
    fn run_accept_iteration(
        &mut self,
        listener: &UnixSocketTransport,
        poll: Duration,
    ) -> Result<bool> {
        if self.is_shutting_down() {
            return Ok(false);
        }
        if !self.try_accept_and_handle(listener)? {
            std::thread::sleep(poll);
        }
        Ok(!self.is_shutting_down())
    }

    /// Inner accept loop shared by `run_socket` and `run_socket_with_ready`.
    fn run_accept_loop(&mut self, listener: UnixSocketTransport) -> Result<()> {
        let poll = Duration::from_millis(100);
        while self.run_accept_iteration(&listener, poll)? {}
        Ok(())
    }

    /// Process one request from a socket stream. Returns false if the loop should stop.
    fn handle_one_socket_request(&mut self, stream: &mut McpStream) -> Result<bool> {
        match stream.read_request()? {
            None => Ok(false),
            Some(req) => {
                if let Some(resp) = self.process_request(req) {
                    stream.write_response(&resp)?;
                }
                Ok(true)
            }
        }
    }

    /// Handle MCP requests from a socket stream.
    fn handle_socket_stream(&mut self, stream: &mut McpStream) -> Result<()> {
        loop {
            if self.is_shutting_down() {
                break;
            }
            if !self.handle_one_socket_request(stream)? {
                break;
            }
        }
        Ok(())
    }

    /// Process one request from a stdio transport. Returns false if the loop should stop.
    fn handle_one_stdio_request<R, W>(
        &mut self,
        transport: &mut StdioTransport<R, W>,
    ) -> Result<bool>
    where
        R: std::io::BufRead,
        W: std::io::Write,
    {
        match transport.read_request()? {
            None => Ok(false),
            Some(req) => {
                if let Some(resp) = self.process_request(req) {
                    transport.write_response(&resp)?;
                }
                Ok(true)
            }
        }
    }

    /// Run the server with a custom transport.
    pub fn run_loop<R, W>(&mut self, transport: &mut StdioTransport<R, W>) -> Result<()>
    where
        R: std::io::BufRead,
        W: std::io::Write,
    {
        loop {
            if self.is_shutting_down() {
                break;
            }
            if !self.handle_one_stdio_request(transport)? {
                break;
            }
        }
        Ok(())
    }

    /// Build a success JSON-RPC response with the given result value.
    fn ok_response(id: Value, result: Value) -> JsonRpcResponse {
        JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            result: Some(result),
            error: None,
            id,
        }
    }

    /// Build a static JSON-RPC response for simple no-op methods (ping, list-like).
    fn static_response(id: Value, method: &str) -> JsonRpcResponse {
        let result = match method {
            "ping" => serde_json::json!({}),
            "resources/list" => serde_json::json!({"resources": []}),
            "prompts/list" => serde_json::json!({"prompts": []}),
            "completion/complete" => {
                serde_json::json!({"completion": {"values": [], "hasMore": false}})
            }
            other => {
                return JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    result: None,
                    error: Some(types::JsonRpcError::method_not_found(other)),
                    id,
                }
            }
        };
        Self::ok_response(id, result)
    }

    /// Handle the notification method, updating state if needed.
    fn handle_notification(&mut self, method: &str) {
        if method == "notifications/initialized" {
            self.state = ServerState::Ready;
        }
    }

    /// Process a single JSON-RPC request.
    ///
    /// Returns `Some(response)` for requests that require a response, or `None` for
    /// JSON-RPC notifications (which must not receive a response per the spec).
    fn process_request(&mut self, request: JsonRpcRequest) -> Option<JsonRpcResponse> {
        let id = request.id.unwrap_or(Value::Null);
        if request.method.starts_with("notifications/") {
            self.handle_notification(&request.method);
            return None;
        }
        match request.method.as_str() {
            "initialize" => Some(Self::ok_response(
                id,
                self.handle_initialize(request.params),
            )),
            "tools/list" => Some(Self::ok_response(id, self.handle_tools_list())),
            "tools/call" => {
                // Capability denials must be JSON-RPC errors per RFC-009 policy enforcement,
                // not tool-level isError responses. This ensures consumers receive a clear
                // protocol-level denial rather than an ambiguous success-with-error-flag.
                Some(self.handle_tools_call_with_denial_as_rpc_error(id, request.params))
            }
            other => Some(Self::static_response(id, other)),
        }
    }

    /// Handle tools/call, returning JSON-RPC error for capability denials.
    ///
    /// Returns a JSON-RPC response: either an error response (for capability denials
    /// or protocol errors) or a success response wrapped in ok_response format.
    fn handle_tools_call_with_denial_as_rpc_error(
        &mut self,
        id: Value,
        params: Option<Value>,
    ) -> JsonRpcResponse {
        let state_check = Self::check_server_state(&self.state, &id);
        if let Some(response) = state_check {
            return response;
        }
        let parse_result = Self::parse_tool_params_simple(params);
        let (tool_name, arguments) = match parse_result {
            Ok(p) => p,
            Err((id, e)) => return Self::json_rpc_error(id, e),
        };
        let result = self.execute_tool(&tool_name, arguments);
        Self::format_tool_result_response(id, result)
    }

    /// Format tool execution result as a JSON-RPC response (policy helper).
    ///
    /// Returns JSON-RPC error for tool execution failures, not a success response with
    /// isError:true. This ensures consumers receive clear protocol-level errors rather
    /// than ambiguous tool-level error flags.
    fn format_tool_result_response(
        id: Value,
        result: Result<ToolResult, ToolError>,
    ) -> JsonRpcResponse {
        match result {
            Ok(tool_result) => Self::ok_response(
                id,
                serde_json::to_value(tool_result)
                    .unwrap_or_else(|_| serde_json::json!({"content": [], "isError": true})),
            ),
            Err(e) => Self::json_rpc_error(id, types::JsonRpcError::tool_error(&e.to_string())),
        }
    }

    /// Check server state and return error response if not ready.
    fn check_server_state(state: &ServerState, id: &Value) -> Option<JsonRpcResponse> {
        if matches!(state, ServerState::Ready) {
            None
        } else {
            Some(Self::json_rpc_error(
                id.clone(),
                types::JsonRpcError::internal_error("Server not initialized"),
            ))
        }
    }

    /// Parse tool params, returning error as part of result for ? operator compatibility.
    fn parse_tool_params_simple(
        params: Option<Value>,
    ) -> Result<(String, Value), (Value, types::JsonRpcError)> {
        match Self::parse_tool_call_params(params) {
            Ok(p) => Ok(p),
            Err(e) => Err((Value::Null, Self::parse_error_to_json_rpc_error(e))),
        }
    }

    /// Convert a parse error to a JSON-RPC error.
    fn parse_error_to_json_rpc_error(err: serde_json::Value) -> types::JsonRpcError {
        let code = err.get("code").and_then(|v| v.as_i64()).unwrap_or(-32600) as i32;
        let message = err
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("Invalid request")
            .to_string();
        types::JsonRpcError {
            code,
            message,
            data: None,
        }
    }

    /// Build a JSON-RPC error response.
    fn json_rpc_error(id: Value, error: types::JsonRpcError) -> JsonRpcResponse {
        JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            result: None,
            error: Some(error),
            id,
        }
    }

    /// Handle the initialize request.
    fn handle_initialize(&mut self, params: Option<Value>) -> Value {
        // Parse client params if provided
        let _client_params: InitializeParams = params
            .and_then(|v| serde_json::from_value(v).ok())
            .unwrap_or_default();

        // Mark as ready
        self.state = ServerState::Ready;

        // Return server info
        let result = InitializeResult::new();
        serde_json::to_value(result).unwrap_or_else(|_| serde_json::json!({}))
    }

    /// Handle the tools/list request.
    fn handle_tools_list(&self) -> Value {
        let tools = self.registry.list_tools();
        serde_json::json!({
            "tools": tools
        })
    }

    /// Serialize a protocol-level error (not a tool error) as a ToolResult with isError:true.
    fn tool_call_protocol_error(message: &str) -> Value {
        let error_result = ToolResult {
            content: vec![ToolContent::text(message)],
            is_error: Some(true),
        };
        serde_json::to_value(error_result)
            .unwrap_or_else(|_| serde_json::json!({"content": [], "isError": true}))
    }

    /// Parse tool name and arguments from a tools/call parameter object.
    ///
    /// Returns `Ok((tool_name, arguments))` or an error `Value` for the protocol-level error.
    fn parse_tool_call_params(params: Option<Value>) -> Result<(String, Value), Value> {
        let obj = match params {
            Some(Value::Object(obj)) => obj,
            Some(_) => {
                return Err(Self::tool_call_protocol_error(
                    "Invalid parameters: expected object",
                ))
            }
            None => return Err(Self::tool_call_protocol_error("Missing parameters")),
        };
        let name = obj
            .get("name")
            .and_then(|v| v.as_str())
            .map(|n| n.to_string())
            .ok_or_else(|| Self::tool_call_protocol_error("Missing 'name' parameter"))?;
        Ok((name, obj.get("arguments").cloned().unwrap_or_default()))
    }

    /// Append an audit record to the trail and optionally send it on the channel.
    fn append_audit_record(&mut self, record: AuditRecord) {
        if let Some(ref sender) = self.audit_sender {
            let _ = sender.send(record.clone());
        }
        let new_records: Vec<_> = self
            .audit_trail
            .records()
            .iter()
            .cloned()
            .chain(std::iter::once(record))
            .collect();
        self.audit_trail = Arc::new(AuditTrail::from_records(new_records));
    }

    /// Record capability checks for all required capabilities of the tool.
    fn record_capability_checks(&mut self, tool_name: &str, caps: &[Capability], ts: u64) {
        for cap in caps {
            let outcome = self.session.check_capability(*cap);
            let description = format!(
                "MCP tool call '{}' - capability check: {} = {:?}",
                tool_name,
                cap.identifier(),
                outcome
            );
            let record = AuditRecord::new(
                self.session.session_id.clone(),
                ts,
                *cap,
                outcome,
                description,
            );
            self.append_audit_record(record);
        }
    }

    /// Record a successful tool execution in the audit trail.
    fn record_execution_success(&mut self, tool_name: &str, ts: u64) {
        let record = AuditRecord::new(
            self.session.session_id.clone(),
            ts,
            Capability::ArtifactSubmit,
            PolicyOutcome::Approved,
            format!("MCP tool call '{}' executed successfully", tool_name),
        );
        self.append_audit_record(record);
    }

    /// Execute a tool and record the audit entry.
    fn execute_tool(&mut self, tool_name: &str, arguments: Value) -> Result<ToolResult, ToolError> {
        let required_caps: Vec<Capability> = self
            .registry
            .get_tool(tool_name)
            .ok_or_else(|| ToolError::NotFound(tool_name.to_string()))?
            .required_capabilities
            .clone();
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        self.record_capability_checks(tool_name, &required_caps, timestamp);
        let result =
            self.registry
                .execute(&self.session, self.workspace.as_ref(), tool_name, arguments)?;
        self.record_execution_success(tool_name, timestamp);
        Ok(result)
    }

    /// Gracefully shut down the server.
    pub fn shutdown(&mut self) {
        self.state = ServerState::Shutdown;
    }
}
