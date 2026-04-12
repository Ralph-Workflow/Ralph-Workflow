//! Session bridge for MCP server lifecycle management.
//!
//! This module bridges Ralph's `AgentSession` and `Workspace` to the MCP server,
//! creating endpoint configuration for MCP communication and managing server lifecycle.
//!
//! # Architecture
//!
//! The session bridge creates endpoint configuration for MCP communication.
//! When wired into the agent spawn flow, this configuration is passed to the
//! agent process via environment variables.
//!
//! # Endpoint Management
//!
//! The session bridge creates a TCP loopback endpoint for each session.
//! The endpoint path is passed to the agent via the `RALPH_MCP_ENDPOINT` environment variable.
//!
//! The MCP server runs in a background thread and listens on the loopback TCP endpoint for
//! agent connections.

use crate::agents::session::{AgentSession, AuditRecord as RalphAuditRecord, AuditTrail};
use crate::agents::tool_manifest::visible_mcp_tool_names_owned;
use crate::mcp_server::capability_mapping::{
    drain_class_for_session, drain_to_access_mode, drain_to_policy_mode,
};
use crate::mcp_server::tool_bridge::{
    build_ralph_tool_registry, RalphAuditSinkAdapter, RalphHostSessionAdapter,
    RalphWorkspaceAdapter,
};
use crate::workspace::Workspace;
use mcp_server::dispatch::access::ToolFilter;
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::{ControlCommand, ControlError};
use mcp_server::io::{EndpointLease, McpServer, ServerState, SessionBridge as McpSessionBridge};
use mcp_server::protocol::{JsonRpcRequest, JsonRpcResponse};
use serde_json::json;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;
use thiserror::Error;

pub const MCP_ENDPOINT_ENV: &str = "RALPH_MCP_ENDPOINT";
pub const MCP_GENERATION_ENV: &str = "RALPH_MCP_GENERATION";
pub const MCP_RUN_ID_ENV: &str = "RALPH_MCP_RUN_ID";

/// Errors that can occur during session bridge operations.
#[derive(Error, Debug)]
pub enum SessionBridgeError {
    #[error("Transport error: {0}")]
    Transport(String),

    #[error("Server error: {0}")]
    Server(String),

    #[error("Session already started")]
    AlreadyStarted,

    #[error("Bridge not started")]
    NotStarted,
}

/// Session bridge that manages MCP server lifecycle for an agent session.
///
/// The bridge creates an MCP server bound to the session's capabilities,
/// provides endpoint configuration for agent connections, and manages
/// the server lifecycle.
pub struct SessionBridge {
    /// The agent session this bridge is bound to.
    session: Arc<AgentSession>,
    /// The workspace this bridge is bound to.
    workspace: Arc<dyn Workspace>,
    /// Inner session bridge from the mcp-server crate.
    inner: McpSessionBridge,
    /// Audit sink adapter that accumulates MCP audit records.
    audit_adapter: Arc<RalphAuditSinkAdapter>,
    /// Cached view of audit records for backward-compatible API.
    cached_audit: AuditTrail,
    /// Remote HTTP endpoint exposed to agent clients.
    http_endpoint: Option<String>,
    /// Shutdown signal for in-process HTTP gateway.
    http_shutdown: Arc<AtomicBool>,
    /// Join handle for HTTP gateway thread.
    http_thread: Option<thread::JoinHandle<()>>,
}

impl SessionBridge {
    /// Create a new session bridge for the given session and workspace.
    ///
    /// This creates an MCP server bound to the session but does not start it.
    /// Call `start()` to begin listening for agent connections.
    pub fn new(session: AgentSession, workspace: Arc<dyn Workspace>) -> Self {
        let session_arc = Arc::new(session);
        let workspace_arc = workspace.clone();

        // Build the tool registry with Ralph tool handlers
        let registry =
            build_ralph_tool_registry(Arc::clone(&session_arc), Arc::clone(&workspace_arc));

        // Create adapters
        let host = Arc::new(RalphHostSessionAdapter::new(Arc::clone(&session_arc)));
        let ws = Arc::new(RalphWorkspaceAdapter::new(Arc::clone(&workspace_arc)));

        // Create the audit sink adapter for MCP audit records
        let audit_adapter = Arc::new(RalphAuditSinkAdapter::new());

        // Derive the access mode from the session's drain — per RFC-009:
        // Planning/Analysis/Review/Fix → ReadOnly; Development/Commit → ReadWrite.
        let access_mode = drain_to_access_mode(session_arc.drain);
        let visible_tools = visible_mcp_tool_names_owned(session_arc.capabilities());
        let generation = next_generation_for_run(workspace.root(), session_arc.run_id.as_str());

        // Create config with workspace root and session ID for audit correlation
        let policy_mode = drain_to_policy_mode(session_arc.drain);
        let config = McpServerConfig::new(workspace.root().to_path_buf())
            .with_session_id(session_arc.session_id.as_str().to_string())
            .with_access_mode(access_mode)
            .with_policy_mode(policy_mode)
            .with_drain(session_arc.drain.as_str().to_string())
            .with_drain_class(drain_class_for_session(session_arc.drain))
            .with_tool_filter(ToolFilter::Allowlist(visible_tools))
            .with_run_id(session_arc.run_id.clone())
            .with_generation(generation);

        // Create the inner session bridge (audit sink passed at start() time)
        let inner = McpSessionBridge::new(host, config, ws, registry);

        Self {
            session: session_arc,
            workspace: workspace_arc,
            inner,
            audit_adapter,
            cached_audit: AuditTrail::new(),
            http_endpoint: None,
            http_shutdown: Arc::new(AtomicBool::new(false)),
            http_thread: None,
        }
    }

    /// Get the session this bridge is bound to.
    pub fn session(&self) -> &AgentSession {
        &self.session
    }

    /// Get the audit trail view (combining cached and fresh records).
    ///
    /// Note: This returns all accumulated records including those since the last
    /// `drain_audit_records()` call.
    pub fn audit_trail(&mut self) -> AuditTrail {
        let new_records = self.audit_adapter.drain_records();
        if !new_records.is_empty() {
            self.cached_audit = AuditTrail::from_records(
                self.cached_audit
                    .records()
                    .iter()
                    .cloned()
                    .chain(new_records.iter().cloned()),
            );
        }

        self.cached_audit.clone()
    }

    /// Drain all audit records accumulated since the last drain.
    pub fn drain_audit_records(&mut self) -> Vec<RalphAuditRecord> {
        let new_records = self.audit_adapter.drain_records();
        if !new_records.is_empty() {
            self.cached_audit = AuditTrail::from_records(
                self.cached_audit
                    .records()
                    .iter()
                    .cloned()
                    .chain(new_records.iter().cloned()),
            );
        }
        new_records
    }

    /// Get the MCP endpoint URI for passing to agents.
    ///
    /// Returns a URI like `tcp://127.0.0.1:12345` that agents can use
    /// to connect to the MCP server.
    pub fn endpoint_uri(&self) -> String {
        self.inner.endpoint_uri()
    }

    /// Get the endpoint URI exposed to agents.
    ///
    /// Uses the in-process HTTP gateway when available.
    pub fn agent_endpoint_uri(&self) -> String {
        self.http_endpoint
            .clone()
            .unwrap_or_else(|| self.inner.endpoint_uri())
    }

    /// Get the latest endpoint lease published by the MCP server.
    pub fn endpoint_lease(&self) -> Option<EndpointLease> {
        self.inner.endpoint_lease()
    }

    /// Get the environment variable name for the MCP endpoint.
    pub fn endpoint_env_var(&self) -> &'static str {
        MCP_ENDPOINT_ENV
    }

    /// Check if the bridge has been started.
    pub fn is_started(&self) -> bool {
        self.inner.is_started()
    }

    /// Check if the bridge has been shutdown.
    pub fn is_shutdown(&self) -> bool {
        self.inner.is_shutdown()
    }

    /// Start the session bridge and MCP server.
    ///
    /// This spawns a background thread that binds the TCP loopback endpoint and runs the MCP server.
    /// The thread signals readiness after the endpoint is bound, so callers can connect
    /// immediately after start() returns without timing races.
    pub fn start(&mut self) -> Result<(), SessionBridgeError> {
        self.inner
            .start_with_audit_sink(self.audit_adapter.clone())
            .map_err(|e| SessionBridgeError::Transport(e.to_string()))?;
        self.start_http_gateway()?;
        Ok(())
    }

    /// Shutdown the session bridge gracefully.
    ///
    /// This signals the MCP server to shutdown and waits for the server thread to finish.
    pub fn shutdown(&mut self) {
        self.http_shutdown.store(true, Ordering::SeqCst);
        if let Some(handle) = self.http_thread.take() {
            let _ = handle.join();
        }
        self.inner.shutdown();
    }

    fn start_http_gateway(&mut self) -> Result<(), SessionBridgeError> {
        self.http_shutdown.store(false, Ordering::SeqCst);
        let listener = TcpListener::bind(("127.0.0.1", 0))
            .map_err(|e| SessionBridgeError::Transport(e.to_string()))?;
        listener
            .set_nonblocking(true)
            .map_err(|e| SessionBridgeError::Transport(e.to_string()))?;
        let addr = listener
            .local_addr()
            .map_err(|e| SessionBridgeError::Transport(e.to_string()))?;
        self.http_endpoint = Some(format!("http://{addr}/mcp"));

        let shutdown = Arc::clone(&self.http_shutdown);
        let session = Arc::clone(&self.session);
        let workspace = Arc::clone(&self.workspace);
        let audit = Arc::clone(&self.audit_adapter);
        self.http_thread = Some(thread::spawn(move || {
            run_http_gateway(listener, shutdown, session, workspace, audit)
        }));
        Ok(())
    }

    /// Send a private control command through the orchestrator-only control channel.
    ///
    /// This path never traverses MCP tool dispatch and always includes the per-run
    /// 256-character policy challenge held in bridge memory.
    pub fn send_private_control_command(
        &self,
        command: ControlCommand,
    ) -> Result<(), ControlError> {
        self.inner.send_control_command(command)
    }

    fn build_in_process_server(&self) -> McpServer {
        let registry =
            build_ralph_tool_registry(Arc::clone(&self.session), Arc::clone(&self.workspace));
        let host = Arc::new(RalphHostSessionAdapter::new(Arc::clone(&self.session)));
        let ws = Arc::new(RalphWorkspaceAdapter::new(Arc::clone(&self.workspace)));
        let access_mode = drain_to_access_mode(self.session.drain);
        let drain_class = drain_class_for_session(self.session.drain);
        let visible_tools = visible_mcp_tool_names_owned(self.session.capabilities());
        let policy_mode = drain_to_policy_mode(self.session.drain);
        let generation =
            next_generation_for_run(self.workspace.root(), self.session.run_id.as_str());
        let config = McpServerConfig::new(self.workspace.root().to_path_buf())
            .with_session_id(self.session.session_id.as_str().to_string())
            .with_access_mode(access_mode)
            .with_policy_mode(policy_mode)
            .with_drain(self.session.drain.as_str().to_string())
            .with_drain_class(drain_class)
            .with_tool_filter(ToolFilter::Allowlist(visible_tools))
            .with_run_id(self.session.run_id.clone())
            .with_generation(generation);

        McpServer::new(host, config, ws, registry, Some(self.audit_adapter.clone()))
    }

    /// Handle a JSON-RPC request directly without transport I/O.
    ///
    /// This deterministic seam is primarily for tests that need protocol behavior
    /// without relying on OS socket support.
    pub fn handle_request_in_process(
        &self,
        request: JsonRpcRequest,
        state: ServerState,
    ) -> (Option<JsonRpcResponse>, ServerState) {
        self.build_in_process_server()
            .handle_request(request, state)
    }
}

fn endpoint_lease_path(workspace_root: &Path) -> PathBuf {
    workspace_root.join(".agent").join("endpoint_lease.json")
}

fn next_generation_for_run(workspace_root: &Path, run_id: &str) -> u32 {
    let lease_path = endpoint_lease_path(workspace_root);
    let Some(content) = std::fs::read_to_string(lease_path).ok() else {
        return 1;
    };
    let Some(lease) = serde_json::from_str::<EndpointLease>(&content).ok() else {
        return 1;
    };
    if lease.run_id == run_id {
        lease.generation.saturating_add(1)
    } else {
        1
    }
}

impl Clone for SessionBridge {
    fn clone(&self) -> Self {
        Self {
            session: Arc::clone(&self.session),
            workspace: Arc::clone(&self.workspace),
            inner: self.inner.clone(),
            audit_adapter: Arc::clone(&self.audit_adapter),
            cached_audit: self.cached_audit.clone(),
            http_endpoint: self.http_endpoint.clone(),
            http_shutdown: Arc::clone(&self.http_shutdown),
            http_thread: None,
        }
    }
}

fn run_http_gateway(
    listener: TcpListener,
    shutdown: Arc<AtomicBool>,
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
    audit_adapter: Arc<RalphAuditSinkAdapter>,
) {
    let server = build_in_process_server(session, workspace, audit_adapter);
    let mut state = ServerState::Uninitialized;

    while !shutdown.load(Ordering::SeqCst) {
        match listener.accept() {
            Ok((mut stream, _addr)) => {
                let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
                let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
                handle_http_connection(&server, &mut state, &mut stream);
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(20));
            }
            Err(_) => break,
        }
    }
}

fn handle_http_connection(
    server: &McpServer,
    state: &mut ServerState,
    stream: &mut std::net::TcpStream,
) {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 1024];
    let mut header_end = None;

    while header_end.is_none() {
        match stream.read(&mut chunk) {
            Ok(0) => return,
            Ok(n) => {
                buffer.extend_from_slice(&chunk[..n]);
                header_end = find_header_end(&buffer);
            }
            Err(_) => return,
        }
    }

    let header_end = header_end.unwrap_or(0);
    let headers_raw = String::from_utf8_lossy(&buffer[..header_end]);
    let mut lines = headers_raw.lines();
    let request_line = lines.next().unwrap_or_default().to_string();
    let content_length = lines
        .filter_map(|line| line.split_once(':'))
        .find_map(|(name, value)| {
            if name.trim().eq_ignore_ascii_case("content-length") {
                value.trim().parse::<usize>().ok()
            } else {
                None
            }
        })
        .unwrap_or(0);

    let mut body = buffer[(header_end + 4)..].to_vec();
    while body.len() < content_length {
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => body.extend_from_slice(&chunk[..n]),
            Err(_) => break,
        }
    }

    if !request_line.starts_with("POST /mcp ") {
        let _ = write_http_response(stream, 404, "application/json", "{}".as_bytes());
        return;
    }

    let request = match serde_json::from_slice::<JsonRpcRequest>(&body) {
        Ok(req) => req,
        Err(_) => {
            let payload = json!({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": serde_json::Value::Null
            });
            let bytes = serde_json::to_vec(&payload).unwrap_or_else(|_| b"{}".to_vec());
            let _ = write_http_response(stream, 400, "application/json", &bytes);
            return;
        }
    };

    let (response, next_state) = server.handle_request(request, *state);
    *state = next_state;

    if let Some(resp) = response {
        let bytes = serde_json::to_vec(&resp).unwrap_or_else(|_| b"{}".to_vec());
        let _ = write_http_response(stream, 200, "application/json", &bytes);
    } else {
        let _ = write_http_response(stream, 204, "application/json", b"");
    }
}

fn write_http_response(
    stream: &mut std::net::TcpStream,
    status: u16,
    content_type: &str,
    body: &[u8],
) -> std::io::Result<()> {
    let status_text = match status {
        200 => "OK",
        204 => "No Content",
        400 => "Bad Request",
        404 => "Not Found",
        _ => "OK",
    };
    let headers = format!(
        "HTTP/1.1 {status} {status_text}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream.write_all(headers.as_bytes())?;
    stream.write_all(body)?;
    stream.flush()
}

fn find_header_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|window| window == b"\r\n\r\n")
}

fn build_in_process_server(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
    audit_adapter: Arc<RalphAuditSinkAdapter>,
) -> McpServer {
    let registry = build_ralph_tool_registry(Arc::clone(&session), Arc::clone(&workspace));
    let host = Arc::new(RalphHostSessionAdapter::new(Arc::clone(&session)));
    let ws = Arc::new(RalphWorkspaceAdapter::new(Arc::clone(&workspace)));
    let access_mode = drain_to_access_mode(session.drain);
    let drain_class = drain_class_for_session(session.drain);
    let visible_tools = visible_mcp_tool_names_owned(session.capabilities());
    let policy_mode = drain_to_policy_mode(session.drain);
    let generation = next_generation_for_run(workspace.root(), session.run_id.as_str());
    let config = McpServerConfig::new(workspace.root().to_path_buf())
        .with_session_id(session.session_id.as_str().to_string())
        .with_access_mode(access_mode)
        .with_policy_mode(policy_mode)
        .with_drain(session.drain.as_str().to_string())
        .with_drain_class(drain_class)
        .with_tool_filter(ToolFilter::Allowlist(visible_tools))
        .with_run_id(session.run_id.clone())
        .with_generation(generation);

    McpServer::new(host, config, ws, registry, Some(audit_adapter))
}

impl Drop for SessionBridge {
    fn drop(&mut self) {
        if self.is_started() {
            self.shutdown();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::memory_workspace::MemoryWorkspace;
    use crate::workspace::WorkspaceFs;
    use mcp_server::dispatch::access::AuditSink;
    use mcp_server::dispatch::access::{AccessDecision, McpCapability};
    use mcp_server::dispatch::audit::AuditRecord as McpAuditRecord;
    use tempfile::tempdir;

    fn unique_session() -> AgentSession {
        AgentSession::for_drain(
            "test-session".to_string(),
            crate::agents::session::SessionDrain::Development,
            1,
        )
    }

    fn test_workspace() -> Arc<dyn Workspace> {
        Arc::new(MemoryWorkspace::new_test())
    }

    fn socket_workspace() -> Arc<dyn Workspace> {
        Arc::new(WorkspaceFs::new(std::path::PathBuf::from("/")))
    }

    #[test]
    fn test_endpoint_uri() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        bridge.start().expect("bridge should start");
        let uri = bridge.endpoint_uri();
        assert!(uri.starts_with("tcp://127.0.0.1:"));
    }

    #[test]
    fn test_agent_endpoint_uri_is_http_gateway() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        bridge.start().expect("bridge should start");
        let uri = bridge.agent_endpoint_uri();
        assert!(
            uri.starts_with("http://127.0.0.1:") && uri.ends_with("/mcp"),
            "agent endpoint should expose in-process HTTP gateway, got: {uri}"
        );
    }

    #[test]
    fn test_http_gateway_handles_initialize_request() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        bridge.start().expect("bridge should start");
        let uri = bridge.agent_endpoint_uri();
        let address = uri
            .strip_prefix("http://")
            .expect("http prefix")
            .strip_suffix("/mcp")
            .expect("/mcp suffix");

        let request_body = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        })
        .to_string();
        let request = format!(
            "POST /mcp HTTP/1.1\r\nHost: {address}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            request_body.len(),
            request_body
        );
        let mut response = String::new();
        for _ in 0..10 {
            let mut stream = std::net::TcpStream::connect(address).expect("connect http gateway");
            std::io::Write::write_all(&mut stream, request.as_bytes()).expect("write request");
            std::io::Write::flush(&mut stream).expect("flush request");

            let mut response_bytes = Vec::new();
            let mut buf = [0u8; 1024];
            loop {
                match std::io::Read::read(&mut stream, &mut buf) {
                    Ok(0) => break,
                    Ok(n) => response_bytes.extend_from_slice(&buf[..n]),
                    Err(e) if e.kind() == std::io::ErrorKind::ConnectionReset => break,
                    Err(e) => panic!("read response: {e}"),
                }
            }

            response = String::from_utf8_lossy(&response_bytes).to_string();
            if !response.is_empty() {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(
            response.starts_with("HTTP/1.1 200"),
            "unexpected response: {response}"
        );
        assert!(response.contains("\"result\""));
    }

    #[test]
    fn test_start_publishes_tcp_endpoint_lease() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        bridge.start().expect("bridge should start");

        let lease = bridge
            .endpoint_lease()
            .expect("start must publish an active endpoint lease");
        assert!(
            lease.endpoint.starts_with("tcp://127.0.0.1:"),
            "lease endpoint must be TCP loopback, got: {}",
            lease.endpoint
        );
    }

    #[test]
    fn next_generation_for_run_defaults_to_one_without_lease() {
        let dir = tempdir().expect("tempdir");
        let generation = next_generation_for_run(dir.path(), "run-a");
        assert_eq!(generation, 1);
    }

    #[test]
    fn next_generation_for_run_increments_when_run_id_matches() {
        let dir = tempdir().expect("tempdir");
        let lease = EndpointLease {
            endpoint: "tcp://127.0.0.1:9900".to_string(),
            run_id: "run-b".to_string(),
            generation: 7,
            ready_at: 1_700_000_700,
        };
        let lease_path = endpoint_lease_path(dir.path());
        std::fs::create_dir_all(lease_path.parent().expect("lease path parent"))
            .expect("create lease dir");
        let lease_json = serde_json::to_string(&lease).expect("serialize lease");
        std::fs::write(&lease_path, lease_json).expect("write lease file");

        let generation = next_generation_for_run(dir.path(), "run-b");
        assert_eq!(generation, 8);
    }

    #[test]
    fn next_generation_for_run_resets_when_run_id_differs() {
        let dir = tempdir().expect("tempdir");
        let lease = EndpointLease {
            endpoint: "tcp://127.0.0.1:9901".to_string(),
            run_id: "run-c".to_string(),
            generation: 9,
            ready_at: 1_700_000_701,
        };
        let lease_path = endpoint_lease_path(dir.path());
        std::fs::create_dir_all(lease_path.parent().expect("lease path parent"))
            .expect("create lease dir");
        let lease_json = serde_json::to_string(&lease).expect("serialize lease");
        std::fs::write(&lease_path, lease_json).expect("write lease file");

        let generation = next_generation_for_run(dir.path(), "different-run");
        assert_eq!(generation, 1);
    }

    #[test]
    fn test_endpoint_env_var() {
        assert_eq!(MCP_ENDPOINT_ENV, "RALPH_MCP_ENDPOINT");
    }

    #[test]
    fn test_bridge_initial_state() {
        let bridge = SessionBridge::new(unique_session(), test_workspace());
        assert!(!bridge.is_started());
        assert!(!bridge.is_shutdown());
    }

    #[test]
    fn test_bridge_start() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        let result = bridge.start();
        assert!(result.is_ok());
        assert!(bridge.is_started());
    }

    #[test]
    fn test_bridge_shutdown() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        bridge.start().expect("start should succeed");
        bridge.shutdown();
        assert!(bridge.is_shutdown());
    }

    #[test]
    fn test_audit_trail_access() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        let trail = bridge.audit_trail();
        assert!(trail.is_empty());
    }

    #[test]
    fn test_drain_audit_records() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        // Initially empty
        let records = bridge.drain_audit_records();
        assert!(records.is_empty());
    }

    #[test]
    fn test_drain_audit_records_returns_only_new_records() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());

        bridge.audit_adapter.emit(
            McpAuditRecord::new(
                "test-session".to_string(),
                "read_file".to_string(),
                AccessDecision::Allow,
            )
            .with_capability(McpCapability::WorkspaceRead),
        );

        let first_drain = bridge.drain_audit_records();
        assert_eq!(first_drain.len(), 1);

        let second_drain = bridge.drain_audit_records();
        assert!(
            second_drain.is_empty(),
            "second drain should only return new records"
        );
    }

    #[test]
    fn test_audit_trail_repeated_calls_keep_records() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());

        bridge.audit_adapter.emit(
            McpAuditRecord::new(
                "test-session".to_string(),
                "read_file".to_string(),
                AccessDecision::Allow,
            )
            .with_capability(McpCapability::WorkspaceRead),
        );

        let first = bridge.audit_trail();
        assert_eq!(first.len(), 1);

        let second = bridge.audit_trail();
        assert_eq!(
            second.len(),
            1,
            "repeated audit_trail calls should not lose previously drained records"
        );
    }

    #[test]
    fn test_clone_preserves_cached_audit_snapshot() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());

        bridge.audit_adapter.emit(
            McpAuditRecord::new(
                "test-session".to_string(),
                "read_file".to_string(),
                AccessDecision::Allow,
            )
            .with_capability(McpCapability::WorkspaceRead),
        );

        let initial = bridge.audit_trail();
        assert_eq!(initial.len(), 1);

        let mut cloned = bridge.clone();
        let cloned_trail = cloned.audit_trail();
        assert_eq!(
            cloned_trail.len(),
            1,
            "clone should preserve cached audit records for repeated reads"
        );
    }

    /// Verify that `ralph_submit_artifact` is reachable via the MCP protocol.
    ///
    /// This test exercises the full initialize → tools/list flow over the deterministic
    /// transport to confirm that `ralph_submit_artifact` appears in the tool list returned
    /// by the server, proving the tool registry is wired correctly end-to-end.
    #[test]
    fn test_tools_list_includes_ralph_submit_artifact() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        bridge.start().expect("bridge should start");

        // Step 1: initialize handshake.
        let init_req = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        });
        let init_req: JsonRpcRequest =
            serde_json::from_value(init_req).expect("initialize request is valid JSON-RPC");
        let (init_resp, state) =
            bridge.handle_request_in_process(init_req, ServerState::Uninitialized);
        let init_resp =
            serde_json::to_value(init_resp.expect("initialize response")).expect("serialize init");
        assert!(
            init_resp["result"].is_object(),
            "initialize must return a result object, got: {init_resp}"
        );

        // Step 2: tools/list — assert ralph_submit_artifact is present.
        let list_req = serde_json::json!({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2
        });
        let list_req: JsonRpcRequest =
            serde_json::from_value(list_req).expect("tools/list request is valid JSON-RPC");
        let (list_resp, _) = bridge.handle_request_in_process(list_req, state);
        let list_resp = serde_json::to_value(list_resp.expect("tools/list response"))
            .expect("serialize tools/list");
        assert!(
            list_resp["result"].is_object(),
            "tools/list must return a result object, got: {list_resp}"
        );
        let tools = list_resp["result"]["tools"]
            .as_array()
            .expect("tools/list result must contain a 'tools' array");
        let tool_names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
        assert!(
            tool_names.contains(&"ralph_submit_artifact"),
            "tools/list must include 'ralph_submit_artifact'; registered tools: {tool_names:?}"
        );
    }

    #[test]
    fn test_tools_list_matches_session_manifest_for_all_drains() {
        use crate::agents::tool_manifest::visible_mcp_tool_names;
        use std::collections::BTreeSet;

        for drain in [
            crate::agents::session::SessionDrain::Planning,
            crate::agents::session::SessionDrain::Analysis,
            crate::agents::session::SessionDrain::Review,
            crate::agents::session::SessionDrain::Development,
            crate::agents::session::SessionDrain::Fix,
            crate::agents::session::SessionDrain::Commit,
        ] {
            let session = crate::agents::session::AgentSession::for_drain(
                format!("manifest-{drain:?}"),
                drain,
                0,
            );
            let expected = visible_mcp_tool_names(session.capabilities());

            let mut bridge = SessionBridge::new(session, test_workspace());
            bridge.start().expect("bridge should start");

            let init_req: JsonRpcRequest = serde_json::from_value(serde_json::json!({
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
                "id": 1
            }))
            .expect("initialize request is valid JSON-RPC");
            let (_, state) = bridge.handle_request_in_process(init_req, ServerState::Uninitialized);

            let list_req: JsonRpcRequest = serde_json::from_value(serde_json::json!({
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": 2
            }))
            .expect("tools/list request is valid JSON-RPC");
            let (list_resp, _) = bridge.handle_request_in_process(list_req, state);
            let list_resp = serde_json::to_value(list_resp.expect("tools/list response"))
                .expect("serialize tools/list");
            let tools = list_resp["result"]["tools"]
                .as_array()
                .expect("tools/list result must contain a 'tools' array");
            let actual: BTreeSet<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
            let expected: BTreeSet<&str> = expected.into_iter().collect();

            assert_eq!(
                actual, expected,
                "tools/list must match the session manifest for {:?}",
                drain
            );
        }
    }

    #[test]
    fn test_started_bridge_accepts_raw_tcp_connection() {
        use std::net::TcpStream;

        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        bridge.start().expect("bridge should start");

        let uri = bridge.endpoint_uri();
        if let Some(addr) = uri.strip_prefix("tcp://") {
            let connect_result = TcpStream::connect(addr.to_string());
            assert!(
                connect_result.is_ok(),
                "started bridge must accept raw TCP connections, got: {connect_result:?}"
            );
        }
    }

    #[test]
    fn test_orchestrator_private_control_heartbeat_ack_succeeds() {
        use mcp_server::io::ControlCommand;

        let mut bridge = SessionBridge::new(unique_session(), socket_workspace());
        bridge.start().expect("bridge should start");

        let result = bridge.send_private_control_command(ControlCommand::HeartbeatAck);
        assert!(
            result.is_ok(),
            "orchestrator private control command should succeed over non-MCP channel"
        );
    }

    #[test]
    fn test_private_control_method_is_denied_from_mcp_namespace() {
        let mut bridge = SessionBridge::new(unique_session(), socket_workspace());
        bridge.start().expect("bridge should start");

        let init_req: JsonRpcRequest = serde_json::from_value(serde_json::json!({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
            "id": 1
        }))
        .expect("initialize request is valid JSON-RPC");
        let (_, state) = bridge.handle_request_in_process(init_req, ServerState::Uninitialized);

        let private_req: JsonRpcRequest = serde_json::from_value(serde_json::json!({
            "jsonrpc": "2.0",
            "method": "private/control",
            "params": {"command": "shutdown"},
            "id": 2
        }))
        .expect("private method request is valid JSON-RPC");

        let (response, _) = bridge.handle_request_in_process(private_req, state);
        let response = response.expect("unknown method should still produce a response");
        let error = response
            .error
            .expect("unknown private method must return JSON-RPC error");
        assert_eq!(
            error.code, -32601,
            "private control method must stay off MCP surface"
        );
    }
}
