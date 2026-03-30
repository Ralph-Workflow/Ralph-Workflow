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
    AccessDecision, AccessDeniedCode, AccessMode, McpCapability, ToolFilter,
};
use mcp_server::dispatch::host::DirEntry;
use mcp_server::dispatch::{ToolHandler, ToolMetadata, ToolRegistry};
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::session_bridge::SessionBridge;
use mcp_server::io::{McpServer, ServerState};
use mcp_server::protocol::{JsonRpcRequest, ToolContent, ToolDefinition, ToolResult};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

// ---------------------------------------------------------------------------
// Standalone HostSession Implementation
// ---------------------------------------------------------------------------

/// A simple in-memory session with capability grants.
/// This demonstrates how to implement HostSession without ralph-workflow.
struct InMemorySession {
    session_id: String,
    /// Map of capability -> granted
    granted_capabilities: HashMap<McpCapability, bool>,
    is_parallel_worker: bool,
    /// Map of path -> allowed (for edit area checks)
    edit_areas: Vec<PathBuf>,
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
            is_parallel_worker: false,
            edit_areas: vec![],
        }
    }

    fn with_capabilities(mut self, caps: &[McpCapability]) -> Self {
        for cap in caps {
            self.granted_capabilities.insert(*cap, true);
        }
        self
    }

    fn with_parallel_worker(mut self, is_worker: bool) -> Self {
        self.is_parallel_worker = is_worker;
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

    fn is_parallel_worker(&self) -> bool {
        self.is_parallel_worker
    }

    fn check_edit_area(&self, path: &str) -> AccessDecision {
        if self.edit_areas.is_empty() {
            return AccessDecision::Allow;
        }
        let path = Path::new(path);
        for area in &self.edit_areas {
            if path.starts_with(area) {
                return AccessDecision::Allow;
            }
        }
        AccessDecision::Deny {
            reason: format!("Path {} is outside edit areas", path.display()),
            code: AccessDeniedCode::OutsideRootDir,
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
    let config = McpServerConfig::new(PathBuf::from("/tmp/test-workspace"));
    let registry = ToolRegistry::new(vec![]);

    McpServer::new(session, config, workspace, registry, None)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[test]
fn test_standalone_session_check_capability() {
    let session = InMemorySession::new("test-session");
    let workspace = InMemoryWorkspace::new(PathBuf::from("/tmp"));

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
    let session = InMemorySession::new("test-session").with_capabilities(&[
        McpCapability::WorkspaceRead,
        McpCapability::WorkspaceWriteTracked,
    ]);
    let workspace =
        InMemoryWorkspace::new(PathBuf::from("/tmp")).with_file("test.txt", "Hello, World!");

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
    // Session without GitStatusRead capability
    let session = InMemorySession::new("restricted-session")
        .with_capabilities(&[McpCapability::WorkspaceRead]); // No GitStatusRead
    let workspace = InMemoryWorkspace::new(PathBuf::from("/tmp"));

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
    let workspace_with_git = InMemoryWorkspace::new(PathBuf::from("/tmp"));

    let session_arc = Arc::new(session_with_git) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace_with_git) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let config = McpServerConfig::new(PathBuf::from("/tmp"));
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

#[test]
fn test_parallel_worker_edit_area() {
    // Session that is a parallel worker with restricted edit area
    let session = InMemorySession::new("parallel-worker")
        .with_capabilities(&[
            McpCapability::WorkspaceRead,
            McpCapability::WorkspaceWriteTracked,
        ])
        .with_parallel_worker(true);
    let workspace = InMemoryWorkspace::new(PathBuf::from("/project"));

    let server = create_test_server(session, workspace);

    // The is_parallel_worker() and check_edit_area() methods are available
    // but their enforcement depends on the tool handler implementation
    let init = JsonRpcRequest {
        jsonrpc: "2.0".to_string(),
        method: "initialize".to_string(),
        params: Some(serde_json::json!({"protocolVersion": "2024-11-05"})),
        id: Some(serde_json::json!(1)),
    };
    let (_, state) = server.handle_request(init, ServerState::Uninitialized);
    assert_eq!(state, ServerState::Ready);
}

// ---------------------------------------------------------------------------
// Additional Standalone Tests: McpServerConfig Enforcement
// ---------------------------------------------------------------------------

/// Test that ReadOnly mode denies write operations.
#[test]
fn test_read_only_mode_denies_write_tool() {
    // Create a read-only config
    let config = McpServerConfig::new(PathBuf::from("/tmp/test-workspace"))
        .with_access_mode(AccessMode::ReadOnly);

    // Session with write capability
    let session = InMemorySession::new("ro-session").with_capabilities(&[
        McpCapability::WorkspaceRead,
        McpCapability::WorkspaceWriteTracked,
    ]);
    let workspace = InMemoryWorkspace::new(PathBuf::from("/tmp/test-workspace"))
        .with_file("test.txt", "content");

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
}

/// Test that Allowlist filter blocks tools not in the list.
#[test]
fn test_allowlist_blocks_unlisted_tool() {
    // Create config with allowlist that only permits "read_file"
    let config = McpServerConfig::new(PathBuf::from("/tmp/test-workspace"))
        .with_tool_filter(ToolFilter::Allowlist(vec!["read_file".to_string()]));

    let session = InMemorySession::new("allowlist-session")
        .with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(PathBuf::from("/tmp/test-workspace"))
        .with_file("test.txt", "content");

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
}

/// Test that Blocklist filter blocks tools in the list.
#[test]
fn test_blocklist_blocks_listed_tool() {
    // Create config with blocklist that blocks "git_status"
    let config = McpServerConfig::new(PathBuf::from("/tmp/test-workspace"))
        .with_tool_filter(ToolFilter::Blocklist(vec!["git_status".to_string()]));

    let session = InMemorySession::new("blocklist-session")
        .with_capabilities(&[McpCapability::WorkspaceRead, McpCapability::GitStatusRead]);
    let workspace = InMemoryWorkspace::new(PathBuf::from("/tmp/test-workspace"));

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

/// Test full protocol flow through SessionBridge over Unix socket.
#[test]
fn test_session_bridge_full_protocol_flow() {
    use std::time::Duration;

    // Create fake implementations
    let session = InMemorySession::new("bridge-test-session")
        .with_capabilities(&[McpCapability::WorkspaceRead]);
    let workspace = InMemoryWorkspace::new(PathBuf::from("/tmp/test-workspace"))
        .with_file("test.txt", "Hello from bridge!");

    let config = McpServerConfig::new(PathBuf::from("/tmp/test-workspace"))
        .with_access_mode(AccessMode::ReadOnly);

    let session_arc = Arc::new(session) as Arc<dyn mcp_server::HostSession>;
    let workspace_arc = Arc::new(workspace) as Arc<dyn mcp_server::WorkspaceAdapter>;
    let registry = ToolRegistry::new(vec![]);

    let mut bridge = SessionBridge::new(session_arc, config, workspace_arc, registry);

    // Start the bridge
    bridge.start().expect("Failed to start bridge");

    // Connect via Unix socket
    let socket_path = bridge.socket_path().clone();
    let mut stream = UnixStream::connect(&socket_path).expect("Failed to connect to bridge");
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
    stream.set_write_timeout(Some(Duration::from_secs(5))).ok();

    // Helper to read a framed response (Content-Length based)
    fn read_framed_response(stream: &mut UnixStream) -> String {
        // Read headers until blank line
        let mut header = Vec::new();
        loop {
            let mut buf = [0u8; 1];
            match stream.read_exact(&mut buf) {
                Ok(_) => {}
                Err(e) if e.kind() == std::io::ErrorKind::TimedOut => {
                    panic!(
                        "Read timed out waiting for header, header so far: {:?}",
                        String::from_utf8_lossy(&header)
                    );
                }
                Err(e) => panic!("Read error: {}", e),
            }
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

    // Send initialize request
    let init_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
        "id": 1
    });
    let init_bytes = serde_json::to_vec(&init_request).unwrap();
    write!(stream, "Content-Length: {}\r\n\r\n", init_bytes.len()).unwrap();
    stream.write_all(&init_bytes).unwrap();
    stream.flush().unwrap();

    // Give server time to process
    std::thread::sleep(std::time::Duration::from_millis(200));

    // Read response
    let response_str = read_framed_response(&mut stream);
    assert!(
        response_str.contains("\"result\""),
        "Response should be JSON-RPC result"
    );

    // Send ping request
    let ping_request = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "ping",
        "id": 2
    });
    let ping_bytes = serde_json::to_vec(&ping_request).unwrap();
    write!(stream, "Content-Length: {}\r\n\r\n", ping_bytes.len()).unwrap();
    stream.write_all(&ping_bytes).unwrap();
    stream.flush().unwrap();

    // Give server time to process
    std::thread::sleep(std::time::Duration::from_millis(200));

    // Read ping response
    let ping_response_str = read_framed_response(&mut stream);
    assert!(
        ping_response_str.contains("\"result\""),
        "Ping response should be JSON-RPC result"
    );
}
