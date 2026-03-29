use crate::agents::session::{AgentSession, SessionDrain};
use crate::mcp_server::types::JsonRpcRequest;
use crate::workspace::{memory_workspace::MemoryWorkspace, Workspace};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

fn development_session() -> AgentSession {
    AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1)
}

fn workspace() -> Arc<dyn Workspace> {
    Arc::new(MemoryWorkspace::new_test())
}

#[test]
fn tools_call_returns_machine_parseable_validation_payload() {
    let shutdown_flag = Arc::new(AtomicBool::new(false));
    let mut server =
        super::super::McpServer::new(development_session(), workspace(), shutdown_flag);

    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({})),
        id: Some(serde_json::json!(1)),
    };
    let _ = server.process_request(init_request);

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

    let rpc_response = server
        .process_request(call_request)
        .expect("tools/call must return Some response");
    let result = rpc_response
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

    assert_eq!(payload["artifact_type"], "plan");
    assert!(payload["errors"].is_array());
    assert!(
        payload["errors"]
            .as_array()
            .expect("errors array")
            .iter()
            .any(|err| err["field_path"] == "steps[0].title"),
        "expected steps[0].title path in payload errors: {}",
        payload
    );
}
