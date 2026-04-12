//! Validation error transport tests.
//!
//! Tests that validation errors from tool handlers are properly formatted
//! as machine-parseable JSON error responses through the JSON-RPC transport.

use crate::agents::session::{AgentSession, SessionDrain};
use crate::mcp_server::tool_bridge::{
    build_ralph_tool_registry, RalphHostSessionAdapter, RalphWorkspaceAdapter,
};
use crate::workspace::memory_workspace::MemoryWorkspace;
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::{McpServer, ServerState};
use mcp_server::protocol::types::JsonRpcRequest;
use std::sync::Arc;

fn development_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
}

fn test_workspace() -> Arc<dyn crate::workspace::Workspace> {
    Arc::new(MemoryWorkspace::new_test())
}

fn setup_server() -> McpServer {
    let session = Arc::new(development_session());
    let workspace: Arc<dyn crate::workspace::Workspace> = test_workspace();
    let registry = build_ralph_tool_registry(Arc::clone(&session), Arc::clone(&workspace));
    let config = McpServerConfig::new(std::env::temp_dir());
    let host = RalphHostSessionAdapter::new(Arc::clone(&session));
    let ws = RalphWorkspaceAdapter::new(Arc::clone(&workspace));

    McpServer::new(Arc::new(host), config, Arc::new(ws), registry, None)
}

#[test]
fn tools_call_returns_machine_parseable_validation_payload() {
    let server = setup_server();

    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({
            "protocolVersion": "2024-11-05"
        })),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    let invalid_plan = serde_json::json!({
        "summary": {
            "context": "transport test",
            "scope_items": [
                {"text": "a"},
                {"text": "b"},
                {"text": "c"}
            ]
        },
        "steps": [
            {
                "number": 1,
                "content": "missing title"
            }
        ],
        "critical_files": {
            "primary_files": [
                {"path": "src/example.rs", "action": "modify"}
            ]
        },
        "risks_mitigations": [
            {"risk": "r", "mitigation": "m"}
        ],
        "verification_strategy": [
            {"method": "cargo test", "expected_outcome": "pass"}
        ]
    });

    let call_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "ralph_submit_artifact",
            "arguments": {
                "artifact_type": "plan",
                "content": serde_json::to_string(&invalid_plan).expect("serialize invalid_plan")
            }
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _state) = server.handle_request(call_request, ServerState::Ready);

    // The response should indicate an error
    let response = response.expect("tools/call should return a response for non-notification");
    let result = response
        .result
        .expect("tools/call result should be present");

    assert_eq!(
        result["isError"],
        serde_json::json!(true),
        "validation failure must return isError:true, got: {result}"
    );

    let content_text = result["content"][0]["text"]
        .as_str()
        .expect("content[0].text must be a string");
    let payload: serde_json::Value =
        serde_json::from_str(content_text).expect("content text must be valid JSON");

    assert_eq!(payload["artifactType"], "plan");
    assert!(payload["errors"].is_array());
    assert!(
        payload["errors"]
            .as_array()
            .expect("errors array")
            .iter()
            .any(|err| err["fieldPath"] == "steps[0].title"),
        "expected steps[0].title path in payload errors: {}",
        payload
    );
}
