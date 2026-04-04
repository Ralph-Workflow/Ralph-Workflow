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
//! The session bridge creates a Unix socket endpoint for each session.
//! The endpoint path is passed to the agent via the `RALPH_MCP_ENDPOINT` environment variable.
//!
//! The MCP server runs in a background thread and listens on the Unix socket for
//! agent connections.

use crate::agents::session::{AgentSession, AuditRecord as RalphAuditRecord, AuditTrail};
use crate::mcp_server::tool_bridge::{
    build_ralph_tool_registry, RalphAuditSinkAdapter, RalphHostSessionAdapter,
    RalphWorkspaceAdapter,
};
use crate::workspace::Workspace;
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::SessionBridge as McpSessionBridge;
use std::path::PathBuf;
use std::sync::Arc;
use thiserror::Error;

pub const MCP_ENDPOINT_ENV: &str = "RALPH_MCP_ENDPOINT";

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
    /// Inner session bridge from the mcp-server crate.
    inner: McpSessionBridge,
    /// Audit sink adapter that accumulates MCP audit records.
    audit_adapter: Arc<RalphAuditSinkAdapter>,
    /// Cached view of audit records for backward-compatible API.
    cached_audit: AuditTrail,
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

        // Create config with workspace root and session ID for audit correlation
        let config = McpServerConfig::new(workspace.root().to_path_buf())
            .with_session_id(session_arc.session_id.as_str().to_string())
            .with_access_mode(::mcp_server::dispatch::access::AccessMode::ReadWrite);

        // Create the inner session bridge (audit sink passed at start() time)
        let inner = McpSessionBridge::new(host, config, ws, registry);

        Self {
            session: session_arc,
            inner,
            audit_adapter,
            cached_audit: AuditTrail::new(),
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

    /// Get the Unix socket path for agent connections.
    pub fn socket_path(&self) -> &PathBuf {
        self.inner.socket_path()
    }

    /// Get the MCP endpoint URI for passing to agents.
    ///
    /// Returns a URI like `unix:///path/to/socket` that agents can use
    /// to connect to the MCP server.
    pub fn endpoint_uri(&self) -> String {
        self.inner.endpoint_uri()
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
    /// This spawns a background thread that binds the Unix socket and runs the MCP server.
    /// The thread signals readiness after the socket is bound, so callers can connect
    /// immediately after start() returns without timing races.
    pub fn start(&mut self) -> Result<(), SessionBridgeError> {
        self.inner
            .start_with_audit_sink(self.audit_adapter.clone())
            .map_err(|e| SessionBridgeError::Transport(e.to_string()))
    }

    /// Shutdown the session bridge gracefully.
    ///
    /// This signals the MCP server to shutdown and waits for the server thread to finish.
    pub fn shutdown(&mut self) {
        self.inner.shutdown();
    }
}

impl Clone for SessionBridge {
    fn clone(&self) -> Self {
        Self {
            session: Arc::clone(&self.session),
            inner: self.inner.clone(),
            audit_adapter: Arc::clone(&self.audit_adapter),
            cached_audit: self.cached_audit.clone(),
        }
    }
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
    use mcp_server::dispatch::access::AuditSink;
    use mcp_server::dispatch::access::{AccessDecision, McpCapability};
    use mcp_server::dispatch::audit::AuditRecord as McpAuditRecord;

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

    #[test]
    fn test_socket_path_format() {
        let bridge = SessionBridge::new(unique_session(), test_workspace());
        let path = bridge.socket_path();
        let path_str = path.display().to_string();
        assert!(path_str.contains("ralph-mcp"));
        assert!(path_str.ends_with(".sock"));
    }

    #[test]
    fn test_endpoint_uri() {
        let bridge = SessionBridge::new(unique_session(), test_workspace());
        let uri = bridge.endpoint_uri();
        assert!(uri.starts_with("unix://"));
        assert!(uri.ends_with(".sock"));
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
}
