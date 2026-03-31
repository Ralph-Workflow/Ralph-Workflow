//! Session bridge for MCP server lifecycle management.
//!
//! This module bridges the MCP server to the Ralph workflow session management,
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

use crate::dispatch::access::AuditSink;
use crate::dispatch::{HostSession, ToolRegistry, WorkspaceAdapter};
use crate::io::access::McpServerConfig;
use crate::io::transport::{McpStream, McpStreamImpl, TransportError, UnixSocketTransport};
use crate::io::{McpServer, ServerState};
use crate::protocol::JsonRpcRequest;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::sync::Arc;
use std::time::Duration;
use thiserror::Error;

/// Environment variable name for passing MCP endpoint to agents.
static SOCKET_COUNTER: AtomicUsize = AtomicUsize::new(0);

/// Environment variable name used to pass the MCP server endpoint to agent processes.
pub const MCP_ENDPOINT_ENV: &str = "RALPH_MCP_ENDPOINT";

/// Errors that can occur during session bridge operations.
#[derive(Error, Debug)]
pub enum SessionBridgeError {
    /// A transport-level error (I/O, framing, or connection failure).
    #[error("Transport error: {0}")]
    Transport(String),

    /// An error from the underlying MCP server.
    #[error("Server error: {0}")]
    Server(String),

    /// Attempted to start a bridge that is already running.
    #[error("Session already started")]
    AlreadyStarted,

    /// Attempted an operation on a bridge that has not been started.
    #[error("Bridge not started")]
    NotStarted,
}

/// Session bridge that manages MCP server lifecycle for an agent session.
///
/// The bridge creates an MCP server bound to the session's capabilities,
/// provides endpoint configuration for agent connections, and manages
/// the server lifecycle.
pub struct SessionBridge {
    /// Session for capability checking.
    session: Arc<dyn HostSession>,
    /// Workspace for file operations.
    workspace: Arc<dyn WorkspaceAdapter>,
    /// Tool registry with registered tools.
    registry: ToolRegistry,
    /// Server configuration.
    config: McpServerConfig,
    /// Unix socket path for agent connections.
    socket_path: PathBuf,
    /// Flag indicating if the bridge has been started.
    started: bool,
    /// Shared shutdown flag. Set to true to signal the MCP server to shutdown.
    shutdown_flag: Arc<AtomicBool>,
    /// Optional audit sink for recording MCP access decisions.
    audit_sink: Option<Arc<dyn AuditSink>>,
}

impl SessionBridge {
    /// Create a new session bridge for the given session and workspace.
    ///
    /// This creates an MCP server bound to the session but does not start it.
    /// Call `start()` to begin listening for agent connections.
    pub fn new(
        session: Arc<dyn HostSession>,
        config: McpServerConfig,
        workspace: Arc<dyn WorkspaceAdapter>,
        registry: ToolRegistry,
    ) -> Self {
        let nonce = SOCKET_COUNTER.fetch_add(1, Ordering::Relaxed);
        let socket_path = build_socket_path(nonce);
        let shutdown_flag = Arc::new(AtomicBool::new(false));

        Self {
            session,
            workspace,
            registry,
            config,
            socket_path,
            started: false,
            shutdown_flag,
            audit_sink: None,
        }
    }

    /// Get the session identifier.
    pub fn session_id(&self) -> &str {
        self.session.session_id()
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
        is_shutdown(&self.shutdown_flag)
    }

    /// Start the session bridge and MCP server.
    ///
    /// This spawns a background thread that binds the Unix socket and runs the MCP server.
    /// The thread signals readiness after the socket is bound, so callers can connect
    /// immediately after start() returns without timing races.
    ///
    /// Uses the audit sink set via `start_with_audit_sink()`, or none if not set.
    pub fn start(&mut self) -> Result<(), SessionBridgeError> {
        if self.started {
            return Err(SessionBridgeError::AlreadyStarted);
        }

        let (ready_tx, ready_rx) = mpsc::channel::<Result<(), String>>();

        let socket_path = self.socket_path.clone();
        let session = Arc::clone(&self.session);
        let workspace = Arc::clone(&self.workspace);
        let registry = self.registry.clone();
        let config = self.config.clone();
        let shutdown_flag = Arc::clone(&self.shutdown_flag);
        let audit_sink = self.audit_sink.clone();

        std::thread::spawn(move || {
            let server = McpServer::new(session, config, workspace, registry, audit_sink);
            if let Err(e) = run_server(server, socket_path, shutdown_flag, ready_tx) {
                eprintln!("MCP server error: {}", e);
            }
        });

        // Wait for the socket to be bound before returning.
        // The spawned thread signals via ready_tx once UnixSocketTransport::new() completes.
        ready_rx
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| {
                SessionBridgeError::Transport("socket bind timed out after 5s".to_string())
            })?
            .map_err(SessionBridgeError::Transport)?;

        self.started = true;
        Ok(())
    }

    /// Start the session bridge with an audit sink.
    ///
    /// This is equivalent to calling `start()` but allows passing an audit sink
    /// that will be used for recording MCP access decisions.
    pub fn start_with_audit_sink(
        &mut self,
        audit_sink: Arc<dyn AuditSink>,
    ) -> Result<(), SessionBridgeError> {
        self.audit_sink = Some(audit_sink);
        self.start()
    }

    /// Signal the session bridge to shutdown.
    pub fn shutdown(&self) {
        self.shutdown_flag.store(true, Ordering::Release);
    }
}

impl Clone for SessionBridge {
    fn clone(&self) -> Self {
        Self {
            session: Arc::clone(&self.session),
            workspace: Arc::clone(&self.workspace),
            registry: self.registry.clone(),
            config: self.config.clone(),
            socket_path: self.socket_path.clone(),
            started: false, // Cloned bridges start in unstarted state
            shutdown_flag: Arc::clone(&self.shutdown_flag),
            audit_sink: self.audit_sink.clone(),
        }
    }
}

impl Drop for SessionBridge {
    fn drop(&mut self) {
        // Signal the server thread to shut down if it was started.
        if self.started {
            self.shutdown();
        }
        // Clean up the socket file. This is safe because:
        // 1. If started, the shutdown signal will cause the listener to close
        //    before the socket file is removed.
        // 2. If not started, there's no listener using the socket.
        let _ = std::fs::remove_file(&self.socket_path);
    }
}

/// Build a unique socket path for a session.
fn build_socket_path(nonce: usize) -> PathBuf {
    let socket_dir = std::env::temp_dir().join("ralph-mcp");
    let _ = std::fs::create_dir_all(&socket_dir);
    socket_dir.join(format!("session-{}.sock", nonce))
}

/// Pure: check shutdown flag.
#[inline]
fn is_shutdown(flag: &AtomicBool) -> bool {
    flag.load(Ordering::Acquire)
}

/// Pure: classify read result.
fn classify_read(result: Result<Option<JsonRpcRequest>, TransportError>) -> ReadClass {
    match result {
        Ok(None) => ReadClass::Eof,
        Ok(Some(req)) => ReadClass::Request(req),
        Err(e) => {
            eprintln!("MCP read error: {}", e);
            ReadClass::Error
        }
    }
}

/// Read classification.
enum ReadClass {
    Request(JsonRpcRequest),
    Eof,
    Error,
}

/// Accept classification.
enum AcceptClass {
    /// A client connection is ready.
    Connection(McpStreamImpl),
    /// No connection ready (non-blocking mode).
    NoConnection,
    /// Server is shutting down.
    Shutdown,
    /// Transport error occurred.
    Error(String),
}

/// Pure: classify accept result.
fn classify_accept(result: Result<Option<McpStreamImpl>, TransportError>) -> AcceptClass {
    match result {
        Ok(Some(stream)) => AcceptClass::Connection(stream),
        Ok(None) => AcceptClass::NoConnection,
        Err(TransportError::Shutdown) => AcceptClass::Shutdown,
        Err(e) => AcceptClass::Error(e.to_string()),
    }
}

/// Result of executing an accept action - controls loop continuation.
enum AcceptOutcome {
    Continue,
    Exit,
}

/// Execute accept action. Returns outcome to signal loop continuation and updated state.
///
/// # Per-Connection State
///
/// Each new client connection MUST start from `ServerState::Uninitialized` so that
/// the initialize handshake is enforced for every connection. The state is NOT
/// carried across connections — accumulated state from previous connections is discarded.
fn execute_accept_action(
    cls: AcceptClass,
    server: &McpServer,
    shutdown_flag: &Arc<AtomicBool>,
    state: ServerState,
) -> (AcceptOutcome, ServerState) {
    match cls {
        AcceptClass::Connection(mut stream) => {
            // Each new connection must complete the initialize handshake, regardless of
            // what state the server was in after the previous connection.
            // The `state` parameter is intentionally ignored — always use Uninitialized.
            let new_state = handle_connection(
                server,
                &mut stream,
                shutdown_flag,
                ServerState::Uninitialized,
            );
            (AcceptOutcome::Continue, new_state)
        }
        AcceptClass::NoConnection => {
            std::thread::sleep(std::time::Duration::from_millis(50));
            (AcceptOutcome::Continue, state)
        }
        AcceptClass::Shutdown => (AcceptOutcome::Exit, state),
        AcceptClass::Error(e) => {
            eprintln!("MCP transport error in accept: {}", e);
            std::thread::sleep(std::time::Duration::from_millis(50));
            (AcceptOutcome::Continue, state)
        }
    }
}

/// Handle one request, return break flag and new state.
fn handle_one(
    server: &McpServer,
    stream: &mut dyn McpStream,
    st: ServerState,
) -> (bool, ServerState) {
    let res = stream.read_request();
    let cls = classify_read(res);
    match cls {
        ReadClass::Request(req) => {
            let (resp, ns) = server.handle_request(req, st);
            // For notifications (id is None), no response should be sent
            if let Some(response) = resp {
                let broke = stream.write_response(&response).is_err();
                (broke, ns)
            } else {
                (false, ns)
            }
        }
        ReadClass::Eof | ReadClass::Error => (true, st),
    }
}

/// Handle connection until EOF or shutdown. Returns final state.
fn handle_connection(
    server: &McpServer,
    stream: &mut dyn McpStream,
    shutdown_flag: &Arc<AtomicBool>,
    mut st: ServerState,
) -> ServerState {
    loop {
        if is_shutdown(shutdown_flag) {
            return st;
        }
        let (br, ns) = handle_one(server, stream, st);
        st = ns;
        if br {
            return st;
        }
    }
}

/// Run server loop.
fn run_server(
    server: McpServer,
    socket_path: PathBuf,
    shutdown_flag: Arc<AtomicBool>,
    ready_tx: mpsc::Sender<Result<(), String>>,
) -> Result<(), SessionBridgeError> {
    let mut listener = UnixSocketTransport::new(socket_path, Arc::clone(&shutdown_flag))
        .map_err(|e| SessionBridgeError::Transport(e.to_string()))?;
    // Signal that the socket is bound and ready to accept connections.
    let _ = ready_tx.send(Ok(()));
    let state = ServerState::Uninitialized;
    run_server_loop(&mut listener, &server, &shutdown_flag, state)
}

fn run_server_loop(
    listener: &mut UnixSocketTransport,
    server: &McpServer,
    shutdown_flag: &Arc<AtomicBool>,
    state: ServerState,
) -> Result<(), SessionBridgeError> {
    let mut state = state;
    loop {
        // 1. Gather input
        let accept_result = listener.accept();
        // 2. Pure classification
        let cls = classify_accept(accept_result);
        // 3. Execute and decide continuation
        let (outcome, new_state) = execute_accept_action(cls, server, shutdown_flag, state);
        state = new_state;
        match outcome {
            AcceptOutcome::Continue => {}
            AcceptOutcome::Exit => return Ok(()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dispatch::access::{AccessDecision, McpCapability};
    use crate::dispatch::host::DirEntry;
    use std::path::Path;

    struct TestSession;
    impl HostSession for TestSession {
        fn session_id(&self) -> &str {
            "test-session"
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

    struct TestWorkspace;
    impl WorkspaceAdapter for TestWorkspace {
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
    fn test_socket_path_format() {
        let path = build_socket_path(12345);
        let path_str = path.display().to_string();
        assert!(path_str.contains("ralph-mcp"));
        assert!(path_str.contains("session-12345"));
        assert!(path_str.ends_with(".sock"));
    }

    #[test]
    fn test_endpoint_uri() {
        let session = Arc::new(TestSession) as Arc<dyn HostSession>;
        let workspace = Arc::new(TestWorkspace) as Arc<dyn WorkspaceAdapter>;
        let registry = ToolRegistry::new(vec![]);
        let config = McpServerConfig::new(std::env::temp_dir());
        let bridge = SessionBridge::new(session, config, workspace, registry);

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
        let session = Arc::new(TestSession) as Arc<dyn HostSession>;
        let workspace = Arc::new(TestWorkspace) as Arc<dyn WorkspaceAdapter>;
        let registry = ToolRegistry::new(vec![]);
        let config = McpServerConfig::new(std::env::temp_dir());
        let bridge = SessionBridge::new(session, config, workspace, registry);

        assert!(!bridge.is_started());
        assert_eq!(bridge.session_id(), "test-session");
    }
}
