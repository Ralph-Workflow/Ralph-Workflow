use super::*;
use crate::dispatch::access::{AccessDecision, McpCapability};
use crate::dispatch::audit::AuditEventType;
use crate::dispatch::host::DirEntry;
use std::path::Path;

struct TestSession;
impl HostSession for TestSession {
    fn session_id(&self) -> &str {
        "test-session"
    }
    fn run_id(&self) -> &str {
        "test-run"
    }
    fn check_capability(&self, _cap: McpCapability) -> AccessDecision {
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

fn test_server_with_sink(
    initial_mode: PolicyMode,
) -> (McpServer, Arc<crate::io::access::InMemoryAuditSink>) {
    let session = Arc::new(TestSession) as Arc<dyn HostSession>;
    let workspace = Arc::new(TestWorkspace) as Arc<dyn WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);
    let sink = Arc::new(crate::io::access::InMemoryAuditSink::new(100));
    let config = McpServerConfig::new(std::env::temp_dir())
        .with_session_id("test-session".to_string())
        .with_run_id("test-run".to_string())
        .with_generation(1)
        .with_drain("development".to_string())
        .with_policy_mode(initial_mode);
    let server = McpServer::new(session, config, workspace, registry, Some(sink.clone()));
    (server, sink)
}

#[test]
fn test_endpoint_uri() {
    let session = Arc::new(TestSession) as Arc<dyn HostSession>;
    let workspace = Arc::new(TestWorkspace) as Arc<dyn WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);
    let config = McpServerConfig::new(std::env::temp_dir());
    let mut bridge = SessionBridge::new(session, config, workspace, registry);
    bridge.start().expect("bridge should start");

    let uri = bridge.endpoint_uri();
    assert!(uri.starts_with("tcp://127.0.0.1:"));
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

#[test]
fn policy_challenge_is_exactly_256_chars() {
    let challenge = generate_policy_challenge();
    assert_eq!(challenge.chars().count(), 256);
}

#[test]
fn process_control_messages_denies_missing_challenge() {
    let (request_tx, request_rx) = mpsc::channel::<ControlRequest>();
    let shutdown_flag = Arc::new(AtomicBool::new(false));
    let (response_tx, response_rx) = mpsc::channel::<ControlResult>();
    let (server, _sink) = test_server_with_sink(PolicyMode::ReadOnly);
    let transition_runtime = TransitionRuntime::new(PolicyMode::ReadOnly);

    request_tx
        .send(ControlRequest {
            challenge: String::new(),
            command: ControlCommand::ModeSwitch {
                mode: "dev".to_string(),
            },
            requester_id: "orchestrator".to_string(),
            requester_context: Some("{}".to_string()),
            response: response_tx,
        })
        .expect("request send should succeed");

    process_control_messages(
        &request_rx,
        &shutdown_flag,
        &"a".repeat(256),
        &server,
        &transition_runtime,
    );

    let response = response_rx
        .recv()
        .expect("response should be returned for denied request");
    assert!(
        matches!(
            response,
            Err(ControlError::AccessDenied(msg)) if msg == "missing policy challenge"
        ),
        "missing challenge should be rejected with explicit reason"
    );
}

#[test]
fn process_control_messages_denies_non_256_challenge_length() {
    let (request_tx, request_rx) = mpsc::channel::<ControlRequest>();
    let shutdown_flag = Arc::new(AtomicBool::new(false));
    let (response_tx, response_rx) = mpsc::channel::<ControlResult>();
    let (server, _sink) = test_server_with_sink(PolicyMode::ReadOnly);
    let transition_runtime = TransitionRuntime::new(PolicyMode::ReadOnly);

    request_tx
        .send(ControlRequest {
            challenge: "too-short".to_string(),
            command: ControlCommand::HeartbeatAck,
            requester_id: "orchestrator".to_string(),
            requester_context: Some("{}".to_string()),
            response: response_tx,
        })
        .expect("request send should succeed");

    process_control_messages(
        &request_rx,
        &shutdown_flag,
        &"b".repeat(256),
        &server,
        &transition_runtime,
    );

    let response = response_rx
        .recv()
        .expect("response should be returned for denied request");
    assert!(
        matches!(
            response,
            Err(ControlError::AccessDenied(msg))
                if msg == "invalid policy challenge length: expected 256"
        ),
        "non-256 challenge should be rejected with explicit reason"
    );
}

#[test]
fn process_control_messages_accepts_orchestrator_request_with_valid_challenge() {
    let challenge = "z".repeat(256);
    let (request_tx, request_rx) = mpsc::channel::<ControlRequest>();
    let shutdown_flag = Arc::new(AtomicBool::new(false));
    let (response_tx, response_rx) = mpsc::channel::<ControlResult>();
    let (server, _sink) = test_server_with_sink(PolicyMode::ReadOnly);
    let transition_runtime = TransitionRuntime::new(PolicyMode::ReadOnly);

    request_tx
        .send(ControlRequest {
            challenge: challenge.clone(),
            command: ControlCommand::Shutdown,
            requester_id: "orchestrator".to_string(),
            requester_context: Some("{}".to_string()),
            response: response_tx,
        })
        .expect("request send should succeed");

    process_control_messages(
        &request_rx,
        &shutdown_flag,
        challenge.as_str(),
        &server,
        &transition_runtime,
    );

    let response = response_rx
        .recv()
        .expect("response should be returned for accepted request");
    assert!(
        response.is_ok(),
        "valid orchestrator request should succeed"
    );
    assert!(
        shutdown_flag.load(Ordering::Acquire),
        "shutdown command should flip shutdown flag"
    );
}

#[test]
fn process_control_messages_rejects_unknown_mode_switch_values() {
    let challenge = "q".repeat(256);
    let (request_tx, request_rx) = mpsc::channel::<ControlRequest>();
    let shutdown_flag = Arc::new(AtomicBool::new(false));
    let (response_tx, response_rx) = mpsc::channel::<ControlResult>();
    let (server, _sink) = test_server_with_sink(PolicyMode::ReadOnly);
    let transition_runtime = TransitionRuntime::new(PolicyMode::ReadOnly);

    request_tx
        .send(ControlRequest {
            challenge: challenge.clone(),
            command: ControlCommand::ModeSwitch {
                mode: "definitely-not-a-policy-mode".to_string(),
            },
            requester_id: "orchestrator".to_string(),
            requester_context: Some("{\"scope\":\"private\"}".to_string()),
            response: response_tx,
        })
        .expect("request send should succeed");

    process_control_messages(
        &request_rx,
        &shutdown_flag,
        challenge.as_str(),
        &server,
        &transition_runtime,
    );

    let response = response_rx
        .recv()
        .expect("response should be returned for mode switch request");
    assert!(
        matches!(response, Err(ControlError::Rejected(msg)) if msg.contains("unknown policy mode")),
        "unexpected mode values must be rejected to avoid ambiguous transitions"
    );
}

#[test]
fn process_control_messages_serializes_mode_transitions_and_emits_audit_payload() {
    let challenge = "m".repeat(256);
    let (request_tx, request_rx) = mpsc::channel::<ControlRequest>();
    let shutdown_flag = Arc::new(AtomicBool::new(false));
    let (response_tx_one, response_rx_one) = mpsc::channel::<ControlResult>();
    let (response_tx_two, response_rx_two) = mpsc::channel::<ControlResult>();
    let (server, sink) = test_server_with_sink(PolicyMode::ReadOnly);
    let transition_runtime = TransitionRuntime::new(PolicyMode::ReadOnly);

    request_tx
        .send(ControlRequest {
            challenge: challenge.clone(),
            command: ControlCommand::ModeSwitch {
                mode: "dev".to_string(),
            },
            requester_id: "orchestrator-a".to_string(),
            requester_context: Some("{\"source\":\"test\",\"request_id\":1}".to_string()),
            response: response_tx_one,
        })
        .expect("first request send should succeed");
    request_tx
        .send(ControlRequest {
            challenge: challenge.clone(),
            command: ControlCommand::ModeSwitch {
                mode: "commit".to_string(),
            },
            requester_id: "orchestrator-b".to_string(),
            requester_context: Some("{\"source\":\"test\",\"request_id\":2}".to_string()),
            response: response_tx_two,
        })
        .expect("second request send should succeed");

    process_control_messages(
        &request_rx,
        &shutdown_flag,
        challenge.as_str(),
        &server,
        &transition_runtime,
    );

    assert!(response_rx_one.recv().expect("first response").is_ok());
    assert!(response_rx_two.recv().expect("second response").is_ok());
    assert_eq!(server.active_policy_mode(), PolicyMode::Commit);

    let records = sink.records();
    let transition_records: Vec<_> = records
        .iter()
        .filter(|record| record.metadata.event_type == AuditEventType::ModeTransition)
        .collect();
    assert_eq!(
        transition_records.len(),
        2,
        "each transition must emit audit"
    );

    let first_details = transition_records[0]
        .metadata
        .details
        .clone()
        .expect("first transition payload");
    assert!(first_details.contains("\"old_mode\":\"ReadOnly\""));
    assert!(first_details.contains("\"new_mode\":\"Dev\""));
    assert!(first_details.contains("\"requester_id\":\"orchestrator-a\""));

    let second_details = transition_records[1]
        .metadata
        .details
        .clone()
        .expect("second transition payload");
    assert!(second_details.contains("\"old_mode\":\"Dev\""));
    assert!(second_details.contains("\"new_mode\":\"Commit\""));
    assert!(second_details.contains("\"requester_id\":\"orchestrator-b\""));
}
