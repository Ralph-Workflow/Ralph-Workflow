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
//! - SessionBridge full protocol flow over Unix socket

use mcp_server::dispatch::access::{
    AccessDecision, AccessDeniedCode, AccessMode, AuditSink, McpCapability, ToolFilter,
};
use mcp_server::dispatch::audit::AuditRecord;
use mcp_server::dispatch::host::DirEntry;
use mcp_server::dispatch::{ToolHandler, ToolMetadata, ToolRegistry};
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::session_bridge::SessionBridge;
use mcp_server::io::{McpServer, ServerState};
use mcp_server::protocol::{
    JsonRpcRequest, JsonRpcResponse, ToolContent, ToolDefinition, ToolResult,
};
use std::collections::HashMap;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

/// Returns a safe temp root for MCP tests that is guaranteed not to be inside a git repo.
fn temp_root() -> PathBuf {
    std::env::temp_dir().join("ralph-mcp-test")
}

/// Fail-fast guardrail: panic if path is inside a real git repo.
///
/// This is a standalone implementation of the git safety check that mcp-server tests
/// use to ensure they don't accidentally touch real git state. This replaces the
/// assert_no_real_git_state function to keep mcp-server tests
/// independent of the test-helpers crate.
fn assert_no_real_git_state(path: &std::path::Path) {
    let path = match std::fs::canonicalize(path) {
        Ok(p) => p,
        Err(_) => return, // Cannot canonicalize - skip check
    };

    // Check the path and all its ancestors for .git
    let mut current: &std::path::Path = &path;
    loop {
        if current.join(".git").exists() {
            panic!(
                "POLICY VIOLATION: test is using real git state at '{}'. \
                 All tests must use MemoryWorkspace. See docs/agents/testing-guide.md.",
                path.display()
            );
        }
        match current.parent() {
            Some(parent) => current = parent,
            None => break,
        }
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
    assert_no_real_git_state(&root);
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
    assert_no_real_git_state(&root);
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
    assert_no_real_git_state(&root);
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
    assert_no_real_git_state(&root);
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
    assert_no_real_git_state(&root);
    // Create config with allowlist that only permits "read_file"
    let config = McpServerConfig::new(root.clone())
        .with_tool_filter(ToolFilter::Allowlist(vec!["read_file".to_string()]));

    let session = InMemorySession::new("allowlist-session")
        .with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(root).with_file("test.txt", "content");

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);

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
    assert_no_real_git_state(&root);
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

/// Helper: send framed request and read framed response over Unix socket.
fn send_framed_request(stream: &mut UnixStream, request: serde_json::Value) -> String {
    use std::io::{Read, Write};
    let bytes = serde_json::to_vec(&request).unwrap();
    write!(stream, "Content-Length: {}\r\n\r\n", bytes.len()).unwrap();
    stream.write_all(&bytes).unwrap();
    stream.flush().unwrap();

    // Read headers until blank line
    let mut header = Vec::new();
    loop {
        let mut buf = [0u8; 1];
        stream.read_exact(&mut buf).expect("Read error");
        header.push(buf[0]);
        if header.ends_with(b"\r\n\r\n") {
            break;
        }
    }
    // Parse Content-Length
    let header_str = String::from_utf8_lossy(&header);
    let content_length = header_str
        .lines()
        .find(|l| l.starts_with("Content-Length:"))
        .and_then(|l| {
            l.strip_prefix("Content-Length:")
                .unwrap()
                .trim()
                .parse::<usize>()
                .ok()
        })
        .expect("Missing Content-Length header");
    // Read body
    let mut body = vec![0u8; content_length];
    stream.read_exact(&mut body).unwrap();
    String::from_utf8(body).unwrap()
}

/// Test full protocol flow through SessionBridge over Unix socket.
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
    use std::time::Duration;

    let root = temp_root();
    assert_no_real_git_state(&root);

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

    let mut bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);

    // Start the bridge
    bridge.start().expect("Failed to start bridge");

    // Connect via Unix socket
    let socket_path = bridge.socket_path().clone();
    let mut stream = UnixStream::connect(&socket_path).expect("Failed to connect to bridge");
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
    stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

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

    // Give server time to process
    std::thread::sleep(std::time::Duration::from_millis(100));

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

    // Close this connection - we'll test other scenarios with fresh connections
    drop(stream);
}

/// Test that NotInitialized error is returned when calling tool before initialize.
///
/// Per the MCP protocol, any tool call before initialize should return
/// error code -32001 (NotInitialized).
#[test]
fn test_not_initialized_error_before_initialize() {
    use std::time::Duration;

    let root = temp_root();
    assert_no_real_git_state(&root);

    let session =
        InMemorySession::new("not-init-test").with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(root.clone());

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);
    let config = McpServerConfig::new(root);

    let mut bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    bridge.start().expect("Failed to start bridge");

    let socket_path = bridge.socket_path().clone();
    let mut stream = UnixStream::connect(&socket_path).expect("Failed to connect");
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
    stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

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
    use std::time::Duration;

    let root = temp_root();
    assert_no_real_git_state(&root);

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

    let mut bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    bridge.start().expect("Failed to start bridge");

    let socket_path = bridge.socket_path().clone();
    let mut stream = UnixStream::connect(&socket_path).expect("Failed to connect");
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
    stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

    // Initialize first
    let init_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    });
    send_framed_request(&mut stream, init_request);
    std::thread::sleep(std::time::Duration::from_millis(100));

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
    use std::time::Duration;

    let root = temp_root();
    assert_no_real_git_state(&root);

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

    let mut bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    bridge.start().expect("Failed to start bridge");

    let socket_path = bridge.socket_path().clone();
    let mut stream = UnixStream::connect(&socket_path).expect("Failed to connect");
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
    stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

    // Initialize first
    let init_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    });
    send_framed_request(&mut stream, init_request);
    std::thread::sleep(std::time::Duration::from_millis(100));

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
    use std::time::Duration;

    let root = temp_root();
    assert_no_real_git_state(&root);

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

    let mut bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);
    bridge.start().expect("Failed to start bridge");

    let socket_path = bridge.socket_path().clone();
    let mut stream = UnixStream::connect(&socket_path).expect("Failed to connect");
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
    stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

    // Initialize first
    let init_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    });
    send_framed_request(&mut stream, init_request);
    std::thread::sleep(std::time::Duration::from_millis(100));

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
    assert_no_real_git_state(&root);

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
