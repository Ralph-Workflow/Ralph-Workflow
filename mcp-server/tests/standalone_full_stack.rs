//! Standalone full-stack integration tests for `mcp-server`.
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
//! | ReadOnly mode | `full_stack_readonly_mode_rejects_write_file` |
//! | Path boundary | `full_stack_root_dir_boundary_rejects_outside_paths` |
//! | Allowlist | `full_stack_allowlist_rejects_unlisted_tools` |
//! | Blocklist | `full_stack_blocklist_rejects_listed_tools` |
//! | Capability denial | `full_stack_capability_denial_propagates_error_code` |

use mcp_server::dispatch::access::{
    AccessDecision, AccessDeniedCode, AccessMode, AuditSink, McpCapability, ToolFilter,
};
use mcp_server::dispatch::audit::AuditRecord;
use mcp_server::dispatch::host::DirEntry;
use mcp_server::dispatch::{ToolHandler, ToolMetadata, ToolRegistry};
use mcp_server::io::access::McpServerConfig;
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
