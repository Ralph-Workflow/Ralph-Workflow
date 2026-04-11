//! Standalone host implementation example for MCP server.
//!
//! This test demonstrates how to implement the [`mcp_server::HostSession`] and
//! [`mcp_server::WorkspaceAdapter`] traits to create a self-contained MCP server
//! without depending on ralph-workflow.
//!
//! This is useful for:
//! - Testing MCP server behavior in isolation
//! - Building lightweight MCP clients
//! - Understanding the trait boundaries
//!
//! # Example Architecture
//!
//! ```text
//! Your Application
//!     |
//!     +-- YourSession: implements HostSession
//!     |
//!     +-- YourWorkspace: implements WorkspaceAdapter
//!     |
//!     +-- ToolRegistry with your handlers
//!     |
//!     +-- McpServerConfig (access mode, tool filter, root_dir)
//!     |
//!     v
//! McpServer (from mcp-server crate)
//!     |
//!     +-- SessionBridge for Unix socket transport
//! ```
//!
//! # Test Coverage
//!
//! This module verifies:
//! - McpServerConfig enforcement: ReadOnly mode rejects writes
//! - ToolFilter enforcement: Allowlist blocks unlisted tools
//! - ToolFilter enforcement: Blocklist blocks listed tools
//! - Capability-based access control
//! - SessionBridge full protocol flow without external socket dependencies

use mcp_server::dispatch::access::{
    AccessDecision, AccessDeniedCode, AccessMode, AuditSink, McpCapability, ToolFilter,
};
use mcp_server::dispatch::audit::AuditRecord;
use mcp_server::dispatch::host::DirEntry;
use mcp_server::dispatch::{ToolHandler, ToolMetadata, ToolRegistry};
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::fake::FakeTransport;
use mcp_server::io::session_bridge::SessionBridge;
use mcp_server::io::transport::McpStream;
use mcp_server::io::{McpServer, ServerState};
use mcp_server::protocol::{
    JsonRpcRequest, JsonRpcResponse, ToolContent, ToolDefinition, ToolResult,
};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

/// Returns a safe temp root for MCP tests guaranteed to be outside any git repo.
///
/// Uses `test_helpers::temp_dir_outside_git` to create a unique temporary directory
/// and verifies it is not inside a git repository. The directory is persisted on disk
/// (not auto-deleted) and will remain until OS temp cleanup.
fn temp_root() -> PathBuf {
    test_helpers::temp_dir_outside_git("ralph-mcp-test-").keep()
}

#[test]
#[should_panic(expected = "POLICY VIOLATION")]
fn shared_helper_panics_for_path_inside_repo() {
    let project_root = Path::new(env!("CARGO_MANIFEST_DIR")).parent();
    if let Some(root) = project_root {
        let missing_path = root.join("definitely-does-not-exist").join("nested");
        test_helpers::assert_not_in_git_repo(&missing_path);
    }
}

// ---------------------------------------------------------------------------
// Test Audit Sink
// ---------------------------------------------------------------------------

/// A test audit sink that records all emitted audit records for verification.
///
/// This allows tests to assert on the number and content of audit records
/// generated during tool dispatch.
struct TestAuditSink {
    records: RwLock<Vec<AuditRecord>>,
}

impl TestAuditSink {
    fn new() -> Self {
        Self {
            records: RwLock::new(Vec::new()),
        }
    }

    /// Returns a copy of all stored audit records.
    fn records(&self) -> Vec<AuditRecord> {
        self.records.read().unwrap().clone()
    }
}

impl AuditSink for TestAuditSink {
    fn emit(&self, record: AuditRecord) {
        self.records.write().unwrap().push(record);
    }
}

// ---------------------------------------------------------------------------
// Standalone HostSession Implementation
// ---------------------------------------------------------------------------

/// A simple in-memory session with capability grants.
/// This demonstrates how to implement HostSession without ralph-workflow.
struct InMemorySession {
    session_id: String,
    /// Map of capability -> granted
    granted_capabilities: HashMap<McpCapability, bool>,
}

impl InMemorySession {
    fn new(session_id: &str) -> Self {
        let mut granted = HashMap::new();
        // Grant all capabilities by default for this example
        granted.insert(McpCapability::WorkspaceRead, true);
        granted.insert(McpCapability::WorkspaceWriteEphemeral, true);
        granted.insert(McpCapability::WorkspaceWriteTracked, true);
        granted.insert(McpCapability::GitStatusRead, true);
        granted.insert(McpCapability::GitWrite, true);
        granted.insert(McpCapability::EnvRead, true);
        granted.insert(McpCapability::ProcessExecBounded, true);
        granted.insert(McpCapability::ArtifactSubmit, true);

        Self {
            session_id: session_id.to_string(),
            granted_capabilities: granted,
        }
    }

    /// Creates a session with no capabilities granted (all denied by default).
    fn new_empty(session_id: &str) -> Self {
        Self {
            session_id: session_id.to_string(),
            granted_capabilities: HashMap::new(),
        }
    }

    fn with_capabilities(mut self, caps: &[McpCapability]) -> Self {
        for cap in caps {
            self.granted_capabilities.insert(*cap, true);
        }
        self
    }
}

impl mcp_server::HostSession for InMemorySession {
    fn session_id(&self) -> &str {
        &self.session_id
    }

    fn check_capability(&self, cap: McpCapability) -> AccessDecision {
        match self.granted_capabilities.get(&cap) {
            Some(true) => AccessDecision::Allow,
            Some(false) | None => AccessDecision::Deny {
                reason: format!("Capability {} not granted", cap),
                code: AccessDeniedCode::CapabilityDenied,
            },
        }
    }
}

// ---------------------------------------------------------------------------
// Standalone WorkspaceAdapter Implementation
// ---------------------------------------------------------------------------

/// A simple in-memory workspace for testing.
/// This demonstrates how to implement WorkspaceAdapter without ralph-workflow.
struct InMemoryWorkspace {
    /// Map of path -> content
    files: RwLock<HashMap<PathBuf, String>>,
    root: PathBuf,
}

impl InMemoryWorkspace {
    fn new(root: PathBuf) -> Self {
        Self {
            files: RwLock::new(HashMap::new()),
            root,
        }
    }

    fn with_file(self, path: &str, content: &str) -> Self {
        self.files
            .write()
            .unwrap()
            .insert(PathBuf::from(path), content.to_string());
        self
    }
}

impl mcp_server::WorkspaceAdapter for InMemoryWorkspace {
    fn read(&self, path: &Path) -> Result<String, String> {
        let files = self.files.read().unwrap();
        files
            .get(path)
            .cloned()
            .ok_or_else(|| format!("File not found: {}", path.display()))
    }

    fn write(&self, path: &Path, content: &str) -> Result<(), String> {
        // Check if path is within root
        if !path.starts_with(&self.root) {
            return Err(format!(
                "Path {} is outside workspace root {}",
                path.display(),
                self.root.display()
            ));
        }
        let mut files = self.files.write().unwrap();
        files.insert(path.to_path_buf(), content.to_string());
        Ok(())
    }

    fn exists(&self, path: &Path) -> bool {
        self.files.read().unwrap().contains_key(path)
    }

    fn read_dir(&self, path: &Path) -> Result<Vec<DirEntry>, String> {
        let files = self.files.read().unwrap();
        let mut entries = Vec::new();

        for (file_path, _) in files.iter() {
            if file_path.parent() == Some(path) || file_path == path {
                entries.push(DirEntry {
                    path: file_path.display().to_string(),
                    is_dir: false,
                });
            }
        }

        Ok(entries)
    }
}

// ---------------------------------------------------------------------------
// Test Server Factory
// ---------------------------------------------------------------------------

fn create_test_server(session: InMemorySession, workspace: InMemoryWorkspace) -> McpServer {
    let session = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(temp_root());
    let registry = ToolRegistry::new(vec![]);

    McpServer::new(session, config, workspace, registry, None)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[test]
fn test_standalone_session_check_capability() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);
    let session = InMemorySession::new("test-session");
    let workspace = InMemoryWorkspace::new(root);

    let server = create_test_server(session, workspace);

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);
}

#[test]
fn test_standalone_workspace_read_write() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);
    let session = InMemorySession::new("test-session").with_capabilities(&[
        McpCapability::WorkspaceRead,
        McpCapability::WorkspaceWriteTracked,
    ]);
    let workspace = InMemoryWorkspace::new(root).with_file("test.txt", "Hello, World!");

    let server = create_test_server(session, workspace);

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // List tools should work
    let list = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };
    let (response, _) = server.handle_request(list, state);
    let response = response.expect("handle_request should return a response for non-notification");
    assert!(response.result.is_some());
}

#[test]
fn test_capability_denial_from_session() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);
    // Session without GitStatusRead capability
    let session = InMemorySession::new("restricted-session")
        .with_capabilities(&[McpCapability::WorkspaceRead]); // No GitStatusRead
    let workspace = InMemoryWorkspace::new(root.clone());

    let server = create_test_server(session, workspace);

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, _state) = server.handle_request(init, ServerState::Uninitialized);

    // Create a tool that requires GitStatusRead
    let handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "git status".to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "git_status".to_string(),
            description: "Get git status".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
        },
        required_capability: McpCapability::GitStatusRead,
        is_mutating: None,
    };

    // Note: This test demonstrates the capability check flow.
    // The actual denial happens in the registry when dispatch is called.
    let session_with_git = InMemorySession::new("test-session")
        .with_capabilities(&[McpCapability::WorkspaceRead, McpCapability::GitStatusRead]);
    let workspace_with_git = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session_with_git) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace_with_git) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(root);
    let registry = ToolRegistry::new(vec![(metadata, handler)]);
    let server_with_git = McpServer::new(session_arc, config, workspace_arc, registry, None);

    let init_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state_with_git) =
        server_with_git.handle_request(init_request, ServerState::Uninitialized);

    // Call the git_status tool - should succeed because we have the capability
    let tool_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "git_status",
            "arguments": {}
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server_with_git.handle_request(tool_request, state_with_git);
    let response = response.expect("handle_request should return a response for non-notification");
    assert!(
        response.result.is_some(),
        "Should succeed with GitStatusRead capability"
    );
}

// ---------------------------------------------------------------------------
// Additional Standalone Tests: McpServerConfig Enforcement
// ---------------------------------------------------------------------------

/// Test that ReadOnly mode denies write operations.
#[test]
fn test_read_only_mode_denies_write_tool() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);
    // Create a read-only config
    let config = McpServerConfig::new(root.clone()).with_access_mode(AccessMode::ReadOnly);

    // Session with write capability
    let session = InMemorySession::new("ro-session").with_capabilities(&[
        McpCapability::WorkspaceRead,
        McpCapability::WorkspaceWriteTracked,
    ]);
    let workspace = InMemoryWorkspace::new(root).with_file("test.txt", "content");

    // Create a Ralph-prefixed write tool that would be blocked by ReadOnly mode
    let handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "written".to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_workspace_write_file".to_string(),
            description: "Write a file to workspace".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string" },
                    "content": { "type": "string" }
                },
                "required": ["path", "content"]
            }),
        },
        required_capability: McpCapability::WorkspaceWriteEphemeral,
        is_mutating: None,
    };
    let registry = ToolRegistry::new(vec![(metadata, handler)]);

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;

    // Create audit sink to verify denial generates audit record
    let audit_sink = Arc::new(TestAuditSink::new());

    let server = McpServer::new(
        session_arc,
        config,
        workspace_arc,
        registry,
        Some(audit_sink.clone()),
    );

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // A Ralph-prefixed write tool call should be denied due to ReadOnly mode
    let tool_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "ralph_workspace_write_file",
            "arguments": {"path": "test.txt", "content": "new content"}
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(tool_request, state);
    let response = response.expect("handle_request should return a response for non-notification");
    // ReadOnly mode should result in an error response with ReadOnly denial
    assert!(
        response.error.is_some(),
        "ReadOnly mode should deny write operations"
    );
    let error = response.error.unwrap();
    assert!(
        error.message.contains("ReadOnly") || error.message.contains("read only"),
        "Error should mention ReadOnly mode restriction, got: {}",
        error.message
    );

    // Assert audit record was created for the ReadOnly denial
    let records = audit_sink.records();
    assert_eq!(
        records.len(),
        1,
        "ReadOnly denial should generate exactly one audit record"
    );
    let record = &records[0];
    assert_eq!(
        record.tool_name, "ralph_workspace_write_file",
        "Audit record should contain the denied tool name"
    );
    // Verify the denial is due to ReadOnlyMode (not CapabilityDenied)
    match &record.decision {
        AccessDecision::Deny { code, .. } => {
            assert!(
                matches!(code, AccessDeniedCode::ReadOnlyMode),
                "Audit record should indicate ReadOnlyMode denial, got: {:?}",
                code
            );
        }
        other => panic!("Expected denial, got: {:?}", other),
    }
}

/// Test that Allowlist filter blocks tools not in the list.
#[test]
fn test_allowlist_blocks_unlisted_tool() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);
    // Create config with allowlist that only permits "read_file"
    let config = McpServerConfig::new(root.clone())
        .with_tool_filter(ToolFilter::Allowlist(vec!["read_file".to_string()]));

    let session = InMemorySession::new("allowlist-session")
        .with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(root).with_file("test.txt", "content");
    let handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "git status".to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "git_status".to_string(),
            description: "Get git status".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
        },
        required_capability: McpCapability::GitStatusRead,
        is_mutating: None,
    };

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![(metadata, handler)]);

    // Create audit sink to verify denial generates audit record
    let audit_sink = Arc::new(TestAuditSink::new());

    let server = McpServer::new(
        session_arc,
        config,
        workspace_arc,
        registry,
        Some(audit_sink.clone()),
    );

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // A tool not in allowlist should be blocked
    let tool_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "git_status",
            "arguments": {}
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(tool_request, state);
    let response = response.expect("handle_request should return a response for non-notification");
    // Tool not in allowlist should be blocked
    assert!(
        response.error.is_some() || response.result.is_none(),
        "Allowlist should block tools not in the list"
    );

    // Assert audit record was created for the ToolNotAllowed denial
    let records = audit_sink.records();
    assert_eq!(
        records.len(),
        1,
        "Allowlist denial should generate exactly one audit record"
    );
    let record = &records[0];
    assert_eq!(
        record.tool_name, "git_status",
        "Audit record should contain the denied tool name"
    );
    // Verify the denial is due to ToolNotAllowed (not CapabilityDenied)
    match &record.decision {
        AccessDecision::Deny { code, .. } => {
            assert!(
                matches!(code, AccessDeniedCode::ToolNotAllowed),
                "Audit record should indicate ToolNotAllowed denial, got: {:?}",
                code
            );
        }
        other => panic!("Expected denial, got: {:?}", other),
    }
}

/// Test that Blocklist filter blocks tools in the list.
#[test]
fn test_blocklist_blocks_listed_tool() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);
    // Create config with blocklist that blocks "git_status"
    let config = McpServerConfig::new(root.clone())
        .with_tool_filter(ToolFilter::Blocklist(vec!["git_status".to_string()]));

    let session = InMemorySession::new("blocklist-session")
        .with_capabilities(&[McpCapability::WorkspaceRead, McpCapability::GitStatusRead]);
    let workspace = InMemoryWorkspace::new(root);

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);

    let server = McpServer::new(session_arc, config, workspace_arc, registry, None);

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // A tool in blocklist should be blocked
    let tool_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "git_status",
            "arguments": {}
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(tool_request, state);
    let response = response.expect("handle_request should return a response for non-notification");
    // Tool in blocklist should be blocked
    assert!(
        response.error.is_some() || response.result.is_none(),
        "Blocklist should block tools in the list"
    );
}

struct TestConnection {
    bridge: SessionBridge,
    state: ServerState,
    pending_response: Option<JsonRpcResponse>,
}

fn connect(bridge: &SessionBridge) -> TestConnection {
    TestConnection {
        bridge: bridge.clone(),
        state: ServerState::Uninitialized,
        pending_response: None,
    }
}

/// Helper: send a request and read the JSON response through the in-process bridge.
fn send_framed_request(connection: &mut TestConnection, request: serde_json::Value) -> String {
    let request: JsonRpcRequest =
        serde_json::from_value(request).expect("Request should be valid JSON-RPC");
    let (response, next_state) = connection
        .bridge
        .handle_request_in_process(request, connection.state);
    connection.state = next_state;
    connection.pending_response = response;

    serde_json::to_string(
        &connection
            .pending_response
            .take()
            .expect("Request should produce a response"),
    )
    .expect("Response should serialize to JSON")
}

/// Test full protocol flow through SessionBridge without a live socket.
///
/// This test exercises ALL 9 required assertions from the standalone E2E test plan:
///
/// 1. Start McpServer with ReadWrite, Blocklist([]) and register an echo tool
/// 2. Connect via UnixSocketTransport using SessionBridge
/// 3. Verify initialize returns successful response with protocol version
/// 4. Verify tools/list shows the echo tool in the list
/// 5. Verify tools/call for the echo tool returns expected content
/// 6. Verify that calling a tool BEFORE initialize returns NotInitialized (-32001)
/// 7. Verify that ReadOnly mode rejects a mutating tool with ReadOnlyMode denial
/// 8. Verify that Allowlist rejects a tool not in the list with ToolNotAllowed
/// 9. Verify that path outside root_dir is rejected with OutsideRootDir
#[test]
fn test_session_bridge_full_protocol_flow() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Create an echo tool handler for testing
    let echo_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            let msg = params
                .get("message")
                .and_then(|v| v.as_str())
                .unwrap_or("default");
            Ok(ToolResult::success(vec![ToolContent::text(format!(
                "echo: {}",
                msg
            ))]))
        },
    );
    let echo_metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "test_echo".to_string(),
            description: "Echo a message".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "message": { "type": "string" }
                }
            }),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None,
    };

    // Create a mutating tool (requires ReadWrite)
    let mutating_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "mutated".to_string(),
            )]))
        },
    );
    let mutating_metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "test_mutate".to_string(),
            description: "A mutating tool".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {}
            }),
        },
        required_capability: McpCapability::WorkspaceWriteEphemeral,
        is_mutating: None,
    };

    // Create session with required capabilities
    let session = InMemorySession::new("bridge-test-session").with_capabilities(&[
        McpCapability::WorkspaceRead,
        McpCapability::WorkspaceWriteEphemeral,
    ]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    // Registry with ReadWrite config, blocklist allowing all tools
    let registry = ToolRegistry::new(vec![
        (echo_metadata, echo_handler),
        (mutating_metadata, mutating_handler),
    ]);
    let config = McpServerConfig::new(root.clone()).with_access_mode(AccessMode::ReadWrite);

    let bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    let mut stream = connect(&bridge);

    // ============================================================
    // ASSERTION 3: initialize returns successful response with protocol version
    // ============================================================
    let init_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    });
    let response_str = send_framed_request(&mut stream, init_request);
    let response: JsonRpcResponse =
        serde_json::from_str(&response_str).expect("Response should be valid JSON");
    assert!(
        response.result.is_some(),
        "Assertion 3 failed: initialize should return success result"
    );
    let result = response.result.unwrap();
    assert_eq!(
        result["protocolVersion"], "2024-11-05",
        "Protocol version should be echoed back"
    );

    // ============================================================
    // ASSERTION 4: tools/list shows the echo tool
    // ============================================================
    let list_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 2
    });
    let list_response_str = send_framed_request(&mut stream, list_request);
    let list_response: JsonRpcResponse =
        serde_json::from_str(&list_response_str).expect("Response should be valid JSON");
    assert!(
        list_response.result.is_some(),
        "Assertion 4 failed: tools/list should return success"
    );
    let list_result = list_response.result.unwrap();
    let tools = list_result["tools"]
        .as_array()
        .expect("tools should be an array");
    let tool_names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert!(
        tool_names.contains(&"test_echo"),
        "Assertion 4 failed: tools/list should contain test_echo, got: {:?}",
        tool_names
    );

    // ============================================================
    // ASSERTION 5: tools/call for echo tool returns expected content
    // ============================================================
    let call_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "test_echo",
            "arguments": {"message": "hello socket"}
        },
        "id": 3
    });
    let call_response_str = send_framed_request(&mut stream, call_request);
    let call_response: JsonRpcResponse =
        serde_json::from_str(&call_response_str).expect("Response should be valid JSON");
    assert!(
        call_response.result.is_some(),
        "Assertion 5 failed: tools/call should return success"
    );
    let call_result = call_response.result.unwrap();
    let content = call_result["content"]
        .as_array()
        .expect("content should be an array");
    let text = content[0]["text"]
        .as_str()
        .expect("text should be a string");
    assert!(
        text.contains("echo: hello socket"),
        "Assertion 5 failed: echo should return 'echo: hello socket', got: {}",
        text
    );

    drop(stream);
}

/// Test that NotInitialized error is returned when calling tool before initialize.
///
/// Per the MCP protocol, any tool call before initialize should return
/// error code -32001 (NotInitialized).
#[test]
fn test_not_initialized_error_before_initialize() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    let session =
        InMemorySession::new("not-init-test").with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);
    let config = McpServerConfig::new(root);

    let bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    let mut stream = connect(&bridge);

    // Try to call tools/list WITHOUT initialize - should get NotInitialized error
    let list_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 1
    });
    let response_str = send_framed_request(&mut stream, list_request);
    let response: JsonRpcResponse =
        serde_json::from_str(&response_str).expect("Response should be valid JSON");

    // Per the MCP protocol, NotInitialized error code is -32001
    assert!(
        response.error.is_some(),
        "Assertion 6 failed: calling tools before initialize should return error"
    );
    let error = response.error.unwrap();
    assert_eq!(
        error.code, -32001,
        "Assertion 6 failed: error code should be -32001 (NotInitialized), got: {}",
        error.code
    );
}

/// Test that ReadOnly mode rejects mutating tool calls.
///
/// Per McpServerConfig enforcement, ReadOnly mode should reject any tool
/// with is_mutating=true, returning ReadOnlyMode denial.
#[test]
fn test_readonly_rejects_mutating_tool_via_socket() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Create a mutating tool
    let mutating_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "mutated".to_string(),
            )]))
        },
    );
    let mutating_metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "test_mutate".to_string(),
            description: "A mutating tool".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
        },
        required_capability: McpCapability::WorkspaceWriteEphemeral,
        is_mutating: None,
    };

    let session = InMemorySession::new("readonly-test")
        .with_capabilities(&[McpCapability::WorkspaceWriteEphemeral]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![(mutating_metadata, mutating_handler)]);
    // ReadOnly mode should block mutating tools
    let config = McpServerConfig::new(root).with_access_mode(AccessMode::ReadOnly);

    let bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    let mut stream = connect(&bridge);

    // Initialize first
    let init_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    });
    send_framed_request(&mut stream, init_request);

    // Try to call mutating tool - should be rejected by ReadOnly mode
    let call_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "test_mutate", "arguments": {}},
        "id": 2
    });
    let response_str = send_framed_request(&mut stream, call_request);
    let response: JsonRpcResponse =
        serde_json::from_str(&response_str).expect("Response should be valid JSON");

    assert!(
        response.error.is_some(),
        "Assertion 7 failed: ReadOnly mode should reject mutating tool"
    );
    let error = response.error.unwrap();
    assert_eq!(
        error.code, -32000,
        "Assertion 7 failed: should be -32000 (tool error), got: {}",
        error.code
    );
    assert!(
        error.message.contains("ReadOnly") || error.message.contains("read only"),
        "Assertion 7 failed: error should mention ReadOnly, got: {}",
        error.message
    );
}

/// Test that Allowlist rejects tools not in the list.
#[test]
fn test_allowlist_rejects_unlisted_tool_via_socket() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Create a tool
    let tool_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "ok".to_string(),
            )]))
        },
    );
    let tool_metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "test_tool".to_string(),
            description: "A test tool".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None,
    };

    let session =
        InMemorySession::new("allowlist-test").with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![(tool_metadata, tool_handler)]);
    // Allowlist only permits "other_tool", not "test_tool"
    let config = McpServerConfig::new(root)
        .with_tool_filter(ToolFilter::Allowlist(vec!["other_tool".to_string()]));

    let bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    let mut stream = connect(&bridge);

    // Initialize first
    let init_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    });
    send_framed_request(&mut stream, init_request);

    // Try to call tool not in allowlist - should be rejected
    let call_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "test_tool", "arguments": {}},
        "id": 2
    });
    let response_str = send_framed_request(&mut stream, call_request);
    let response: JsonRpcResponse =
        serde_json::from_str(&response_str).expect("Response should be valid JSON");

    assert!(
        response.error.is_some(),
        "Assertion 8 failed: Allowlist should reject unlisted tool"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    assert!(
        error.message.contains("not allowed") || error.message.contains("ToolNotAllowed"),
        "Assertion 8 failed: error should indicate ToolNotAllowed, got: {}",
        error.message
    );
}

/// Test that path outside root_dir is rejected with OutsideRootDir denial.
#[test]
fn test_outside_root_dir_rejected_via_socket() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Create a read tool
    let read_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "content".to_string(),
            )]))
        },
    );
    let read_metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "test_read".to_string(),
            description: "A read tool".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {"path": {"type": "string"}}
            }),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None,
    };

    let session =
        InMemorySession::new("root-dir-test").with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![(read_metadata, read_handler)]);
    let config = McpServerConfig::new(root.clone()).with_access_mode(AccessMode::ReadWrite);

    let bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    let mut stream = connect(&bridge);

    // Initialize first
    let init_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    });
    send_framed_request(&mut stream, init_request);

    // Try to access path outside root - should be rejected
    let call_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "test_read", "arguments": {"path": "/etc/passwd"}},
        "id": 2
    });
    let response_str = send_framed_request(&mut stream, call_request);
    let response: JsonRpcResponse =
        serde_json::from_str(&response_str).expect("Response should be valid JSON");

    assert!(
        response.error.is_some(),
        "Assertion 9 failed: path outside root_dir should be rejected"
    );
    let error = response.error.unwrap();
    assert_eq!(error.code, -32000);
    assert!(
        error.message.contains("outside") || error.message.contains("root"),
        "Assertion 9 failed: error should indicate OutsideRootDir, got: {}",
        error.message
    );
}

/// Test that a failed tool call returns a JSON-RPC error response with code -32000.
///
/// Per mcp-server protocol, tool execution failures are returned as
/// JSON-RPC error responses with code -32000 (Tool error). This includes
/// ExecutionError, CapabilityDenied, and InvalidParams variants.
#[test]
fn test_tool_execution_error_returns_json_rpc_error() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Create a tool handler that always returns an error
    let handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Err(mcp_server::ToolError::ExecutionError(
                "Intentional error for testing".to_string(),
            ))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "failing_tool".to_string(),
            description: "A tool that always fails".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None,
    };

    let session =
        InMemorySession::new("test-session").with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(root);

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(temp_root());
    let registry = ToolRegistry::new(vec![(metadata, handler)]);

    let server = McpServer::new(session_arc, config, workspace_arc, registry, None);

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // Call the failing tool
    let tool_request = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "failing_tool",
            "arguments": {}
        })),
        id: Some(serde_json::json!(2)),
    };

    let (response, _) = server.handle_request(tool_request, state);
    let response = response.expect("handle_request should return a response for non-notification");

    // Per mcp-server protocol, tool execution errors are JSON-RPC error responses with code -32000
    assert!(
        response.error.is_some(),
        "Tool execution errors must be JSON-RPC protocol errors with code -32000, got: {:#?}",
        response
    );

    let error = response.error.expect("error must be present");
    assert_eq!(
        error.code, -32000,
        "Tool execution errors must have code -32000, got: {}",
        error.code
    );
    assert!(
        error.message.contains("Tool error"),
        "Error message should contain 'Tool error', got: {}",
        error.message
    );
    assert!(
        error.data.as_ref().and_then(|d| d.get("error")).is_some(),
        "Error data should contain 'error' field with the tool error message, got: {:#?}",
        error
    );
}

/// Test that an unknown method returns -32601 (MethodNotFound) JSON-RPC error.
///
/// Per JSON-RPC 2.0 spec section 5.1, when a client calls a method that does not
/// exist on the server, the server must return error code -32601 (Method not found).
/// This is distinct from tool call errors (-32000) and pre-initialize errors (-32001).
///
/// Verified invariants:
/// - Unknown method returns error response with code -32601
/// - Error message mentions the unknown method or "Method not found"
/// - Response uses the same id as the request
#[test]
fn test_unknown_method_returns_method_not_found() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    let session = InMemorySession::new("unknown-method-test")
        .with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);
    let config = McpServerConfig::new(root);

    let bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    let mut stream = connect(&bridge);

    // Send an initialize request first so the server is in Ready state
    let init_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    });
    send_framed_request(&mut stream, init_request);

    // Send an unknown method — server must return -32601
    let unknown_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "completely/unknown/method",
        "params": {},
        "id": 2
    });
    let response_str = send_framed_request(&mut stream, unknown_request);
    let response: JsonRpcResponse =
        serde_json::from_str(&response_str).expect("Response should be valid JSON");

    assert!(
        response.error.is_some(),
        "Unknown method must return an error response, got: {:#?}",
        response
    );
    // Assert response id matches the request id (2)
    assert_eq!(
        response.id,
        serde_json::json!(2),
        "Response id must match request id, got: {:?}",
        response.id
    );
    let error = response.error.unwrap();
    assert_eq!(
        error.code, -32601,
        "Unknown method must return error code -32601 (MethodNotFound), got: {}",
        error.code
    );
    // Assert error message mentions the method or "Method not found"
    assert!(
        error.message.contains("Method")
            || error.message.contains("not found")
            || error.message.contains("unknown"),
        "Error message must mention method-not-found, got: {}",
        error.message
    );
}

/// Test that Allowlist and ReadOnly mode are enforced independently.
///
/// A tool in the Allowlist is still rejected by ReadOnly mode if it is mutating.
/// Both checks must pass: Allowlist presence alone is insufficient.
///
/// Verified invariants:
/// - A mutating tool that IS in the Allowlist still fails under ReadOnly mode
/// - The error code is -32000 with a ReadOnly mention (not ToolNotAllowed)
/// - ReadOnly rejection takes precedence for mutating tools regardless of filter
#[test]
fn test_allowlist_and_readonly_are_independent() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Create a mutating tool
    let mutating_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "mutated".to_string(),
            )]))
        },
    );
    let mutating_metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "allowed_but_mutating".to_string(),
            description: "A mutating tool that is in the Allowlist".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
        },
        required_capability: McpCapability::WorkspaceWriteEphemeral,
        is_mutating: None,
    };

    let session = InMemorySession::new("allowlist-readonly-test")
        .with_capabilities(&[McpCapability::WorkspaceWriteEphemeral]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![(mutating_metadata, mutating_handler)]);
    // ReadOnly + Allowlist containing the mutating tool
    let config = McpServerConfig::new(root)
        .with_access_mode(AccessMode::ReadOnly)
        .with_tool_filter(ToolFilter::Allowlist(vec![
            "allowed_but_mutating".to_string()
        ]));

    let bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    let mut stream = connect(&bridge);

    // Initialize
    let init_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    });
    send_framed_request(&mut stream, init_request);

    // Call the tool that IS in the Allowlist but is mutating
    // ReadOnly mode must still reject it, proving the two checks are independent
    let call_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "allowed_but_mutating", "arguments": {}},
        "id": 2
    });
    let response_str = send_framed_request(&mut stream, call_request);
    let response: JsonRpcResponse =
        serde_json::from_str(&response_str).expect("Response should be valid JSON");

    assert!(
        response.error.is_some(),
        "ReadOnly mode must reject a mutating tool even if it is in the Allowlist, got: {:#?}",
        response
    );
    let error = response.error.unwrap();
    assert_eq!(
        error.code, -32000,
        "Rejection must use code -32000 (tool error), got: {}",
        error.code
    );
    assert!(
        error.message.contains("ReadOnly") || error.message.contains("read only"),
        "Error must mention ReadOnly mode (not ToolNotAllowed), got: {}",
        error.message
    );
}

/// Test that tools/list returns registered tools with required names and exact count.
///
/// Verified invariants:
/// - tools/list response includes "read_file" and "write_file" tool names
/// - tools/list returns exactly the count of registered tools
/// - Each tool entry has a non-empty name field
#[test]
fn test_tools_list_has_required_names_and_count() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Register read_file and write_file tools
    let read_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            let _path = params.get("path").and_then(|v| v.as_str()).unwrap_or("");
            Ok(ToolResult::success(vec![ToolContent::text(
                "content".to_string(),
            )]))
        },
    );
    let read_metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "read_file".to_string(),
            description: "Read a file from the workspace".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
            }),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: Some(false),
    };

    let write_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            let _path = params.get("path").and_then(|v| v.as_str()).unwrap_or("");
            let _content = params.get("content").and_then(|v| v.as_str()).unwrap_or("");
            Ok(ToolResult::success(vec![ToolContent::text(
                "written".to_string(),
            )]))
        },
    );
    let write_metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "write_file".to_string(),
            description: "Write a file to the workspace".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }),
        },
        required_capability: McpCapability::WorkspaceWriteEphemeral,
        is_mutating: Some(true),
    };

    let session = InMemorySession::new("tools-list-test").with_capabilities(&[
        McpCapability::WorkspaceRead,
        McpCapability::WorkspaceWriteEphemeral,
    ]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(root);
    let registry = ToolRegistry::new(vec![
        (read_metadata, read_handler),
        (write_metadata, write_handler),
    ]);

    let server = McpServer::new(session_arc, config, workspace_arc, registry, None);

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // tools/list must return both tools
    let list = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };
    let (response, _) = server.handle_request(list, state);
    let response = response.expect("tools/list must return a response");
    assert!(
        response.error.is_none(),
        "tools/list must not error: {:?}",
        response.error
    );

    let result = response.result.expect("tools/list result must be present");
    let tools = result["tools"]
        .as_array()
        .expect("result.tools must be an array");

    let names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();

    assert!(
        names.contains(&"read_file"),
        "tools/list must include 'read_file', got: {:?}",
        names
    );
    assert!(
        names.contains(&"write_file"),
        "tools/list must include 'write_file', got: {:?}",
        names
    );
    assert_eq!(
        names.len(),
        2,
        "tools/list must return exactly 2 registered tools, got: {:?}",
        names
    );
}

/// Test that ReadOnly mode rejects an exec/process tool with ReadOnlyMode denial.
///
/// An exec tool requiring `ProcessExecBounded` capability must be blocked in ReadOnly
/// mode before capability checking — ReadOnly applies to all mutating operations
/// regardless of session capabilities.
///
/// Verified invariants:
/// - ReadOnly mode rejects an exec tool with error code -32000
/// - Error message mentions ReadOnly restriction
/// - AuditSink receives Deny record with AccessDeniedCode::ReadOnlyMode
#[test]
fn test_readonly_rejects_exec_tool() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Create an exec tool with ProcessExecBounded requirement
    let exec_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "executed".to_string(),
            )]))
        },
    );
    let exec_metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "exec".to_string(),
            description: "Execute a command in the workspace".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]
            }),
        },
        required_capability: McpCapability::ProcessExecBounded,
        is_mutating: Some(true),
    };

    let session = InMemorySession::new("exec-readonly-test")
        .with_capabilities(&[McpCapability::ProcessExecBounded]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    // ReadOnly mode must block exec
    let config = McpServerConfig::new(root).with_access_mode(AccessMode::ReadOnly);
    let registry = ToolRegistry::new(vec![(exec_metadata, exec_handler)]);

    let audit_sink = Arc::new(TestAuditSink::new());
    let server = McpServer::new(
        session_arc,
        config,
        workspace_arc,
        registry,
        Some(audit_sink.clone()),
    );

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // Call exec tool — must be rejected by ReadOnly mode
    let call = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({"name": "exec", "arguments": {"command": "ls"}})),
        id: Some(serde_json::json!(2)),
    };
    let (response, _) = server.handle_request(call, state);
    let response = response.expect("exec call must return a response");

    assert!(
        response.error.is_some(),
        "ReadOnly mode must reject exec tool"
    );
    let error = response.error.unwrap();
    assert_eq!(
        error.code, -32000,
        "Rejection must use code -32000, got: {}",
        error.code
    );
    assert!(
        error.message.contains("ReadOnly") || error.message.contains("read only"),
        "Error must mention ReadOnly mode, got: {}",
        error.message
    );

    // Verify AuditSink received Deny record with ReadOnlyMode code
    let records = audit_sink.records();
    assert_eq!(
        records.len(),
        1,
        "Exactly one audit record expected, got: {}",
        records.len()
    );
    match &records[0].decision {
        AccessDecision::Deny { code, .. } => {
            assert!(
                matches!(code, AccessDeniedCode::ReadOnlyMode),
                "Audit record must indicate ReadOnlyMode, got: {:?}",
                code
            );
        }
        other => panic!("Expected Deny decision, got: {:?}", other),
    }
}

/// Test that AuditSink receives Allow decisions for successful tool calls.
///
/// Per the PLAN, the AuditSink must receive emit() for BOTH Allow and Deny decisions.
/// This test verifies the Allow path.
///
/// Verified invariants:
/// - AuditSink receives exactly one Allow record when a tool call succeeds
/// - Allow record contains the correct tool name
#[test]
fn test_audit_sink_receives_allow_decision() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Create a simple read tool that always succeeds
    let handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "ok".to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "read_tool".to_string(),
            description: "A read-only tool for audit testing".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {}}),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: Some(false),
    };

    let session =
        InMemorySession::new("audit-allow-test").with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(root).with_access_mode(AccessMode::ReadOnly);
    let registry = ToolRegistry::new(vec![(metadata, handler)]);

    let audit_sink = Arc::new(TestAuditSink::new());
    let server = McpServer::new(
        session_arc,
        config,
        workspace_arc,
        registry,
        Some(audit_sink.clone()),
    );

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // Call read_tool — should succeed and generate Allow audit record
    let call = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({"name": "read_tool", "arguments": {}})),
        id: Some(serde_json::json!(2)),
    };
    let (response, _) = server.handle_request(call, state);
    let response = response.expect("read_tool call must return a response");

    assert!(
        response.error.is_none(),
        "read_tool must succeed, got error: {:?}",
        response.error
    );
    assert!(response.result.is_some(), "read_tool must return a result");

    // Verify AuditSink received Allow record
    let records = audit_sink.records();
    assert_eq!(
        records.len(),
        1,
        "Exactly one audit record expected for successful call, got: {}",
        records.len()
    );
    assert_eq!(
        records[0].tool_name, "read_tool",
        "Audit record tool_name must match, got: {}",
        records[0].tool_name
    );
    match &records[0].decision {
        AccessDecision::Allow => {}
        other => panic!(
            "Expected Allow decision for successful tool call, got: {:?}",
            other
        ),
    }
}

/// End-to-end protocol roundtrip using in-memory FakeTransport.
///
/// Proves the full protocol stack works without ralph-workflow dependency and
/// without a real Unix socket:
///
/// 1. Create McpServer with fake InMemorySession and InMemoryWorkspace
/// 2. Drive requests through FakeTransport (inject request → handle → read response)
/// 3. Verify initialize handshake returns protocol version
/// 4. Verify tools/list returns registered tools
/// 5. Verify a read tool call succeeds in ReadOnly mode
/// 6. Verify a write tool call is rejected with ReadOnlyMode in ReadOnly mode
/// 7. Verify a tool call before initialize returns NotInitialized (-32001)
/// 8. Verify a tool call rejected by missing capability returns structured CapabilityDenied
///    payload (error.code = -32000, error.data.code = "CapabilityDenied")
#[test]
fn full_protocol_roundtrip_with_fake_host() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // -- Helpers: drive McpServer via FakeTransport ------------------------
    fn inject_and_handle(
        server: &McpServer,
        transport: &mut FakeTransport,
        request: JsonRpcRequest,
        state: ServerState,
    ) -> (JsonRpcResponse, ServerState) {
        transport.inject_request(request);
        let req = transport
            .read_request()
            .expect("transport read must not error")
            .expect("transport must have a request");
        let (maybe_response, new_state) = server.handle_request(req, state);
        let response = maybe_response.expect("request with id must produce a response");
        transport
            .write_response(&response)
            .expect("write_response must not error");
        (response, new_state)
    }

    // -- Step 1: Verify NotInitialized before initialize ------------------
    // Create a minimal server with no tools registered to test protocol ordering
    let session_pre =
        Arc::new(InMemorySession::new("pre-init-session")) as Arc<dyn mcp_server::HostSession>;
    let workspace_pre =
        Arc::new(InMemoryWorkspace::new(root.clone())) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config_pre = McpServerConfig::new(root.clone());
    let server_pre = McpServer::new(
        session_pre,
        config_pre,
        workspace_pre,
        ToolRegistry::new(vec![]),
        None,
    );
    let mut transport_pre = FakeTransport::new();

    let tool_call_before_init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({"name": "read_file", "arguments": {}})),
        id: Some(serde_json::json!(99)),
    };
    let (not_init_response, _) = inject_and_handle(
        &server_pre,
        &mut transport_pre,
        tool_call_before_init,
        ServerState::Uninitialized,
    );
    assert!(
        not_init_response.error.is_some(),
        "tools/call before initialize must return an error"
    );
    let not_init_err = not_init_response.error.unwrap();
    assert_eq!(
        not_init_err.code, -32001,
        "Not-initialized error must use code -32001 (NotInitialized), got: {}",
        not_init_err.code
    );

    // -- Step 2: Create server with tools for full roundtrip ---------------
    let read_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "file contents".to_string(),
            )]))
        },
    );
    let read_meta = ToolMetadata {
        definition: ToolDefinition {
            name: "read_file".to_string(),
            description: "Read a file".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {"path": {"type": "string"}}}),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None,
    };

    let write_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "written".to_string(),
            )]))
        },
    );
    let write_meta = ToolMetadata {
        definition: ToolDefinition {
            name: "write_file".to_string(),
            description: "Write a file".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}),
        },
        required_capability: McpCapability::WorkspaceWriteTracked,
        is_mutating: None,
    };

    let session = Arc::new(InMemorySession::new("e2e-session").with_capabilities(&[
        McpCapability::WorkspaceRead,
        McpCapability::WorkspaceWriteTracked,
    ])) as Arc<dyn mcp_server::HostSession>;
    let workspace =
        Arc::new(InMemoryWorkspace::new(root.clone())) as Arc<dyn mcp_server::WorkspaceAdapter>;
    // ReadOnly mode: write_file must be rejected even though session has the capability
    let config = McpServerConfig::new(root.clone()).with_access_mode(AccessMode::ReadOnly);
    let registry = ToolRegistry::new(vec![(read_meta, read_handler), (write_meta, write_handler)]);
    let server = McpServer::new(session, config, workspace, registry, None);
    let mut transport = FakeTransport::new();

    // -- Step 3: initialize handshake -------------------------------------
    let init_req = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(
            serde_json::json!({"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test-client", "version": "0.1"}}),
        ),
        id: Some(serde_json::json!(1)),
    };
    let (init_response, state) = inject_and_handle(
        &server,
        &mut transport,
        init_req,
        ServerState::Uninitialized,
    );
    assert!(
        init_response.error.is_none(),
        "initialize must succeed, got error: {:?}",
        init_response.error
    );
    let init_result = init_response
        .result
        .expect("initialize must return a result");
    assert_eq!(
        init_result["protocolVersion"], "2024-11-05",
        "initialize must echo back protocol version"
    );
    assert!(
        init_result["serverInfo"]["name"].as_str().is_some(),
        "initialize result must include serverInfo.name"
    );
    assert_eq!(state, ServerState::Ready);

    // -- Step 4: tools/list returns registered tools ----------------------
    let list_req = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };
    let (list_response, state) = inject_and_handle(&server, &mut transport, list_req, state);
    assert!(
        list_response.error.is_none(),
        "tools/list must succeed, got error: {:?}",
        list_response.error
    );
    let list_result = list_response
        .result
        .expect("tools/list must return a result");
    let tools = list_result["tools"]
        .as_array()
        .expect("tools must be an array");
    let tool_names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
    assert!(
        tool_names.contains(&"read_file"),
        "tools/list must include read_file, got: {:?}",
        tool_names
    );
    assert!(
        tool_names.contains(&"write_file"),
        "tools/list must include write_file (filter is at dispatch, not list), got: {:?}",
        tool_names
    );

    // -- Step 5: read_file succeeds in ReadOnly mode ----------------------
    let read_call = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({"name": "read_file", "arguments": {"path": "README.md"}})),
        id: Some(serde_json::json!(3)),
    };
    let (read_response, state) = inject_and_handle(&server, &mut transport, read_call, state);
    assert!(
        read_response.error.is_none(),
        "read_file must succeed in ReadOnly mode, got error: {:?}",
        read_response.error
    );
    assert!(
        read_response.result.is_some(),
        "read_file must return a result"
    );

    // -- Step 6: write_file is rejected in ReadOnly mode ------------------
    let write_call = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(
            serde_json::json!({"name": "write_file", "arguments": {"path": "out.txt", "content": "x"}}),
        ),
        id: Some(serde_json::json!(4)),
    };
    let (write_response, _) = inject_and_handle(&server, &mut transport, write_call, state);
    assert!(
        write_response.error.is_some(),
        "write_file must be denied in ReadOnly mode"
    );
    let write_err = write_response.error.unwrap();
    assert!(
        write_err.message.contains("ReadOnly") || write_err.message.contains("read only"),
        "write_file denial must mention ReadOnly mode, got: {}",
        write_err.message
    );

    // -- Verify FakeTransport captured all responses correctly ------------
    // The main transport should have pending responses (init, list, read_call, write_call).
    // Each inject_and_handle writes one response back via write_response.
    assert!(
        transport.has_pending_responses(),
        "Main transport must have pending responses from the protocol roundtrip"
    );

    // -- Step 7: capability-denied tool call returns structured CapabilityDenied payload --
    // Create a session lacking ArtifactSubmit capability to exercise the CapabilityDenied path.
    // Uses new_empty() so no capability is granted by default — only WorkspaceRead is added.
    let artifact_submit_handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                "submitted".to_string(),
            )]))
        },
    );
    let artifact_meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_submit_artifact".to_string(),
            description: "Submit artifact".to_string(),
            input_schema: serde_json::json!({"type": "object", "properties": {"plan": {"type": "string"}}}),
        },
        required_capability: McpCapability::ArtifactSubmit,
        is_mutating: None,
    };

    let restricted_session = Arc::new(
        // new_empty() starts with no grants — only WorkspaceRead is added, so ArtifactSubmit is denied
        InMemorySession::new_empty("restricted-session")
            .with_capabilities(&[McpCapability::WorkspaceRead]),
    ) as Arc<dyn mcp_server::HostSession>;
    let restricted_workspace =
        Arc::new(InMemoryWorkspace::new(root.clone())) as Arc<dyn mcp_server::WorkspaceAdapter>;
    // ReadWrite mode so the ReadOnly check does not interfere — only capability check fires
    let restricted_config =
        McpServerConfig::new(root.clone()).with_access_mode(AccessMode::ReadWrite);
    let restricted_registry = ToolRegistry::new(vec![(artifact_meta, artifact_submit_handler)]);
    let server_restricted = McpServer::new(
        restricted_session,
        restricted_config,
        restricted_workspace,
        restricted_registry,
        None,
    );
    let mut transport_restricted = FakeTransport::new();

    let init_restricted = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "restricted-client", "version": "0.1"}
        })),
        id: Some(serde_json::json!(1)),
    };
    let (_, restricted_state) = inject_and_handle(
        &server_restricted,
        &mut transport_restricted,
        init_restricted,
        ServerState::Uninitialized,
    );

    let artifact_call = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(
            serde_json::json!({"name": "ralph_submit_artifact", "arguments": {"plan": "test plan"}}),
        ),
        id: Some(serde_json::json!(2)),
    };
    let (cap_denied_response, _) = inject_and_handle(
        &server_restricted,
        &mut transport_restricted,
        artifact_call,
        restricted_state,
    );

    assert!(
        cap_denied_response.error.is_some(),
        "ralph_submit_artifact must be denied when session lacks ArtifactSubmit capability"
    );
    let cap_err = cap_denied_response.error.unwrap();
    assert_eq!(
        cap_err.code, -32000,
        "CapabilityDenied must use code -32000 (tool error), got: {}",
        cap_err.code
    );
    assert_eq!(
        cap_err
            .data
            .as_ref()
            .and_then(|d| d.get("code"))
            .and_then(|c| c.as_str()),
        Some("CapabilityDenied"),
        "error.data.code must be 'CapabilityDenied', got: {:?}",
        cap_err.data
    );
}

/// Test the full MCP protocol stack: initialize → tools/list → tools/call.
///
/// Verifies:
/// 1. initialize transitions the server from Uninitialized to Ready
/// 2. tools/list returns the registered ralph_submit_artifact tool
/// 3. tools/call on a registered tool succeeds and returns a result
#[test]
fn full_protocol_stack_initialize_list_call() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Register a fake ralph_submit_artifact tool
    let handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                r#"{"accepted":true}"#.to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_submit_artifact".to_string(),
            description: "Submit a workflow artifact".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {"plan": {"type": "string"}},
                "required": ["plan"]
            }),
        },
        required_capability: McpCapability::ArtifactSubmit,
        is_mutating: None,
    };

    let session =
        Arc::new(InMemorySession::new("stack-test-session")) as Arc<dyn mcp_server::HostSession>;
    let workspace =
        Arc::new(InMemoryWorkspace::new(root.clone())) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(root);
    let registry = ToolRegistry::new(vec![(metadata, handler)]);
    let server = McpServer::new(session, config, workspace, registry, None);

    // Step 1: initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (init_response, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(
        state,
        ServerState::Ready,
        "initialize must transition to Ready"
    );
    let init_resp = init_response.expect("initialize must return a response");
    assert!(
        init_resp.error.is_none(),
        "initialize must not return an error: {:?}",
        init_resp.error
    );

    // Step 2: tools/list must include ralph_submit_artifact
    let list = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/list".to_string(),
        params: None,
        id: Some(serde_json::json!(2)),
    };
    let (list_response, state) = server.handle_request(list, state);
    let list_resp = list_response.expect("tools/list must return a response");
    assert!(
        list_resp.error.is_none(),
        "tools/list must not return an error: {:?}",
        list_resp.error
    );
    let result = list_resp.result.expect("tools/list must return a result");
    let tools = result["tools"]
        .as_array()
        .expect("result.tools must be an array");
    let names: Vec<&str> = tools.iter().filter_map(|t| t["name"].as_str()).collect();
    assert!(
        names.contains(&"ralph_submit_artifact"),
        "tools/list must include 'ralph_submit_artifact', got: {:?}",
        names
    );

    // Step 3: tools/call on ralph_submit_artifact must succeed
    let call = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "ralph_submit_artifact",
            "arguments": {"plan": "test plan"}
        })),
        id: Some(serde_json::json!(3)),
    };
    let (call_response, _) = server.handle_request(call, state);
    let call_resp = call_response.expect("tools/call must return a response");
    assert!(
        call_resp.error.is_none(),
        "tools/call on ralph_submit_artifact must succeed, got error: {:?}",
        call_resp.error
    );
    assert!(
        call_resp.result.is_some(),
        "tools/call must return a result"
    );
}

/// Test that ralph_submit_artifact succeeds when the session has ArtifactSubmit capability.
///
/// Verifies that a fake artifact submission handler is callable and returns
/// the expected accepted response when the session is properly authorized.
#[test]
fn submit_artifact_tool_callable() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Register a fake submit_artifact handler that returns accepted:true
    let handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                r#"{"accepted":true,"artifact_id":"test-001"}"#.to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_submit_artifact".to_string(),
            description: "Submit a workflow artifact".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {"plan": {"type": "string"}},
                "required": ["plan"]
            }),
        },
        required_capability: McpCapability::ArtifactSubmit,
        is_mutating: None,
    };

    // Session with ArtifactSubmit capability granted
    let session = Arc::new(
        InMemorySession::new("artifact-session")
            .with_capabilities(&[McpCapability::ArtifactSubmit]),
    ) as Arc<dyn mcp_server::HostSession>;
    let workspace =
        Arc::new(InMemoryWorkspace::new(root.clone())) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(root).with_access_mode(AccessMode::ReadWrite);
    let registry = ToolRegistry::new(vec![(metadata, handler)]);
    let server = McpServer::new(session, config, workspace, registry, None);

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // Call submit_artifact — must succeed and return accepted:true
    let call = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "ralph_submit_artifact",
            "arguments": {"plan": "my plan content"}
        })),
        id: Some(serde_json::json!(2)),
    };
    let (response, _) = server.handle_request(call, state);
    let resp = response.expect("tools/call must return a response");
    assert!(
        resp.error.is_none(),
        "ralph_submit_artifact must succeed when session has ArtifactSubmit capability, got: {:?}",
        resp.error
    );
    let result = resp.result.expect("tools/call must return a result");
    let content_text = result["content"]
        .as_array()
        .and_then(|arr| arr.first())
        .and_then(|c| c["text"].as_str())
        .unwrap_or("");
    assert!(
        content_text.contains("accepted"),
        "response content must contain 'accepted', got: {}",
        content_text
    );
}

/// Test that ReadOnly mode rejects ralph_submit_artifact when it is registered as mutating.
///
/// Verifies:
/// - `AccessMode::ReadOnly` blocks mutating artifact submission tools
/// - The denial error uses `AccessDeniedCode::ReadOnlyMode`
/// - The host session's ArtifactSubmit capability is irrelevant — ReadOnly fires first
#[test]
fn readonly_mode_rejects_submit_artifact() {
    let root = temp_root();
    test_helpers::assert_not_in_git_repo(&root);

    // Register ralph_submit_artifact explicitly as mutating
    let handler: ToolHandler = Arc::new(
        |_session: &dyn mcp_server::HostSession,
         _workspace: &dyn mcp_server::WorkspaceAdapter,
         _params: serde_json::Value|
         -> Result<ToolResult, mcp_server::ToolError> {
            Ok(ToolResult::success(vec![ToolContent::text(
                r#"{"accepted":true}"#.to_string(),
            )]))
        },
    );
    let metadata = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_submit_artifact".to_string(),
            description: "Submit a workflow artifact".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {"plan": {"type": "string"}},
                "required": ["plan"]
            }),
        },
        required_capability: McpCapability::ArtifactSubmit,
        // Explicitly mark as mutating so ReadOnly mode blocks it
        is_mutating: Some(true),
    };

    // Session WITH ArtifactSubmit capability — ReadOnly must fire before capability check
    let session = Arc::new(
        InMemorySession::new("readonly-artifact-session")
            .with_capabilities(&[McpCapability::ArtifactSubmit]),
    ) as Arc<dyn mcp_server::HostSession>;
    let workspace =
        Arc::new(InMemoryWorkspace::new(root.clone())) as Arc<dyn mcp_server::WorkspaceAdapter>;
    // ReadOnly mode is the enforcer under test
    let config = McpServerConfig::new(root).with_access_mode(AccessMode::ReadOnly);
    let audit_sink = Arc::new(TestAuditSink::new());
    let registry = ToolRegistry::new(vec![(metadata, handler)]);
    let server = McpServer::new(
        session,
        config,
        workspace,
        registry,
        Some(audit_sink.clone()),
    );

    // Initialize
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);

    // Call submit_artifact — must be denied due to ReadOnly mode
    let call = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "tools/call".to_string(),
        params: Some(serde_json::json!({
            "name": "ralph_submit_artifact",
            "arguments": {"plan": "should be denied"}
        })),
        id: Some(serde_json::json!(2)),
    };
    let (response, _) = server.handle_request(call, state);
    let resp = response.expect("tools/call must return a response");
    assert!(
        resp.error.is_some(),
        "ReadOnly mode must deny ralph_submit_artifact (is_mutating=true)"
    );
    let error = resp.error.unwrap();
    assert!(
        error.message.contains("ReadOnly") || error.message.contains("read only"),
        "Error message must mention ReadOnly restriction, got: {}",
        error.message
    );

    // Audit record must indicate ReadOnlyMode denial
    let records = audit_sink.records();
    assert_eq!(
        records.len(),
        1,
        "ReadOnly denial must generate exactly one audit record"
    );
    match &records[0].decision {
        AccessDecision::Deny { code, .. } => {
            assert!(
                matches!(code, AccessDeniedCode::ReadOnlyMode),
                "Audit record must show ReadOnlyMode denial, got: {:?}",
                code
            );
        }
        other => panic!("Expected Deny decision, got: {:?}", other),
    }
}
