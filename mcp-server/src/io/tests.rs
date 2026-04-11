use super::*;
use crate::dispatch::access::{AccessDecision, McpCapability};
use crate::dispatch::host::DirEntry;
use crate::dispatch::{ToolHandler, ToolMetadata, ToolRegistry};
use crate::io::DrainClass;
use crate::protocol::{ToolContent, ToolDefinition, ToolResult};
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

struct MockSession;
impl HostSession for MockSession {
    fn session_id(&self) -> &str {
        "test-session"
    }
    fn run_id(&self) -> &str {
        "test-run"
    }
    fn check_capability(&self, cap: McpCapability) -> AccessDecision {
        if cap == McpCapability::WorkspaceRead {
            AccessDecision::Allow
        } else {
            AccessDecision::Deny {
                reason: format!("Missing capability: {}", cap),
                code: crate::dispatch::access::AccessDeniedCode::CapabilityDenied,
            }
        }
    }
}

struct MockWorkspace;
impl WorkspaceAdapter for MockWorkspace {
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

struct CountingAllowSession {
    capability_checks: Arc<AtomicUsize>,
}

impl CountingAllowSession {
    fn new(capability_checks: Arc<AtomicUsize>) -> Self {
        Self { capability_checks }
    }
}

impl HostSession for CountingAllowSession {
    fn session_id(&self) -> &str {
        "counting-allow-session"
    }

    fn run_id(&self) -> &str {
        "counting-allow-run"
    }

    fn check_capability(&self, _cap: McpCapability) -> AccessDecision {
        self.capability_checks.fetch_add(1, Ordering::SeqCst);
        AccessDecision::Allow
    }
}

struct PermissiveSession;

impl HostSession for PermissiveSession {
    fn session_id(&self) -> &str {
        "permissive-session"
    }

    fn run_id(&self) -> &str {
        "permissive-run"
    }

    fn check_capability(&self, _cap: McpCapability) -> AccessDecision {
        AccessDecision::Allow
    }
}

struct CountingDenySession {
    capability_checks: Arc<AtomicUsize>,
}

impl CountingDenySession {
    fn new(capability_checks: Arc<AtomicUsize>) -> Self {
        Self { capability_checks }
    }
}

impl HostSession for CountingDenySession {
    fn session_id(&self) -> &str {
        "counting-deny-session"
    }

    fn run_id(&self) -> &str {
        "counting-deny-run"
    }

    fn check_capability(&self, cap: McpCapability) -> AccessDecision {
        self.capability_checks.fetch_add(1, Ordering::SeqCst);
        AccessDecision::Deny {
            reason: format!("Missing capability: {}", cap),
            code: crate::dispatch::access::AccessDeniedCode::CapabilityDenied,
        }
    }
}

fn initialize(server: &McpServer) -> ServerState {
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({ "protocolVersion": "2024-11-05" })),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);
    state
}

fn call_tool(
    server: &McpServer,
    state: ServerState,
    name: &str,
    arguments: serde_json::Value,
) -> JsonRpcResponse {
    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": name,
            "arguments": arguments,
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(request, state);
    response.expect("tools/call should return a response")
}

#[test]
fn test_server_initialization() {
    let session = Arc::new(MockSession) as Arc<dyn HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);
    let config = crate::io::access::McpServerConfig::new(std::env::temp_dir());
    let server = McpServer::new(session, config, workspace, registry, None);

    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({
            "protocolVersion": "2024-11-05",
            "clientInfo": { "name": "test", "version": "1.0" }
        })),
        id: Some(serde_json::json!(1)),
    };

    let (response, state) = server.handle_request(request, ServerState::Uninitialized);
    let response = response.expect("initialize should return a response");
    assert!(response.result.is_some());
    assert!(response.error.is_none());
    assert_eq!(state, ServerState::Ready);
}

#[test]
fn test_tools_list() {
    let session = Arc::new(MockSession) as Arc<dyn HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);
    let config = crate::io::access::McpServerConfig::new(std::env::temp_dir());
    let server = McpServer::new(session, config, workspace, registry, None);

    // Initialize first
    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({ "protocolVersion": "2024-11-05" })),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    // List tools
    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(request, state);
    let response = response.expect("tools/list should return a response");
    assert!(response.result.is_some());
}

#[test]
fn test_tools_list_respects_tool_filter() {
    let session = Arc::new(MockSession) as Arc<dyn HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
    let handler: ToolHandler = Arc::new(|_, _, _| {
        Ok(ToolResult {
            content: vec![ToolContent::text("ok")],
            is_error: Some(false),
        })
    });
    let registry = ToolRegistry::new(vec![
        (
            ToolMetadata {
                definition: ToolDefinition {
                    name: "read_file".to_string(),
                    description: "read".to_string(),
                    input_schema: serde_json::json!({"type": "object"}),
                },
                required_capability: McpCapability::WorkspaceRead,
                is_mutating: None,
            },
            Arc::clone(&handler),
        ),
        (
            ToolMetadata {
                definition: ToolDefinition {
                    name: "write_file".to_string(),
                    description: "write".to_string(),
                    input_schema: serde_json::json!({"type": "object"}),
                },
                required_capability: McpCapability::WorkspaceWriteTracked,
                is_mutating: None,
            },
            handler,
        ),
    ]);
    let config = crate::io::access::McpServerConfig::new(std::env::temp_dir()).with_tool_filter(
        crate::dispatch::access::ToolFilter::Allowlist(vec!["read_file".to_string()]),
    );
    let server = McpServer::new(session, config, workspace, registry, None);

    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({ "protocolVersion": "2024-11-05" })),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(request, state);
    let response = response.expect("tools/list should return a response");
    let result = response.result.expect("tools/list should return result");
    let tool_names: Vec<&str> = result
        .get("tools")
        .and_then(|tools| tools.as_array())
        .expect("tools/list result should contain array")
        .iter()
        .filter_map(|tool| tool.get("name").and_then(|name| name.as_str()))
        .collect();

    assert_eq!(tool_names, vec!["read_file"]);
}

#[test]
fn private_control_rpc_is_not_exposed_through_mcp_method_namespace() {
    let session = Arc::new(MockSession) as Arc<dyn HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);
    let config = crate::io::access::McpServerConfig::new(std::env::temp_dir());
    let server = McpServer::new(session, config, workspace, registry, None);

    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({ "protocolVersion": "2024-11-05" })),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    let request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "private/control".to_string(),
        params: Some(serde_json::json!({ "command": "shutdown" })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(request, state);
    let response = response.expect("unknown method should return error response");
    let error = response.error.expect("unknown method should return error");
    assert_eq!(error.code, -32601, "private RPC must not be an MCP method");
}

#[test]
fn requests_admitted_after_mode_transition_commit_use_new_policy_mode() {
    struct PermissiveSession;
    impl HostSession for PermissiveSession {
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

    let session = Arc::new(PermissiveSession) as Arc<dyn HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
    let write_handler: ToolHandler = Arc::new(|_, _, _| {
        Ok(ToolResult {
            content: vec![ToolContent::text("wrote")],
            is_error: Some(false),
        })
    });
    let registry = ToolRegistry::new(vec![(
        ToolMetadata {
            definition: ToolDefinition {
                name: "write_file".to_string(),
                description: "write".to_string(),
                input_schema: serde_json::json!({"type":"object"}),
            },
            required_capability: McpCapability::WorkspaceWriteTracked,
            is_mutating: Some(true),
        },
        write_handler,
    )]);
    let config = crate::io::access::McpServerConfig::new(std::env::temp_dir())
        .with_access_mode(crate::dispatch::access::AccessMode::ReadOnly)
        .with_policy_mode(PolicyMode::ReadOnly);
    let server = McpServer::new(session, config, workspace, registry, None);

    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({ "protocolVersion": "2024-11-05" })),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init_request, ServerState::Uninitialized);

    let write_request_before = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "write_file",
            "arguments": { "path": "x.txt", "content": "before" }
        })),
        id: Some(serde_json::json!(2)),
    };
    let (before_resp, state) = server.handle_request(write_request_before, state);
    let before_error = before_resp
        .expect("response expected")
        .error
        .expect("read-only mode should deny mutating request");
    assert_eq!(before_error.code, -32000);

    server.switch_policy_mode(PolicyMode::Dev);

    let write_request_after = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "write_file",
            "arguments": { "path": "x.txt", "content": "after" }
        })),
        id: Some(serde_json::json!(3)),
    };
    let (after_resp, _) = server.handle_request(write_request_after, state);
    let after = after_resp.expect("response expected after transition");
    assert!(
        after.error.is_none(),
        "request admitted after transition commit must evaluate under new policy"
    );
}

#[test]
fn successful_tool_call_checks_capability_once_at_dispatch_boundary() {
    let capability_checks = Arc::new(AtomicUsize::new(0));
    let session =
        Arc::new(CountingAllowSession::new(Arc::clone(&capability_checks))) as Arc<dyn HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
    let handler: ToolHandler =
        Arc::new(|_, _, _| Ok(ToolResult::success(vec![ToolContent::text("ok")])));
    let registry = ToolRegistry::new(vec![(
        ToolMetadata {
            definition: ToolDefinition {
                name: "read_file".to_string(),
                description: "read".to_string(),
                input_schema: serde_json::json!({"type": "object"}),
            },
            required_capability: McpCapability::WorkspaceRead,
            is_mutating: Some(false),
        },
        handler,
    )]);
    let server = McpServer::new(
        session,
        crate::io::access::McpServerConfig::new(std::env::temp_dir())
            .with_policy_mode(PolicyMode::Dev)
            .with_drain("development".to_string())
            .with_drain_class(DrainClass::Dev),
        workspace,
        registry,
        None,
    );

    let response = call_tool(
        &server,
        initialize(&server),
        "read_file",
        serde_json::json!({ "path": "src/lib.rs" }),
    );

    assert!(
        response.error.is_none(),
        "capability gate should admit read tool"
    );
    assert_eq!(
        capability_checks.load(Ordering::SeqCst),
        1,
        "tools/call should consult the host capability gate exactly once"
    );
}

#[test]
fn commit_mode_denial_payload_includes_mode_drain_and_operation_class() {
    let handler_calls = Arc::new(AtomicUsize::new(0));
    let handler_call_counter = Arc::clone(&handler_calls);
    let session = Arc::new(MockSession) as Arc<dyn HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
    let handler: ToolHandler = Arc::new(move |_, _, _| {
        handler_call_counter.fetch_add(1, Ordering::SeqCst);
        Ok(ToolResult::success(vec![ToolContent::text("wrote")]))
    });
    let registry = ToolRegistry::new(vec![(
        ToolMetadata {
            definition: ToolDefinition {
                name: "write_file".to_string(),
                description: "write".to_string(),
                input_schema: serde_json::json!({"type": "object"}),
            },
            required_capability: McpCapability::WorkspaceWriteTracked,
            is_mutating: Some(true),
        },
        handler,
    )]);
    let config = crate::io::access::McpServerConfig::new(std::env::temp_dir())
        .with_access_mode(crate::dispatch::access::AccessMode::ReadOnly)
        .with_policy_mode(PolicyMode::Commit)
        .with_drain("commit".to_string())
        .with_drain_class(DrainClass::Commit);
    let server = McpServer::new(session, config, workspace, registry, None);

    let response = call_tool(
        &server,
        initialize(&server),
        "write_file",
        serde_json::json!({ "path": "Cargo.toml", "content": "x" }),
    );

    let error = response
        .error
        .expect("commit mode should deny tracked edits");
    assert_eq!(
        error.message,
        "Access denied: PolicyMode::Commit denies Edit operations"
    );
    let data = error
        .data
        .expect("access denial should include structured payload");
    assert_eq!(
        data.get("reason").and_then(serde_json::Value::as_str),
        Some("PolicyMode::Commit denies Edit operations")
    );
    assert_eq!(
        data.get("code").and_then(serde_json::Value::as_str),
        Some("ReadOnlyMode")
    );
    assert_eq!(
        data.get("mode").and_then(serde_json::Value::as_str),
        Some("Commit")
    );
    assert_eq!(
        data.get("drain").and_then(serde_json::Value::as_str),
        Some("commit")
    );
    assert_eq!(
        data.get("opClass").and_then(serde_json::Value::as_str),
        Some("Edit")
    );
    assert_eq!(
        handler_calls.load(Ordering::SeqCst),
        0,
        "denied requests must not invoke the tool handler"
    );
}

#[test]
fn commit_mode_allows_artifact_submission_tool_calls() {
    let handler_calls = Arc::new(AtomicUsize::new(0));
    let handler_call_counter = Arc::clone(&handler_calls);
    let session = Arc::new(PermissiveSession) as Arc<dyn HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
    let handler: ToolHandler = Arc::new(move |_, _, _| {
        handler_call_counter.fetch_add(1, Ordering::SeqCst);
        Ok(ToolResult::success(vec![ToolContent::text("submitted")]))
    });
    let registry = ToolRegistry::new(vec![(
        ToolMetadata {
            definition: ToolDefinition {
                name: "ralph_submit_artifact".to_string(),
                description: "submit artifact".to_string(),
                input_schema: serde_json::json!({"type": "object"}),
            },
            required_capability: McpCapability::ArtifactSubmit,
            is_mutating: Some(false),
        },
        handler,
    )]);
    let config = crate::io::access::McpServerConfig::new(std::env::temp_dir())
        .with_access_mode(crate::dispatch::access::AccessMode::ReadOnly)
        .with_policy_mode(PolicyMode::Commit)
        .with_drain("commit".to_string())
        .with_drain_class(DrainClass::Commit);
    let server = McpServer::new(session, config, workspace, registry, None);

    let response = call_tool(
        &server,
        initialize(&server),
        "ralph_submit_artifact",
        serde_json::json!({ "artifact": "commit message" }),
    );

    assert!(
        response.error.is_none(),
        "commit mode should allow artifact submission tools"
    );
    assert_eq!(
        handler_calls.load(Ordering::SeqCst),
        1,
        "allowed artifact submission must invoke the tool handler"
    );
}

#[test]
fn commit_mode_denies_git_write_with_commit_policy_payload_and_no_side_effects() {
    let handler_calls = Arc::new(AtomicUsize::new(0));
    let handler_call_counter = Arc::clone(&handler_calls);
    let session = Arc::new(PermissiveSession) as Arc<dyn HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
    let handler: ToolHandler = Arc::new(move |_, _, _| {
        handler_call_counter.fetch_add(1, Ordering::SeqCst);
        Ok(ToolResult::success(vec![ToolContent::text("committed")]))
    });
    let registry = ToolRegistry::new(vec![(
        ToolMetadata {
            definition: ToolDefinition {
                name: "git_commit".to_string(),
                description: "git commit".to_string(),
                input_schema: serde_json::json!({"type": "object"}),
            },
            required_capability: McpCapability::GitWrite,
            is_mutating: Some(true),
        },
        handler,
    )]);
    let config = crate::io::access::McpServerConfig::new(std::env::temp_dir())
        .with_access_mode(crate::dispatch::access::AccessMode::ReadOnly)
        .with_policy_mode(PolicyMode::Commit)
        .with_drain("commit".to_string())
        .with_drain_class(DrainClass::Commit);
    let server = McpServer::new(session, config, workspace, registry, None);

    let response = call_tool(
        &server,
        initialize(&server),
        "git_commit",
        serde_json::json!({ "message": "ship it" }),
    );

    let error = response
        .error
        .expect("commit mode should deny git write operations");
    assert_eq!(
        error.message,
        "Access denied: PolicyMode::Commit denies GitCommit operations"
    );
    let data = error
        .data
        .expect("git write denial should include structured payload");
    assert_eq!(
        data.get("reason").and_then(serde_json::Value::as_str),
        Some("PolicyMode::Commit denies GitCommit operations")
    );
    assert_eq!(
        data.get("code").and_then(serde_json::Value::as_str),
        Some("ReadOnlyMode")
    );
    assert_eq!(
        data.get("mode").and_then(serde_json::Value::as_str),
        Some("Commit")
    );
    assert_eq!(
        data.get("drain").and_then(serde_json::Value::as_str),
        Some("commit")
    );
    assert_eq!(
        data.get("opClass").and_then(serde_json::Value::as_str),
        Some("GitCommit")
    );
    assert_eq!(
        handler_calls.load(Ordering::SeqCst),
        0,
        "denied git write requests must not invoke the tool handler"
    );
}

#[test]
fn capability_denial_payload_includes_mode_drain_and_operation_class_without_side_effects() {
    let capability_checks = Arc::new(AtomicUsize::new(0));
    let handler_calls = Arc::new(AtomicUsize::new(0));
    let handler_call_counter = Arc::clone(&handler_calls);
    let session =
        Arc::new(CountingDenySession::new(Arc::clone(&capability_checks))) as Arc<dyn HostSession>;
    let workspace = Arc::new(MockWorkspace) as Arc<dyn WorkspaceAdapter>;
    let handler: ToolHandler = Arc::new(move |_, _, _| {
        handler_call_counter.fetch_add(1, Ordering::SeqCst);
        Ok(ToolResult::success(vec![ToolContent::text("read")]))
    });
    let registry = ToolRegistry::new(vec![(
        ToolMetadata {
            definition: ToolDefinition {
                name: "read_file".to_string(),
                description: "read".to_string(),
                input_schema: serde_json::json!({"type": "object"}),
            },
            required_capability: McpCapability::WorkspaceRead,
            is_mutating: Some(false),
        },
        handler,
    )]);
    let server = McpServer::new(
        session,
        crate::io::access::McpServerConfig::new(std::env::temp_dir())
            .with_policy_mode(PolicyMode::ReadOnly)
            .with_drain("planning".to_string())
            .with_drain_class(DrainClass::Planning),
        workspace,
        registry,
        None,
    );

    let response = call_tool(
        &server,
        initialize(&server),
        "read_file",
        serde_json::json!({ "path": "src/lib.rs" }),
    );

    let error = response
        .error
        .expect("host capability denial should return tool error");
    let data = error
        .data
        .expect("capability denial should include structured payload");
    assert_eq!(
        data.get("code").and_then(serde_json::Value::as_str),
        Some("CapabilityDenied")
    );
    assert_eq!(
        data.get("mode").and_then(serde_json::Value::as_str),
        Some("ReadOnly")
    );
    assert_eq!(
        data.get("drain").and_then(serde_json::Value::as_str),
        Some("planning")
    );
    assert_eq!(
        data.get("opClass").and_then(serde_json::Value::as_str),
        Some("Read")
    );
    assert_eq!(
        capability_checks.load(Ordering::SeqCst),
        1,
        "capability denial should come from the centralized pre-dispatch gate"
    );
    assert_eq!(
        handler_calls.load(Ordering::SeqCst),
        0,
        "capability-denied requests must not invoke the tool handler"
    );
}
