//! Standalone integration tests for `mcp-server`.
//!
//! # Purpose
//!
//! These tests prove that `mcp-server` is independently testable without any
//! dependency on `ralph-workflow`. All host implementations are inline fakes —
//! no external crate types are imported.
//!
//! # Isolation Guarantee
//!
//! This file must not import any type from `ralph-workflow`. If a `ralph-workflow`
//! type accidentally appears (directly or via re-export), the compiler will produce
//! an unknown-import error, serving as a compile-time isolation proof.
//!
//! # Coverage
//!
//! Each enforcement-chain level is validated by a dedicated test:
//!
//! | Level | Test |
//! |-------|------|
//! | Config creation | `standalone_config_creates_without_ralph_workflow` |
//! | Initialize handshake | `full_stack_initialize_handshake_succeeds` |
//! | Ping liveness | `full_stack_ping_returns_null_and_preserves_state` |
//! | Allowed call (happy path) | `full_stack_allowed_tool_call_returns_success_result` |
//! | ReadOnly mode | `full_stack_readonly_mode_rejects_write_file` |
//! | Path boundary | `full_stack_root_dir_boundary_rejects_outside_paths` |
//! | Allowlist | `full_stack_allowlist_rejects_unlisted_tools` |
//! | Blocklist | `full_stack_blocklist_rejects_listed_tools` |
//! | Capability denial | `full_stack_capability_denial_propagates_error_code` |
//! | Audit: Allow + Deny | `full_stack_audit_sink_records_both_allow_and_deny_outcomes` |

use mcp_server::dispatch::access::{
    AccessDecision, AccessDeniedCode, AccessMode, AuditSink, McpCapability, ToolFilter,
};
use mcp_server::dispatch::audit::AuditRecord;
use mcp_server::dispatch::host::DirEntry;
use mcp_server::dispatch::{ToolHandler, ToolMetadata, ToolRegistry};
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::fake::FakeTransportPair;
use mcp_server::io::transport::McpStream;
use mcp_server::io::{McpServer, ServerState};
use mcp_server::protocol::{JsonRpcRequest, ToolContent, ToolDefinition, ToolResult};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, RwLock};

// ---------------------------------------------------------------------------
// Inline fake: HostSession
// ---------------------------------------------------------------------------

/// Minimal in-memory session for standalone testing.
///
/// Grants capabilities based on a simple allow/deny map. Does not depend on
/// any `ralph-workflow` type.
struct AlwaysAllowSession;

impl mcp_server::HostSession for AlwaysAllowSession {
    fn session_id(&self) -> &str {
        "standalone-test-session"
    }

    fn check_capability(&self, _cap: McpCapability) -> AccessDecision {
        AccessDecision::Allow
    }
}

/// Session that denies every capability check.
struct AlwaysDenySession;

impl mcp_server::HostSession for AlwaysDenySession {
    fn session_id(&self) -> &str {
        "deny-session"
    }

    fn check_capability(&self, cap: McpCapability) -> AccessDecision {
        AccessDecision::Deny {
            reason: format!("capability {cap} denied by policy"),
            code: AccessDeniedCode::CapabilityDenied,
        }
    }
}

// ---------------------------------------------------------------------------
// Inline fake: WorkspaceAdapter
// ---------------------------------------------------------------------------

/// Minimal in-memory workspace that stores files in a `HashMap`.
///
/// Does not depend on any `ralph-workflow` type.
struct InMemoryFs {
    files: RwLock<std::collections::HashMap<PathBuf, String>>,
}

impl InMemoryFs {
    fn new() -> Self {
        Self {
            files: RwLock::new(std::collections::HashMap::new()),
        }
    }
}

impl mcp_server::WorkspaceAdapter for InMemoryFs {
    fn read(&self, path: &Path) -> Result<String, String> {
        self.files
            .read()
            .unwrap()
            .get(path)
            .cloned()
            .ok_or_else(|| format!("not found: {}", path.display()))
    }

    fn write(&self, path: &Path, content: &str) -> Result<(), String> {
        self.files
            .write()
            .unwrap()
            .insert(path.to_path_buf(), content.to_string());
        Ok(())
    }

    fn exists(&self, path: &Path) -> bool {
        self.files.read().unwrap().contains_key(path)
    }

    fn read_dir(&self, _path: &Path) -> Result<Vec<DirEntry>, String> {
        Ok(vec![])
    }
}

// ---------------------------------------------------------------------------
// Inline fake: AuditSink
// ---------------------------------------------------------------------------

/// Audit sink that records all emitted records for test assertions.
struct RecordingAuditSink {
    records: Mutex<Vec<AuditRecord>>,
}

impl RecordingAuditSink {
    fn new() -> Self {
        Self {
            records: Mutex::new(Vec::new()),
        }
    }

    fn records(&self) -> Vec<AuditRecord> {
        self.records.lock().unwrap().clone()
    }
}

impl AuditSink for RecordingAuditSink {
    fn emit(&self, record: AuditRecord) {
        self.records.lock().unwrap().push(record);
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Build a minimal `ToolRegistry` with one read-only tool and one mutating tool.
///
/// - `"echo_read"`: `WorkspaceRead` capability, not mutating
/// - `"echo_write"`: `WorkspaceWriteTracked` capability, mutating
fn build_test_registry() -> ToolRegistry {
    let read_meta = ToolMetadata {
        definition: ToolDefinition {
            name: "echo_read".to_string(),
            description: "Read-only echo tool for tests".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": { "msg": { "type": "string" } }
            }),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: Some(false),
    };
    let read_handler: ToolHandler = Arc::new(|_session, _workspace, args| {
        let msg = args
            .get("msg")
            .and_then(|v| v.as_str())
            .unwrap_or("(no msg)");
        Ok(ToolResult {
            content: vec![ToolContent::text(msg)],
            is_error: Some(false),
        })
    });

    let write_meta = ToolMetadata {
        definition: ToolDefinition {
            name: "echo_write".to_string(),
            description: "Mutating echo tool for tests".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": { "msg": { "type": "string" } }
            }),
        },
        required_capability: McpCapability::WorkspaceWriteTracked,
        is_mutating: Some(true),
    };
    let write_handler: ToolHandler = Arc::new(|_session, _workspace, args| {
        let msg = args
            .get("msg")
            .and_then(|v| v.as_str())
            .unwrap_or("(no msg)");
        Ok(ToolResult {
            content: vec![ToolContent::text(msg)],
            is_error: Some(false),
        })
    });

    ToolRegistry::new(vec![(read_meta, read_handler), (write_meta, write_handler)])
}

/// Build an initialized `McpServer` with a custom config and the test tool registry.
fn build_server(
    session: Arc<dyn mcp_server::HostSession>,
    config: McpServerConfig,
    audit_sink: Option<Arc<dyn AuditSink>>,
) -> McpServer {
    let workspace = Arc::new(InMemoryFs::new()) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = build_test_registry();
    McpServer::new(session, config, workspace, registry, audit_sink)
}

/// Construct an `initialize` request.
fn initialize_request(id: i64) -> JsonRpcRequest {
    JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({ "protocolVersion": "2024-11-05" })),
        id: Some(serde_json::json!(id)),
    }
}

/// Construct a `tools/call` request.
fn tools_call_request(id: i64, name: &str, arguments: serde_json::Value) -> JsonRpcRequest {
    JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": name,
            "arguments": arguments,
        })),
        id: Some(serde_json::json!(id)),
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

/// Proves that `McpServerConfig` can be constructed without importing any
/// `ralph-workflow` type. This is the compile-time isolation proof: if this
/// test compiles and passes, `mcp-server` is usable standalone.
#[test]
fn standalone_config_creates_without_ralph_workflow() {
    let root = PathBuf::from("/tmp/standalone-test-root");
    let config = McpServerConfig::new(root.clone());
    assert_eq!(config.root_dir, root);

    // ReadOnly config
    let ro_config = McpServerConfig::new(root.clone()).with_access_mode(AccessMode::ReadOnly);
    assert_eq!(ro_config.access_mode, AccessMode::ReadOnly);

    // ReadWrite config with blocklist
    let rw_config = McpServerConfig::new(root.clone())
        .with_access_mode(AccessMode::ReadWrite)
        .with_tool_filter(ToolFilter::Blocklist(vec![]));
    assert_eq!(rw_config.access_mode, AccessMode::ReadWrite);
}

/// Verifies that the `initialize` handshake succeeds and transitions server
/// state from `Uninitialized` to `Ready`.
#[test]
fn full_stack_initialize_handshake_succeeds() {
    let root = PathBuf::from("/tmp/full-stack-init-test");
    let config = McpServerConfig::new(root);
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let server = build_server(session, config, None);

    let (response, new_state) =
        server.handle_request(initialize_request(1), ServerState::Uninitialized);

    let resp = response.expect("initialize must return a response");
    assert!(resp.result.is_some(), "initialize must return a result");
    assert!(resp.error.is_none(), "initialize must not return an error");
    assert_eq!(
        new_state,
        ServerState::Ready,
        "state must transition to Ready after initialize"
    );
}

/// Verifies that `ReadOnly` mode rejects mutating tools before the capability
/// check is reached. The error code must be `ReadOnlyMode`.
#[test]
fn full_stack_readonly_mode_rejects_write_file() {
    let root = PathBuf::from("/tmp/full-stack-readonly-test");
    let config = McpServerConfig::new(root).with_access_mode(AccessMode::ReadOnly);
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let server = build_server(session, config, None);

    let (response, _state) = server.handle_request(
        tools_call_request(2, "echo_write", serde_json::json!({"msg": "hello"})),
        ServerState::Ready,
    );

    let resp = response.expect("tools/call must return a response");
    assert!(resp.error.is_some(), "ReadOnly mode must return an error");
    let err = resp.error.unwrap();
    // Tool errors return code -32000
    assert_eq!(err.code, -32000, "ReadOnly denial must use code -32000");
    let data_str = serde_json::to_string(&err.data.unwrap_or_default()).unwrap_or_default();
    assert!(
        data_str.contains("ReadOnlyMode"),
        "error data must contain ReadOnlyMode code; got: {data_str}"
    );
}

/// Verifies that the path boundary check rejects file-path arguments that resolve
/// outside `root_dir`, regardless of `access_mode` or capability grants.
#[test]
fn full_stack_root_dir_boundary_rejects_outside_paths() {
    let root = PathBuf::from("/tmp/full-stack-boundary-test");
    let config = McpServerConfig::new(root.clone()).with_access_mode(AccessMode::ReadWrite);
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let server = build_server(session, config, None);

    // Attempt to read a file outside root_dir
    let outside_path = "/etc/passwd";
    let (response, _) = server.handle_request(
        tools_call_request(3, "echo_read", serde_json::json!({ "path": outside_path })),
        ServerState::Ready,
    );

    let resp = response.expect("tools/call must return a response");
    assert!(
        resp.error.is_some(),
        "path outside root_dir must return an error"
    );
    let err = resp.error.unwrap();
    assert_eq!(err.code, -32000, "boundary rejection must use code -32000");
    let data_str = serde_json::to_string(&err.data.unwrap_or_default()).unwrap_or_default();
    assert!(
        data_str.contains("OutsideRootDir"),
        "error data must contain OutsideRootDir code; got: {data_str}"
    );
}

/// Verifies that an `Allowlist` filter rejects tools not in the allowlist,
/// independent of `access_mode` and capability grants.
#[test]
fn full_stack_allowlist_rejects_unlisted_tools() {
    let root = PathBuf::from("/tmp/full-stack-allowlist-test");
    // Only echo_read is in the allowlist; echo_write is excluded
    let config = McpServerConfig::new(root)
        .with_access_mode(AccessMode::ReadWrite)
        .with_tool_filter(ToolFilter::Allowlist(vec!["echo_read".to_string()]));
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let server = build_server(session, config, None);

    let (response, _) = server.handle_request(
        tools_call_request(4, "echo_write", serde_json::json!({"msg": "hi"})),
        ServerState::Ready,
    );

    let resp = response.expect("tools/call must return a response");
    assert!(
        resp.error.is_some(),
        "tool not in allowlist must return error"
    );
    let err = resp.error.unwrap();
    assert_eq!(err.code, -32000, "allowlist denial must use code -32000");
    let data_str = serde_json::to_string(&err.data.unwrap_or_default()).unwrap_or_default();
    assert!(
        data_str.contains("ToolNotAllowed"),
        "error data must contain ToolNotAllowed code; got: {data_str}"
    );
}

/// Verifies that a `Blocklist` filter rejects tools in the blocklist,
/// independent of `access_mode` and capability grants.
#[test]
fn full_stack_blocklist_rejects_listed_tools() {
    let root = PathBuf::from("/tmp/full-stack-blocklist-test");
    // Block echo_read; echo_write remains accessible (subject to other checks)
    let config = McpServerConfig::new(root)
        .with_access_mode(AccessMode::ReadWrite)
        .with_tool_filter(ToolFilter::Blocklist(vec!["echo_read".to_string()]));
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let server = build_server(session, config, None);

    let (response, _) = server.handle_request(
        tools_call_request(5, "echo_read", serde_json::json!({"msg": "hi"})),
        ServerState::Ready,
    );

    let resp = response.expect("tools/call must return a response");
    assert!(resp.error.is_some(), "tool in blocklist must return error");
    let err = resp.error.unwrap();
    assert_eq!(err.code, -32000, "blocklist denial must use code -32000");
    let data_str = serde_json::to_string(&err.data.unwrap_or_default()).unwrap_or_default();
    assert!(
        data_str.contains("ToolNotAllowed"),
        "error data must contain ToolNotAllowed code; got: {data_str}"
    );
}

/// Verifies that when `HostSession::check_capability` returns `Deny`, the
/// server propagates the correct `CapabilityDenied` error code to the client.
///
/// The `AlwaysDenySession` is used here — it denies every capability.
/// The server must use the `CapabilityDenied` denial code, not `ReadOnlyMode`
/// or `ToolNotAllowed`, proving that all four enforcement levels are distinct.
#[test]
fn full_stack_capability_denial_propagates_error_code() {
    let root = PathBuf::from("/tmp/full-stack-capability-test");
    // No tool filter, ReadWrite mode — only the capability check should fire
    let config = McpServerConfig::new(root)
        .with_access_mode(AccessMode::ReadWrite)
        .with_tool_filter(ToolFilter::Blocklist(vec![]));
    // Session denies everything
    let session = Arc::new(AlwaysDenySession) as Arc<dyn mcp_server::HostSession>;
    let audit_sink = Arc::new(RecordingAuditSink::new());
    let server = build_server(
        session,
        config,
        Some(Arc::clone(&audit_sink) as Arc<dyn AuditSink>),
    );

    let (response, _) = server.handle_request(
        tools_call_request(6, "echo_read", serde_json::json!({"msg": "hello"})),
        ServerState::Ready,
    );

    let resp = response.expect("tools/call must return a response");
    assert!(
        resp.error.is_some(),
        "capability denial must return an error"
    );
    let err = resp.error.unwrap();
    assert_eq!(
        err.code, -32000,
        "capability denial must use code -32000; got: {}",
        err.code
    );
    let data_str = serde_json::to_string(&err.data.unwrap_or_default()).unwrap_or_default();
    assert!(
        data_str.contains("CapabilityDenied"),
        "error data must contain CapabilityDenied code; got: {data_str}"
    );

    // Audit sink must record the denial
    let records = audit_sink.records();
    assert!(
        !records.is_empty(),
        "audit sink must record the capability denial"
    );
}

/// Verifies that `access_mode` and `tool_filter` checks are independent:
/// an allowlisted tool still fails `ReadOnly` mode if it is mutating.
#[test]
fn full_stack_allowlist_and_readonly_are_independent() {
    let root = PathBuf::from("/tmp/full-stack-independent-test");
    // echo_write is in the allowlist BUT the server is ReadOnly
    let config = McpServerConfig::new(root)
        .with_access_mode(AccessMode::ReadOnly)
        .with_tool_filter(ToolFilter::Allowlist(vec!["echo_write".to_string()]));
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let server = build_server(session, config, None);

    let (response, _) = server.handle_request(
        tools_call_request(7, "echo_write", serde_json::json!({"msg": "hi"})),
        ServerState::Ready,
    );

    let resp = response.expect("tools/call must return a response");
    assert!(
        resp.error.is_some(),
        "ReadOnly + mutating must return error"
    );
    let err = resp.error.unwrap();
    let data_str = serde_json::to_string(&err.data.unwrap_or_default()).unwrap_or_default();
    // ReadOnly check fires AFTER allowlist — code must be ReadOnlyMode not ToolNotAllowed
    assert!(
        data_str.contains("ReadOnlyMode"),
        "error must be ReadOnlyMode (not ToolNotAllowed); got: {data_str}"
    );
}

/// Verifies that tools/list returns the correct tool names.
///
/// This exercises the full tools/list path and confirms the registry is wired
/// to the server correctly, independent of any ralph-workflow tooling.
#[test]
fn full_stack_tools_list_returns_registered_tools() {
    let root = PathBuf::from("/tmp/full-stack-list-test");
    let config = McpServerConfig::new(root);
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let server = build_server(session, config, None);

    // Must initialize first
    let (_, state) = server.handle_request(initialize_request(1), ServerState::Uninitialized);

    let list_req = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };
    let (response, _) = server.handle_request(list_req, state);

    let resp = response.expect("tools/list must return a response");
    assert!(resp.result.is_some(), "tools/list must return a result");
    let tools = resp.result.unwrap();
    let tool_arr = tools["tools"]
        .as_array()
        .expect("result must have a tools array");
    let names: Vec<&str> = tool_arr.iter().filter_map(|t| t["name"].as_str()).collect();
    assert!(
        names.contains(&"echo_read"),
        "tools list must include echo_read; got: {names:?}"
    );
    assert!(
        names.contains(&"echo_write"),
        "tools list must include echo_write; got: {names:?}"
    );
}

/// Verifies that methods called before `initialize` return the correct
/// `NotInitialized` error with code `-32001`.
#[test]
fn full_stack_methods_before_initialize_return_not_initialized() {
    let root = PathBuf::from("/tmp/full-stack-uninit-test");
    let config = McpServerConfig::new(root);
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let server = build_server(session, config, None);

    let (response, state) = server.handle_request(
        tools_call_request(1, "echo_read", serde_json::json!({})),
        ServerState::Uninitialized,
    );

    let resp = response.expect("uninitialized call must return a response");
    assert!(
        resp.error.is_some(),
        "uninitialized call must return an error"
    );
    let err = resp.error.unwrap();
    assert_eq!(
        err.code, -32001,
        "not-initialized error must use code -32001"
    );
    assert_eq!(
        state,
        ServerState::Uninitialized,
        "state must remain Uninitialized"
    );
}

/// Verifies that a `ping` request returns a `null` result and leaves the
/// server state unchanged. This is the MCP liveness-check path.
#[test]
fn full_stack_ping_returns_null_and_preserves_state() {
    let root = PathBuf::from("/tmp/full-stack-ping-test");
    let config = McpServerConfig::new(root);
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let server = build_server(session, config, None);

    // Initialize first so the server is in Ready state
    let (_, state) = server.handle_request(initialize_request(1), ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    let ping_req = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "ping".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };
    let (response, new_state) = server.handle_request(ping_req, state);

    let resp = response.expect("ping must return a response");
    assert!(resp.error.is_none(), "ping must not return an error");
    assert!(resp.result.is_some(), "ping must return a result");
    assert_eq!(
        resp.result.unwrap(),
        serde_json::Value::Null,
        "ping result must be null"
    );
    assert_eq!(new_state, ServerState::Ready, "ping must not change state");
}

/// Verifies that an allowed `tools/call` succeeds and returns the expected
/// `ToolResult` content. This is the happy-path: AllowAll session, ReadWrite
/// mode, open blocklist, non-mutating tool.
#[test]
fn full_stack_allowed_tool_call_returns_success_result() {
    let root = PathBuf::from("/tmp/full-stack-allowed-call-test");
    let config = McpServerConfig::new(root).with_access_mode(AccessMode::ReadWrite);
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let server = build_server(session, config, None);

    let (_, state) = server.handle_request(initialize_request(1), ServerState::Uninitialized);

    let (response, _) = server.handle_request(
        tools_call_request(
            2,
            "echo_read",
            serde_json::json!({ "msg": "hello-allowed" }),
        ),
        state,
    );

    let resp = response.expect("allowed tools/call must return a response");
    assert!(
        resp.error.is_none(),
        "allowed tools/call must not return an error; got: {:?}",
        resp.error
    );
    let result = resp
        .result
        .expect("allowed tools/call must return a result");
    let content = result["content"]
        .as_array()
        .expect("result must have a content array");
    assert!(!content.is_empty(), "content must be non-empty");
    let text = content[0]["text"].as_str().unwrap_or("");
    assert_eq!(
        text, "hello-allowed",
        "tool must echo the msg argument; got: {text:?}"
    );
}

/// Verifies that the `AuditSink` receives records for both `Allow` and `Deny`
/// outcomes within the same test. This proves the audit path is exercised for
/// the complete set of access decisions in a single standalone suite run.
#[test]
fn full_stack_audit_sink_records_both_allow_and_deny_outcomes() {
    let root = PathBuf::from("/tmp/full-stack-audit-both-test");
    // Open config: ReadWrite, empty blocklist → allow path reachable
    let config = McpServerConfig::new(root).with_access_mode(AccessMode::ReadWrite);
    let audit_sink = Arc::new(RecordingAuditSink::new());

    // Use AlwaysAllowSession so the first call succeeds (Allow record)
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(InMemoryFs::new()) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = build_test_registry();
    let server = McpServer::new(
        session,
        config.clone(),
        workspace,
        registry,
        Some(Arc::clone(&audit_sink) as Arc<dyn AuditSink>),
    );

    // Initialize, then make one successful call (produces Allow audit record)
    let (_, state) = server.handle_request(initialize_request(1), ServerState::Uninitialized);
    let (resp_allow, _state) = server.handle_request(
        tools_call_request(2, "echo_read", serde_json::json!({ "msg": "audit-allow" })),
        state,
    );
    assert!(
        resp_allow
            .as_ref()
            .map(|r| r.error.is_none())
            .unwrap_or(false),
        "first call must succeed to produce an Allow audit record"
    );

    // Now build a second server using AlwaysDenySession to produce a Deny audit record
    let deny_audit_sink = Arc::clone(&audit_sink);
    let deny_config = McpServerConfig::new(PathBuf::from("/tmp/full-stack-audit-both-deny-test"))
        .with_access_mode(AccessMode::ReadWrite);
    let deny_session = Arc::new(AlwaysDenySession) as Arc<dyn mcp_server::HostSession>;
    let deny_workspace = Arc::new(InMemoryFs::new()) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let deny_registry = build_test_registry();
    let deny_server = McpServer::new(
        deny_session,
        deny_config,
        deny_workspace,
        deny_registry,
        Some(deny_audit_sink as Arc<dyn AuditSink>),
    );
    let (_, deny_state) =
        deny_server.handle_request(initialize_request(1), ServerState::Uninitialized);
    let (resp_deny, _) = deny_server.handle_request(
        tools_call_request(2, "echo_read", serde_json::json!({ "msg": "audit-deny" })),
        deny_state,
    );
    assert!(
        resp_deny
            .as_ref()
            .map(|r| r.error.is_some())
            .unwrap_or(false),
        "deny-session call must fail to produce a Deny audit record"
    );

    // Assert the shared audit sink has at least one Allow and at least one Deny record
    let records = audit_sink.records();
    let has_allow = records
        .iter()
        .any(|r| matches!(r.decision, AccessDecision::Allow));
    let has_deny = records
        .iter()
        .any(|r| matches!(r.decision, AccessDecision::Deny { .. }));
    assert!(
        has_allow,
        "audit sink must have at least one Allow record; records: {records:?}"
    );
    assert!(
        has_deny,
        "audit sink must have at least one Deny record; records: {records:?}"
    );
    assert!(
        records.len() >= 2,
        "audit sink must have at least 2 records (one Allow, one Deny); got: {}",
        records.len()
    );
}

/// Verify that [`FakeTransportPair`] routes requests and responses bidirectionally.
///
/// Proves that the shared-queue transport pair correctly wires the client and
/// server halves:
///
/// 1. Client injects an `initialize` request.
/// 2. Server reads it via `McpStream::read_request()`.
/// 3. Server handles the request and writes the response via `McpStream::write_response()`.
/// 4. Client reads the response via `read_response()`.
///
/// This test uses only `mcp_server` types (no `ralph_workflow` import) — it is
/// part of the standalone isolation proof.
#[test]
fn fake_transport_pair_bidirectional_request_response_roundtrip() {
    let root = PathBuf::from("/tmp/fake-pair-roundtrip-test");
    let session = Arc::new(AlwaysAllowSession) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(InMemoryFs::new()) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(root).with_access_mode(AccessMode::ReadWrite);
    let server = McpServer::new(session, config, workspace, build_test_registry(), None);

    let mut pair = FakeTransportPair::new();

    // Step 1: client injects an initialize request
    let init_req = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    pair.client.inject_request(init_req);

    // Step 2: server reads the request from the shared client-to-server queue
    let req = pair
        .server
        .read_request()
        .expect("server read_request must not error")
        .expect("request injected by client must be available on server side");
    assert_eq!(
        req.method, "initialize",
        "server must receive the initialize request"
    );

    // Step 3: server handles the request and writes the response
    let (maybe_resp, state) = server.handle_request(req, ServerState::Uninitialized);
    let resp = maybe_resp.expect("initialize must produce a response");
    assert_eq!(
        state,
        ServerState::Ready,
        "state must be Ready after initialize"
    );
    pair.server
        .write_response(&resp)
        .expect("server write_response must not error");

    // Step 4: client reads the response from the shared server-to-client queue
    let client_resp = pair
        .client
        .read_response()
        .expect("response written by server must be available on client side");
    assert!(
        client_resp.result.is_some(),
        "initialize response must have a result; got: {client_resp:?}"
    );
    assert!(
        client_resp.error.is_none(),
        "initialize response must not have an error; got: {client_resp:?}"
    );

    // Step 5: exercise a full tool call through the pair (tools/list)
    let list_req = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };
    pair.client.inject_request(list_req);

    let list_server_req = pair
        .server
        .read_request()
        .expect("read must not error")
        .expect("tools/list must be available");
    let (list_maybe_resp, _) = server.handle_request(list_server_req, state);
    let list_resp = list_maybe_resp.expect("tools/list must produce a response");
    pair.server
        .write_response(&list_resp)
        .expect("write must not error");

    let client_list_resp = pair
        .client
        .read_response()
        .expect("tools/list response must reach client");
    let tools = client_list_resp
        .result
        .as_ref()
        .and_then(|r| r["tools"].as_array())
        .expect("tools/list result must have a tools array");
    assert!(
        !tools.is_empty(),
        "tools/list must return at least one tool (echo_read or echo_write)"
    );
}
