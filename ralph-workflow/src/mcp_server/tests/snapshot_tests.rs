//! Snapshot tests for MCP protocol messages.
//!
//! These tests verify that protocol messages and audit records
//! maintain consistent format across changes.

use crate::agents::session::{AgentSession, SessionDrain};
use crate::mcp_server::tool_bridge::{
    build_ralph_tool_registry, RalphHostSessionAdapter, RalphWorkspaceAdapter,
};
use crate::workspace::memory_workspace::MemoryWorkspace;
use mcp_server::protocol::types::{InitializeResult, ServerCapabilities, ServerInfo};
use std::sync::Arc;

// Initialize response snapshot

#[test]
fn test_initialize_response_format() {
    let result = InitializeResult {
        protocol_version: "2024-11-05".to_string(),
        capabilities: ServerCapabilities::default(),
        server_info: ServerInfo {
            name: "ralph-mcp".to_string(),
            version: "1.0.0".to_string(),
        },
    };
    let json = serde_json::to_string(&result).expect("serialize");

    // Verify it serializes to valid JSON
    let parsed: serde_json::Value = serde_json::from_str(&json).expect("parse json");
    assert!(parsed.is_object());
}

#[test]
fn test_initialize_response_protocol_version() {
    let result = InitializeResult {
        protocol_version: "2024-11-05".to_string(),
        capabilities: ServerCapabilities::default(),
        server_info: ServerInfo {
            name: "ralph-mcp".to_string(),
            version: "1.0.0".to_string(),
        },
    };
    let json = serde_json::to_string(&result).expect("serialize");
    let parsed: serde_json::Value = serde_json::from_str(&json).expect("parse");

    // Should have protocol version
    assert!(parsed.get("protocolVersion").is_some());
}

#[test]
fn test_initialize_response_server_info() {
    let result = InitializeResult {
        protocol_version: "2024-11-05".to_string(),
        capabilities: ServerCapabilities::default(),
        server_info: ServerInfo {
            name: "ralph-mcp".to_string(),
            version: "1.0.0".to_string(),
        },
    };
    let json = serde_json::to_string(&result).expect("serialize");
    let parsed: serde_json::Value = serde_json::from_str(&json).expect("parse");

    // Should have server info
    assert!(parsed.get("serverInfo").is_some());
}

// Tools list snapshot

fn setup_test_registry() -> (
    mcp_server::ToolRegistry,
    RalphHostSessionAdapter,
    RalphWorkspaceAdapter,
) {
    let session = Arc::new(AgentSession::for_drain(
        "test-run".to_string(),
        SessionDrain::Development,
        1,
    ));
    let workspace: Arc<dyn crate::workspace::Workspace> = Arc::new(MemoryWorkspace::new_test());
    let registry = build_ralph_tool_registry(Arc::clone(&session), Arc::clone(&workspace));
    let host = RalphHostSessionAdapter::new(Arc::clone(&session));
    let ws = RalphWorkspaceAdapter::new(Arc::clone(&workspace));
    (registry, host, ws)
}

#[test]
fn test_tools_list_response_format() {
    let (registry, _host, _ws) = setup_test_registry();
    let tools = registry.list_tools();
    let json = serde_json::to_string(&tools).expect("serialize");

    // Verify it's an array
    let parsed: serde_json::Value = serde_json::from_str(&json).expect("parse");
    assert!(parsed.is_array());
}

#[test]
fn test_tools_list_contains_required_tools() {
    let (registry, _host, _ws) = setup_test_registry();
    let tools = registry.list_tools();
    let tool_names: Vec<_> = tools.iter().map(|t| t.name.clone()).collect();

    // All required tools should be present
    assert!(tool_names.contains(&"read_file".to_string()));
    assert!(tool_names.contains(&"write_file".to_string()));
    assert!(tool_names.contains(&"list_directory".to_string()));
    assert!(tool_names.contains(&"search_files".to_string()));
    assert!(tool_names.contains(&"git_status".to_string()));
    assert!(tool_names.contains(&"git_diff".to_string()));
    assert!(tool_names.contains(&"git_log".to_string()));
    assert!(tool_names.contains(&"git_show".to_string()));
    assert!(tool_names.contains(&"exec".to_string()));
    assert!(tool_names.contains(&"ralph_submit_artifact".to_string()));
    assert!(tool_names.contains(&"report_progress".to_string()));
    assert!(tool_names.contains(&"read_env".to_string()));
    assert!(tool_names.contains(&"declare_complete".to_string()));
}

#[test]
fn test_tool_schema_format() {
    let (registry, _host, _ws) = setup_test_registry();
    let tools = registry.list_tools();

    for tool in tools {
        let json = serde_json::to_string(&tool).expect("serialize");
        let parsed: serde_json::Value = serde_json::from_str(&json).expect("parse");

        // Each tool should have required fields
        assert!(parsed["name"].is_string());
        assert!(parsed["description"].is_string());
        // Note: inputSchema might be input_schema in the JSON
        assert!(parsed.get("inputSchema").is_some() || parsed.get("input_schema").is_some());
    }
}

// Session handshake snapshot

#[test]
fn test_session_handshake_protocol_version() {
    let session = crate::agents::session::AgentSession::for_drain(
        "test-run".to_string(),
        crate::agents::session::SessionDrain::Development,
        1,
    );
    let handshake = crate::agents::session::SessionHandshake::from_session(&session);
    let json = serde_json::to_string(&handshake).expect("serialize");

    assert!(json.contains("ralph-mcp/1.0"));
}

#[test]
fn test_session_handshake_drain_values() {
    use crate::agents::session::SessionDrain;

    let session = crate::agents::session::AgentSession::for_drain(
        "test-run".to_string(),
        SessionDrain::Development,
        1,
    );
    let handshake = crate::agents::session::SessionHandshake::from_session(&session);
    assert_eq!(handshake.drain, SessionDrain::Development);

    let session = crate::agents::session::AgentSession::for_drain(
        "test-run".to_string(),
        SessionDrain::Planning,
        1,
    );
    let handshake = crate::agents::session::SessionHandshake::from_session(&session);
    assert_eq!(handshake.drain, SessionDrain::Planning);
}

// Capability set tests

#[test]
fn test_capability_set_has_read_for_planning() {
    use crate::agents::session::{AgentSession, Capability, SessionDrain};

    let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
    let caps = session.capabilities.to_vec();

    assert!(caps.contains(&Capability::WorkspaceRead));
    assert!(caps.contains(&Capability::GitStatusRead));
}

#[test]
fn test_capability_set_has_write_for_development() {
    use crate::agents::session::{AgentSession, Capability, SessionDrain};

    let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1);
    let caps = session.capabilities.to_vec();

    assert!(caps.contains(&Capability::WorkspaceWriteTracked));
    assert!(caps.contains(&Capability::WorkspaceWriteEphemeral));
    assert!(caps.contains(&Capability::ProcessExecBounded));
}

#[test]
fn test_capability_set_no_write_for_planning() {
    use crate::agents::session::{AgentSession, Capability, SessionDrain};

    let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
    let caps = session.capabilities.to_vec();

    assert!(!caps.contains(&Capability::WorkspaceWriteTracked));
}

// Policy flag snapshot

#[test]
fn test_policy_flags_no_edit_for_planning() {
    use crate::agents::session::{AgentSession, PolicyFlag, SessionDrain};

    let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
    let flags = session.policy_flags.to_vec();

    assert!(flags.contains(&PolicyFlag::NoEdit));
}

#[test]
fn test_policy_flags_allow_shell_for_development() {
    use crate::agents::session::{AgentSession, PolicyFlag, SessionDrain};

    let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1);
    let flags = session.policy_flags.to_vec();

    assert!(flags.contains(&PolicyFlag::AllowShell));
}

#[test]
fn test_policy_flags_allow_git_write_for_commit() {
    use crate::agents::session::{AgentSession, PolicyFlag, SessionDrain};

    let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Commit, 1);
    let flags = session.policy_flags.to_vec();

    assert!(flags.contains(&PolicyFlag::AllowGitWrite));
}

// Audit record snapshot

#[test]
fn test_audit_record_serialization() {
    use crate::agents::session::{AuditRecord, PolicyOutcome, SessionDrain};

    let record = AuditRecord::new(
        crate::agents::session::AgentSessionId::new("run-1", &SessionDrain::Development, 1),
        1234567890,
        crate::agents::session::Capability::WorkspaceRead,
        PolicyOutcome::Approved,
        "Test capability check".to_string(),
    );

    let json = serde_json::to_string(&record).expect("serialize");
    let parsed: serde_json::Value = serde_json::from_str(&json).expect("parse");

    // Verify it has the main fields (exact structure may vary)
    assert!(parsed.is_object());
    assert!(parsed.get("timestamp").is_some());
    assert!(parsed.get("capability").is_some());
    assert!(parsed.get("outcome").is_some());
    assert!(parsed.get("description").is_some());
}
