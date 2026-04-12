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
//! The session bridge creates a TCP loopback endpoint for each session.
//! The endpoint path is passed to the agent via the `RALPH_MCP_ENDPOINT` environment variable.
//!
//! The MCP server runs in a background thread and listens on the TCP loopback endpoint for
//! agent connections.

use crate::dispatch::access::{AuditSink, PolicyMode};
use crate::dispatch::{HostSession, ToolRegistry, WorkspaceAdapter};
use crate::io::access::McpServerConfig;
use crate::io::control::{
    ControlCommand, ControlError, ControlReceiver, ControlRequest, ControlResult, ControlSender,
    POLICY_CHALLENGE_LENGTH,
};
use crate::io::transport::{McpStream, McpStreamImpl, TcpLoopbackTransport, TransportError};
use crate::io::{EndpointLease, McpServer, ServerState};
use crate::protocol::{JsonRpcRequest, JsonRpcResponse};
use rand::{distributions::Alphanumeric, thread_rng, Rng};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::sync::Arc;
use std::time::{Duration, SystemTime};
use thiserror::Error;

struct TransitionRuntime {
    active_mode: std::sync::Mutex<PolicyMode>,
    serialize_guard: std::sync::Mutex<()>,
    transition_index: AtomicU64,
}

impl TransitionRuntime {
    fn new(initial_mode: PolicyMode) -> Self {
        Self {
            active_mode: std::sync::Mutex::new(initial_mode),
            serialize_guard: std::sync::Mutex::new(()),
            transition_index: AtomicU64::new(0),
        }
    }

    fn transition_to(&self, next_mode: PolicyMode) -> (PolicyMode, PolicyMode, u64) {
        let _serialize = self
            .serialize_guard
            .lock()
            .expect("transition lock poisoned");
        let mut mode_guard = self.active_mode.lock().expect("mode state lock poisoned");
        let old_mode = *mode_guard;
        *mode_guard = next_mode;
        let index = self.transition_index.fetch_add(1, Ordering::AcqRel) + 1;
        (old_mode, next_mode, index)
    }
}

/// Environment variable name for passing MCP endpoint to agents.
static SOCKET_COUNTER: AtomicUsize = AtomicUsize::new(0);

/// Environment variable name used to pass the MCP server endpoint to agent processes.
pub const MCP_ENDPOINT_ENV: &str = "RALPH_MCP_ENDPOINT";

#[derive(Debug, Clone)]
struct ServerReady {
    lease: EndpointLease,
    challenge: String,
}

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
    /// Endpoint URI for agent connections.
    endpoint_uri: String,
    /// Latest endpoint lease reported to orchestrator.
    endpoint_lease: Option<EndpointLease>,
    /// Flag indicating if the bridge has been started.
    started: bool,
    /// Shared shutdown flag. Set to true to signal the MCP server to shutdown.
    shutdown_flag: Arc<AtomicBool>,
    /// Optional audit sink for recording MCP access decisions.
    audit_sink: Option<Arc<dyn AuditSink>>,
    /// Private control sender for orchestrator commands.
    control_tx: Option<ControlSender>,
    /// Challenge phrase shared with the orchestrator for policy RPCs.
    policy_challenge: Option<String>,
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
        let _nonce = SOCKET_COUNTER.fetch_add(1, Ordering::Relaxed);
        let shutdown_flag = Arc::new(AtomicBool::new(false));

        Self {
            session,
            workspace,
            registry,
            config,
            endpoint_uri: String::new(),
            endpoint_lease: None,
            started: false,
            shutdown_flag,
            audit_sink: None,
            control_tx: None,
            policy_challenge: None,
        }
    }

    /// Get the session identifier.
    pub fn session_id(&self) -> &str {
        self.session.session_id()
    }

    /// Get the MCP endpoint URI for passing to agents.
    ///
    /// Returns a URI like `tcp://127.0.0.1:12345` that agents can use
    /// to connect to the MCP server.
    pub fn endpoint_uri(&self) -> String {
        self.endpoint_uri.clone()
    }

    /// Get the latest endpoint lease advertised by the server.
    pub fn endpoint_lease(&self) -> Option<EndpointLease> {
        self.endpoint_lease.clone()
    }

    /// Get the environment variable name for the MCP endpoint.
    pub fn endpoint_env_var(&self) -> &'static str {
        MCP_ENDPOINT_ENV
    }

    /// Get the policy challenge string emitted during startup.
    pub fn policy_challenge(&self) -> Option<&str> {
        self.policy_challenge.as_deref()
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
    /// This spawns a background thread that binds the TCP loopback endpoint and runs the MCP server.
    /// The thread signals readiness after the endpoint is bound, so callers can connect
    /// immediately after start() returns without timing races.
    ///
    /// Uses the audit sink set via `start_with_audit_sink()`, or none if not set.
    pub fn start(&mut self) -> Result<(), SessionBridgeError> {
        if self.started {
            return Err(SessionBridgeError::AlreadyStarted);
        }

        let (ready_tx, ready_rx) = mpsc::channel::<Result<ServerReady, String>>();
        self.spawn_server_thread(ready_tx);
        let ready = wait_for_socket_ready(ready_rx)?;
        self.endpoint_uri = ready.lease.endpoint.clone();
        self.endpoint_lease = Some(ready.lease.clone());
        self.policy_challenge = Some(ready.challenge);

        self.started = true;
        Ok(())
    }

    /// Spawn the MCP server thread with cloned state.
    fn spawn_server_thread(&mut self, ready_tx: mpsc::Sender<Result<ServerReady, String>>) {
        let session = Arc::clone(&self.session);
        let workspace = Arc::clone(&self.workspace);
        let registry = self.registry.clone();
        let config = self.config.clone();
        let shutdown_flag = Arc::clone(&self.shutdown_flag);
        let audit_sink = self.audit_sink.clone();

        let (control_tx, control_rx) = mpsc::channel::<ControlRequest>();
        self.control_tx = Some(control_tx.clone());

        std::thread::spawn(move || {
            let run_id = config.run_id.clone();
            let generation = config.generation;
            let server = McpServer::new(session, config, workspace, registry, audit_sink);
            if let Err(e) = run_server(
                server,
                shutdown_flag,
                ready_tx,
                control_rx,
                run_id,
                generation,
            ) {
                tracing::error!(error = %e, "MCP server error");
            }
        });
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

    /// Send a control command to the MCP server over the private channel.
    pub fn send_control_command(&self, command: ControlCommand) -> Result<(), ControlError> {
        let sender = self
            .control_tx
            .as_ref()
            .ok_or(ControlError::ChannelClosed)?;
        let (response_tx, response_rx) = mpsc::channel::<ControlResult>();
        let challenge = self
            .policy_challenge
            .as_ref()
            .ok_or(ControlError::ChallengeMissing)?
            .clone();
        let request = ControlRequest {
            challenge,
            command,
            requester_id: self.session.session_id().to_string(),
            requester_context: Some(
                serde_json::json!({
                    "session_id": self.session.session_id(),
                    "run_id": self.session.run_id(),
                })
                .to_string(),
            ),
            response: response_tx,
        };
        sender
            .send(request)
            .map_err(|_| ControlError::ChannelClosed)?;
        response_rx
            .recv()
            .map_err(|_| ControlError::ChannelClosed)??;
        Ok(())
    }

    /// Return a clone of the private control sender for reuse.
    pub fn control_sender(&self) -> Option<ControlSender> {
        self.control_tx.clone()
    }

    /// Signal the session bridge to shutdown.
    pub fn shutdown(&self) {
        self.shutdown_flag.store(true, Ordering::Release);
    }

    /// Handle a request directly without binding a transport.
    ///
    /// This seam keeps protocol tests deterministic in environments where
    /// socket binding is unavailable. It exercises the same server dispatch
    /// path as the transport bridge while leaving connection lifecycle to
    /// the caller-provided [`ServerState`].
    pub fn handle_request_in_process(
        &self,
        request: JsonRpcRequest,
        state: ServerState,
    ) -> (Option<JsonRpcResponse>, ServerState) {
        self.build_server().handle_request(request, state)
    }

    fn build_server(&self) -> McpServer {
        McpServer::new(
            Arc::clone(&self.session),
            self.config.clone(),
            Arc::clone(&self.workspace),
            self.registry.clone(),
            self.audit_sink.clone(),
        )
    }
}

impl Clone for SessionBridge {
    fn clone(&self) -> Self {
        Self {
            session: Arc::clone(&self.session),
            workspace: Arc::clone(&self.workspace),
            registry: self.registry.clone(),
            config: self.config.clone(),
            endpoint_uri: self.endpoint_uri.clone(),
            endpoint_lease: self.endpoint_lease.clone(),
            started: false, // Cloned bridges start in unstarted state
            shutdown_flag: Arc::clone(&self.shutdown_flag),
            audit_sink: self.audit_sink.clone(),
            control_tx: self.control_tx.clone(),
            policy_challenge: self.policy_challenge.clone(),
        }
    }
}

impl Drop for SessionBridge {
    fn drop(&mut self) {
        // Signal the server thread to shut down if it was started.
        if self.started {
            self.shutdown();
        }
    }
}

/// Wait for the server thread to signal that the socket is bound.
fn wait_for_socket_ready(
    ready_rx: mpsc::Receiver<Result<ServerReady, String>>,
) -> Result<ServerReady, SessionBridgeError> {
    ready_rx
        .recv_timeout(Duration::from_secs(5))
        .map_err(|_| SessionBridgeError::Transport("socket bind timed out after 5s".to_string()))?
        .map_err(SessionBridgeError::Transport)
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
            tracing::warn!(error = %e, "MCP read error");
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
/// Handle a new client connection: reset state and run initialize handshake.
fn handle_new_connection(
    mut stream: McpStreamImpl,
    server: &McpServer,
    shutdown_flag: &Arc<AtomicBool>,
    control_rx: &ControlReceiver,
    policy_challenge: &str,
    transition_runtime: &TransitionRuntime,
) -> ServerState {
    tracing::debug!("MCP server accepted client connection — resetting to Uninitialized");
    handle_connection(
        server,
        &mut stream,
        shutdown_flag,
        ServerState::Uninitialized,
        control_rx,
        policy_challenge,
        transition_runtime,
    )
}

/// Log and sleep on transport accept error.
fn handle_accept_error(e: String) {
    tracing::warn!(error = %e, "MCP transport error in accept");
    std::thread::sleep(std::time::Duration::from_millis(50));
}

/// Execute accept action. Returns outcome to signal loop continuation and updated state.
fn execute_accept_action(
    cls: AcceptClass,
    server: &McpServer,
    shutdown_flag: &Arc<AtomicBool>,
    state: ServerState,
    control_rx: &ControlReceiver,
    policy_challenge: &str,
    transition_runtime: &TransitionRuntime,
) -> (AcceptOutcome, ServerState) {
    match cls {
        AcceptClass::Connection(stream) => {
            let new_state = handle_new_connection(
                stream,
                server,
                shutdown_flag,
                control_rx,
                policy_challenge,
                transition_runtime,
            );
            (AcceptOutcome::Continue, new_state)
        }
        AcceptClass::NoConnection => {
            std::thread::sleep(std::time::Duration::from_millis(50));
            (AcceptOutcome::Continue, state)
        }
        AcceptClass::Shutdown => (AcceptOutcome::Exit, state),
        AcceptClass::Error(e) => {
            handle_accept_error(e);
            (AcceptOutcome::Continue, state)
        }
    }
}

fn generate_policy_challenge() -> String {
    thread_rng()
        .sample_iter(&Alphanumeric)
        .take(POLICY_CHALLENGE_LENGTH)
        .map(char::from)
        .collect()
}

fn ensure_request_challenge_present(challenge: &str) -> Result<(), ControlError> {
    if challenge.is_empty() {
        return Err(ControlError::AccessDenied(
            "missing policy challenge".to_string(),
        ));
    }
    Ok(())
}

fn ensure_request_challenge_length(challenge: &str) -> Result<(), ControlError> {
    if challenge.chars().count() != POLICY_CHALLENGE_LENGTH {
        return Err(ControlError::AccessDenied(format!(
            "invalid policy challenge length: expected {}",
            POLICY_CHALLENGE_LENGTH
        )));
    }
    Ok(())
}

fn ensure_policy_challenge_length(policy_challenge: &str) -> Result<(), ControlError> {
    if policy_challenge.chars().count() != POLICY_CHALLENGE_LENGTH {
        return Err(ControlError::Rejected(format!(
            "server policy challenge length is invalid: expected {}",
            POLICY_CHALLENGE_LENGTH
        )));
    }
    Ok(())
}

fn ensure_challenge_matches(challenge: &str, policy_challenge: &str) -> Result<(), ControlError> {
    if challenge != policy_challenge {
        return Err(ControlError::AccessDenied(
            "invalid policy challenge".to_string(),
        ));
    }
    Ok(())
}

fn validate_control_challenge(challenge: &str, policy_challenge: &str) -> Result<(), ControlError> {
    ensure_request_challenge_present(challenge)
        .and_then(|()| ensure_request_challenge_length(challenge))
        .and_then(|()| ensure_policy_challenge_length(policy_challenge))
        .and_then(|()| ensure_challenge_matches(challenge, policy_challenge))
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
    control_rx: &ControlReceiver,
    policy_challenge: &str,
    transition_runtime: &TransitionRuntime,
) -> ServerState {
    loop {
        match connection_step(
            server,
            stream,
            shutdown_flag,
            st,
            control_rx,
            policy_challenge,
            transition_runtime,
        ) {
            ConnectionStep::Continue(next_state) => st = next_state,
            ConnectionStep::Exit(final_state) => return final_state,
        }
    }
}

enum ConnectionStep {
    Continue(ServerState),
    Exit(ServerState),
}

fn connection_step(
    server: &McpServer,
    stream: &mut dyn McpStream,
    shutdown_flag: &Arc<AtomicBool>,
    state: ServerState,
    control_rx: &ControlReceiver,
    policy_challenge: &str,
    transition_runtime: &TransitionRuntime,
) -> ConnectionStep {
    if process_control_cycle(
        control_rx,
        shutdown_flag,
        policy_challenge,
        server,
        transition_runtime,
    ) {
        return ConnectionStep::Exit(state);
    }

    let (should_break, next_state) = handle_one(server, stream, state);
    if should_break {
        ConnectionStep::Exit(next_state)
    } else {
        ConnectionStep::Continue(next_state)
    }
}

fn process_control_cycle(
    control_rx: &ControlReceiver,
    shutdown_flag: &Arc<AtomicBool>,
    policy_challenge: &str,
    server: &McpServer,
    transition_runtime: &TransitionRuntime,
) -> bool {
    process_control_messages(
        control_rx,
        shutdown_flag,
        policy_challenge,
        server,
        transition_runtime,
    );
    is_shutdown(shutdown_flag)
}

/// Run server loop.
fn run_server(
    server: McpServer,
    shutdown_flag: Arc<AtomicBool>,
    ready_tx: mpsc::Sender<Result<ServerReady, String>>,
    control_rx: ControlReceiver,
    run_id: Option<String>,
    generation: Option<u32>,
) -> Result<(), SessionBridgeError> {
    let mut listener = TcpLoopbackTransport::new(Arc::clone(&shutdown_flag)).map_err(|e| {
        tracing::error!(error = %e, "MCP tcp bind failed");
        SessionBridgeError::Transport(e.to_string())
    })?;
    let endpoint_uri = format!("tcp://{}", listener.local_addr());
    let challenge = generate_policy_challenge();
    let policy_challenge = Arc::new(challenge.clone());
    let run_id_value = run_id.unwrap_or_else(|| "unknown".to_string());
    let generation_value = generation.unwrap_or(0);
    let lease = EndpointLease::new(
        endpoint_uri.clone(),
        run_id_value,
        generation_value,
        SystemTime::now(),
    );
    let ready = ServerReady {
        lease: lease.clone(),
        challenge,
    };
    let _ = ready_tx.send(Ok(ready));
    let state = ServerState::Uninitialized;
    let transition_runtime = TransitionRuntime::new(server.active_policy_mode());
    run_server_loop(
        &mut listener,
        &server,
        &shutdown_flag,
        state,
        control_rx,
        policy_challenge,
        &transition_runtime,
    )
}

fn run_server_loop(
    listener: &mut TcpLoopbackTransport,
    server: &McpServer,
    shutdown_flag: &Arc<AtomicBool>,
    state: ServerState,
    control_rx: ControlReceiver,
    policy_challenge: Arc<String>,
    transition_runtime: &TransitionRuntime,
) -> Result<(), SessionBridgeError> {
    let mut state = state;
    while let Some(next_state) = run_server_iteration(
        listener,
        server,
        shutdown_flag,
        state,
        &control_rx,
        policy_challenge.as_str(),
        transition_runtime,
    ) {
        state = next_state;
    }
    Ok(())
}

fn classify_iteration_accept(
    listener: &mut TcpLoopbackTransport,
    control_rx: &ControlReceiver,
    shutdown_flag: &Arc<AtomicBool>,
    policy_challenge: &str,
    server: &McpServer,
    transition_runtime: &TransitionRuntime,
) -> Option<AcceptClass> {
    should_continue_server_loop(
        control_rx,
        shutdown_flag,
        policy_challenge,
        server,
        transition_runtime,
    )
    .then(|| classify_accept(listener.accept()))
}

fn continue_state_from_outcome(
    outcome: AcceptOutcome,
    new_state: ServerState,
) -> Option<ServerState> {
    matches!(outcome, AcceptOutcome::Continue).then_some(new_state)
}

fn run_server_iteration(
    listener: &mut TcpLoopbackTransport,
    server: &McpServer,
    shutdown_flag: &Arc<AtomicBool>,
    state: ServerState,
    control_rx: &ControlReceiver,
    policy_challenge: &str,
    transition_runtime: &TransitionRuntime,
) -> Option<ServerState> {
    let cls = classify_iteration_accept(
        listener,
        control_rx,
        shutdown_flag,
        policy_challenge,
        server,
        transition_runtime,
    )?;
    let (outcome, new_state) = execute_accept_action(
        cls,
        server,
        shutdown_flag,
        state,
        control_rx,
        policy_challenge,
        transition_runtime,
    );
    continue_state_from_outcome(outcome, new_state)
}

fn should_continue_server_loop(
    control_rx: &ControlReceiver,
    shutdown_flag: &Arc<AtomicBool>,
    policy_challenge: &str,
    server: &McpServer,
    transition_runtime: &TransitionRuntime,
) -> bool {
    process_control_messages(
        control_rx,
        shutdown_flag,
        policy_challenge,
        server,
        transition_runtime,
    );
    !is_shutdown(shutdown_flag)
}

fn parse_mode_switch_target(mode: &str) -> Result<PolicyMode, ControlError> {
    PolicyMode::try_from_str(mode)
        .ok_or_else(|| ControlError::Rejected(format!("unknown policy mode: {}", mode)))
}

fn apply_mode_switch(
    server: &McpServer,
    transition_runtime: &TransitionRuntime,
    mode: &str,
    requester_id: &str,
    requester_context: Option<&str>,
) -> ControlResult {
    let next_mode = parse_mode_switch_target(mode)?;
    let (old_mode, committed_mode, transition_index) = transition_runtime.transition_to(next_mode);
    server.switch_policy_mode(committed_mode);
    server.emit_mode_transition_audit(
        old_mode,
        committed_mode,
        requester_id,
        requester_context,
        transition_index,
    );
    tracing::info!(
        old_mode = ?old_mode,
        new_mode = ?committed_mode,
        requester_id = %requester_id,
        transition_index,
        "Serialized mode switch committed"
    );
    Ok(())
}

type ControlExecutor =
    fn(&ControlRequest, &Arc<AtomicBool>, &McpServer, &TransitionRuntime) -> ControlResult;

fn execute_mode_switch_request(
    request: &ControlRequest,
    _shutdown_flag: &Arc<AtomicBool>,
    server: &McpServer,
    transition_runtime: &TransitionRuntime,
) -> ControlResult {
    match &request.command {
        ControlCommand::ModeSwitch { mode } => apply_mode_switch(
            server,
            transition_runtime,
            mode.as_str(),
            request.requester_id.as_str(),
            request.requester_context.as_deref(),
        ),
        _ => Err(ControlError::Rejected(
            "mode switch executor received non-mode-switch command".to_string(),
        )),
    }
}

fn execute_shutdown_request(
    _request: &ControlRequest,
    shutdown_flag: &Arc<AtomicBool>,
    _server: &McpServer,
    _transition_runtime: &TransitionRuntime,
) -> ControlResult {
    shutdown_flag.store(true, Ordering::Release);
    Ok(())
}

fn execute_heartbeat_ack_request(
    _request: &ControlRequest,
    _shutdown_flag: &Arc<AtomicBool>,
    _server: &McpServer,
    _transition_runtime: &TransitionRuntime,
) -> ControlResult {
    tracing::trace!("Received heartbeat ack from orchestrator");
    Ok(())
}

fn command_executor(command: &ControlCommand) -> ControlExecutor {
    match command {
        ControlCommand::ModeSwitch { .. } => execute_mode_switch_request,
        ControlCommand::Shutdown => execute_shutdown_request,
        ControlCommand::HeartbeatAck => execute_heartbeat_ack_request,
    }
}

fn apply_control_command(
    request: &ControlRequest,
    shutdown_flag: &Arc<AtomicBool>,
    server: &McpServer,
    transition_runtime: &TransitionRuntime,
) -> ControlResult {
    command_executor(&request.command)(request, shutdown_flag, server, transition_runtime)
}

fn process_one_control_message(
    request: &ControlRequest,
    shutdown_flag: &Arc<AtomicBool>,
    policy_challenge: &str,
    server: &McpServer,
    transition_runtime: &TransitionRuntime,
) {
    let result = validate_control_challenge(request.challenge.as_str(), policy_challenge)
        .and_then(|()| apply_control_command(request, shutdown_flag, server, transition_runtime));
    let _ = request.response.send(result);
}

fn process_control_messages(
    control_rx: &ControlReceiver,
    shutdown_flag: &Arc<AtomicBool>,
    policy_challenge: &str,
    server: &McpServer,
    transition_runtime: &TransitionRuntime,
) {
    while let Ok(request) = control_rx.try_recv() {
        process_one_control_message(
            &request,
            shutdown_flag,
            policy_challenge,
            server,
            transition_runtime,
        );
    }
}

#[cfg(test)]
#[path = "session_bridge/tests.rs"]
mod tests;
