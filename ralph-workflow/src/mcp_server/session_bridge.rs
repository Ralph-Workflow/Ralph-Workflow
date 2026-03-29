//! Session bridge for MCP server lifecycle management.
//!
//! This module bridges the `AgentSession` to the `McpServer` lifecycle,
//! managing the MCP server alongside each agent invocation.
//!
//! # Architecture
//!
//! The session bridge creates endpoint configuration for MCP communication.
//! When wired into the agent spawn flow (Step 6), this configuration is
//! passed to the agent process via environment variables.
//!
//! # Endpoint Management
//!
//! The session bridge creates a Unix socket endpoint for each session.
//! The endpoint path is passed to the agent via the `RALPH_MCP_ENDPOINT` environment variable.
//!
//! The MCP server runs in a background thread and listens on the Unix socket for
//! agent connections. Each connection is handled sequentially (one agent at a time).

use crate::agents::session::{AgentSession, AuditRecord, AuditTrail};
use crate::mcp_server::{McpServer, McpServerError};
use crate::workspace::Workspace;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::sync::Arc;
use std::thread::JoinHandle;

/// Environment variable name for passing MCP endpoint to agents.
static SOCKET_COUNTER: AtomicUsize = AtomicUsize::new(0);

pub const MCP_ENDPOINT_ENV: &str = "RALPH_MCP_ENDPOINT";

/// Session bridge that manages MCP server lifecycle for an agent session.
///
/// The bridge creates an MCP server bound to the agent's session,
/// provides endpoint configuration for agent connections, and manages
/// the server lifecycle.
pub struct SessionBridge {
    /// Cloned session Arc — accessible at any lifecycle stage without panicking.
    session: Arc<AgentSession>,
    /// Cached audit trail — empty until `drain_audit_records()` is called.
    cached_audit: AuditTrail,
    /// Receiver for audit records produced by the background MCP server thread.
    audit_receiver: Option<mpsc::Receiver<AuditRecord>>,
    /// The MCP server bound to this session (None after start() is called).
    mcp_server: Option<McpServer>,
    /// Unix socket path for agent connections.
    socket_path: PathBuf,
    /// Flag indicating if the bridge has been started.
    started: bool,
    /// Flag indicating if the bridge has been shutdown.
    shutdown: bool,
    /// Handle to the background server thread.
    server_thread: Option<JoinHandle<()>>,
    /// Shared shutdown flag. Set to true to signal the MCP server to shutdown.
    /// This is shared with the server thread so we can signal shutdown from Drop.
    shutdown_flag: Arc<AtomicBool>,
}

impl SessionBridge {
    /// Create a new session bridge for the given session and workspace.
    ///
    /// This creates an MCP server bound to the session but does not start it.
    /// Call `start()` to begin listening for agent connections.
    pub fn new(session: AgentSession, workspace: Arc<dyn Workspace>) -> Self {
        let nonce = SOCKET_COUNTER.fetch_add(1, Ordering::Relaxed);
        let socket_path = build_socket_path(&session, nonce);
        let shutdown_flag = Arc::new(AtomicBool::new(false));
        let session_arc = Arc::new(session.clone());
        let (audit_tx, audit_rx) = mpsc::channel::<AuditRecord>();
        let mcp_server = McpServer::new_with_audit_sender(
            session,
            workspace,
            Arc::clone(&shutdown_flag),
            audit_tx,
        );
        Self {
            session: session_arc,
            cached_audit: AuditTrail::new(),
            audit_receiver: Some(audit_rx),
            mcp_server: Some(mcp_server),
            socket_path,
            started: false,
            shutdown: false,
            server_thread: None,
            shutdown_flag,
        }
    }

    /// Get the session this bridge is bound to.
    pub fn session(&self) -> &AgentSession {
        &self.session
    }

    /// Get the audit trail from the bound MCP server.
    pub fn audit_trail(&self) -> &AuditTrail {
        self.mcp_server
            .as_ref()
            .map(McpServer::audit_trail)
            .unwrap_or(&self.cached_audit)
    }

    /// Drain all audit records produced by the MCP server background thread.
    ///
    /// Shuts down the bridge (if not already shut down), waits for the server thread
    /// to finish, then collects all pending audit records from the channel.
    ///
    /// This must be called after the agent execution completes to retrieve the audit
    /// records from MCP tool calls before the bridge is dropped.
    pub fn drain_audit_records(&mut self) -> Vec<AuditRecord> {
        self.shutdown();
        self.audit_receiver
            .as_ref()
            .map(|rx| std::iter::from_fn(|| rx.try_recv().ok()).collect())
            .unwrap_or_default()
    }

    /// Get the Unix socket path for agent connections.
    pub fn socket_path(&self) -> &PathBuf {
        &self.socket_path
    }

    /// Get the MCP endpoint URI for passing to agents.
    ///
    /// Returns a URI like `unix:///path/to/socket` that agents can use
    /// to connect to the MCP server.
    pub fn endpoint_uri(&self) -> String {
        format!("unix://{}", self.socket_path.display())
    }

    /// Get the environment variable name for the MCP endpoint.
    pub fn endpoint_env_var(&self) -> &'static str {
        MCP_ENDPOINT_ENV
    }

    /// Check if the bridge has been started.
    pub fn is_started(&self) -> bool {
        self.started
    }

    /// Check if the bridge has been shutdown.
    pub fn is_shutdown(&self) -> bool {
        self.shutdown
    }

    /// Start the session bridge and MCP server.
    ///
    /// This:
    /// 1. Spawns a background thread that binds the Unix socket and runs the MCP server
    /// 2. Blocks until the socket is bound and listening (eliminates the readiness race)
    /// 3. Returns only after the agent can safely connect to the socket
    ///
    /// # Errors
    ///
    /// Returns an error if the socket cannot be created/bound or if the thread fails to spawn.
    pub fn start(&mut self) -> Result<(), McpServerError> {
        if self.started {
            return Err(McpServerError::Transport(std::io::Error::new(
                std::io::ErrorKind::AlreadyExists,
                "Session bridge already started",
            )));
        }
        let socket_path = self.socket_path.clone();
        let server = self.mcp_server.take().expect("mcp_server already taken");
        let thread = spawn_server_thread(server, socket_path)?;
        self.server_thread = Some(thread);
        self.started = true;
        Ok(())
    }

    /// Shutdown the session bridge gracefully.
    ///
    /// This signals the MCP server to shutdown and waits for the server thread to finish.
    pub fn shutdown(&mut self) {
        if self.shutdown {
            return;
        }
        self.shutdown = true;

        // Signal the server thread to shutdown via the shared flag
        self.shutdown_flag.store(true, Ordering::Release);

        // Wait for the server thread to finish
        if let Some(handle) = self.server_thread.take() {
            let _ = handle.join();
        }
    }
}

/// Spawn the MCP server background thread, waiting for the socket to be ready.
///
/// Returns the join handle on success, or an error if the socket fails to bind.
fn spawn_server_thread(
    mut server: McpServer,
    socket_path: PathBuf,
) -> Result<std::thread::JoinHandle<()>, McpServerError> {
    let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<(), String>>();
    let thread = std::thread::spawn(move || {
        server.run_socket_with_ready(&socket_path, ready_tx);
    });
    let recv = ready_rx.recv_timeout(std::time::Duration::from_secs(5));
    match interpret_ready_recv(recv) {
        Ok(()) => Ok(thread),
        Err(e) => {
            let _ = thread.join();
            Err(e)
        }
    }
}

/// Build a unique socket path for a session.
fn build_socket_path(session: &AgentSession, nonce: usize) -> PathBuf {
    let socket_dir = std::env::temp_dir().join("ralph-mcp");
    let session_id = session.session_id.as_str();
    PathBuf::from(format!(
        "{}/{}-{}.sock",
        socket_dir.display(),
        session_id,
        nonce
    ))
}

/// Interpret a `recv_timeout` result for the socket readiness signal.
///
/// Returns `Ok(())` on successful socket bind, or the McpServerError on failure.
/// The caller is responsible for joining or discarding `thread` on the error path.
fn interpret_ready_recv(
    recv: Result<Result<(), String>, std::sync::mpsc::RecvTimeoutError>,
) -> Result<(), McpServerError> {
    match recv {
        Ok(Ok(())) => Ok(()),
        Ok(Err(e)) => Err(McpServerError::Transport(std::io::Error::other(e))),
        Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
            Err(McpServerError::Transport(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "MCP socket bind timed out after 5s",
            )))
        }
        Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
            Err(McpServerError::Transport(std::io::Error::new(
                std::io::ErrorKind::ConnectionAborted,
                "MCP server thread exited before sending ready signal",
            )))
        }
    }
}

/// Wait for a thread to finish within a deadline, then join if done.
fn wait_for_thread_with_timeout(handle: std::thread::JoinHandle<()>) {
    let deadline = std::time::Instant::now() + std::time::Duration::from_millis(2000);
    while !handle.is_finished() {
        if std::time::Instant::now() >= deadline {
            return; // Detach — thread will exit on its own
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let _ = handle.join();
}

impl Drop for SessionBridge {
    fn drop(&mut self) {
        if !self.shutdown {
            self.shutdown_flag.store(true, Ordering::Release);
            if let Some(handle) = self.server_thread.take() {
                wait_for_thread_with_timeout(handle);
            }
        }
        if self.socket_path.exists() {
            let _ = std::fs::remove_file(&self.socket_path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::memory_workspace::MemoryWorkspace;

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
        let session = AgentSession::for_drain(
            "test-run".to_string(),
            crate::agents::session::SessionDrain::Development,
            1,
        );
        let path = build_socket_path(&session, 0);
        let path_str = path.display().to_string();
        assert!(path_str.contains("ralph-mcp"));
        assert!(path_str.contains("test-run"));
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
    fn test_bridge_double_start_error() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        bridge.start().expect("first start should succeed");
        let result = bridge.start();
        assert!(result.is_err());
    }

    #[test]
    fn test_bridge_shutdown() {
        let mut bridge = SessionBridge::new(unique_session(), test_workspace());
        bridge.start().expect("start should succeed");
        bridge.shutdown();
        assert!(bridge.is_shutdown());
    }

    #[test]
    fn test_session_access() {
        let session = AgentSession::for_drain(
            "test-run".to_string(),
            crate::agents::session::SessionDrain::Development,
            1,
        );
        let bridge = SessionBridge::new(session, test_workspace());
        let s = bridge.session();
        assert_eq!(s.session_id.as_str(), "test-run-development-1");
    }

    #[test]
    fn test_audit_trail_access() {
        let bridge = SessionBridge::new(unique_session(), test_workspace());
        let trail = bridge.audit_trail();
        assert!(trail.is_empty());
    }
}
