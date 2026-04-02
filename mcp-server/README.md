# mcp-server

MCP (Model Context Protocol) server implementation for Ralph workflow orchestration.

A standalone crate that provides MCP server functionality with typed capability gating,
configurable access control, and clear boundary between protocol handling and I/O.

## Overview

This crate provides the MCP server that enables Ralph to communicate with AI agents
(Claude Code, Codex, OpenCode) via JSON-RPC over stdio or Unix sockets.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Agent (Claude Code, Codex)                    │
└─────────────────────────┬───────────────────────────────────────────┘
                          │ JSON-RPC over stdio or Unix socket
┌─────────────────────────▼───────────────────────────────────────────┐
│                          mcp-server                                  │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  protocol/   — Pure JSON-RPC types, tool definitions        │  │
│  │               No side effects, serializable/deserializable    │  │
│  └────────────────────────────┬────────────────────────────────┘  │
│  ┌────────────────────────────▼────────────────────────────────┐  │
│  │  dispatch/   — Tool registry, handler dispatch,             │  │
│  │               capability gating, routing                      │  │
│  └────────────────────────────┬────────────────────────────────┘  │
│  ┌────────────────────────────▼────────────────────────────────┐  │
│  │  io/         — Transport framing (Content-Length),           │  │
│  │               socket handling, stdio I/O                     │  │
│  │               [Boundary module — Dylint-recognized]          │  │
│  └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              │    Adapter Traits     │
              │  (implemented by host) │
              ├───────────────────────┤
              │  HostSession          │  — capability decisions
              │  WorkspaceAdapter     │  — file read/write
              │  AuditSink           │  — audit records
              └───────────────────────┘
```

### Module Organization

| Module | Type | Purpose |
|--------|------|---------|
| `protocol/` | Pure | JSON-RPC types, capability declarations, tool definitions |
| `io/` | Boundary | Transport framing, socket handling, stdio I/O |
| `dispatch/` | Application | Tool registry, handler dispatch, capability gating |

### Boundary Rules

- **`protocol/`** — Pure data types, no side effects
- **`io/`** — Boundary module (Dylint-recognized), all actual I/O lives here
- **`dispatch/`** — Application logic, handlers are pure functions

## McpServerConfig

`McpServerConfig` establishes the server's initialization contract. It is set once at
construction and cannot be changed.

### Configuration Fields

| Field | Type | Meaning | Example |
|-------|------|---------|---------|
| `root_dir` | `PathBuf` | Authorized directory boundary. All file operations must resolve within this directory. | `"/home/user/project"` |
| `access_mode` | `AccessMode` | Operations permitted. `ReadOnly` or `ReadWrite`. | `AccessMode::ReadWrite` |
| `tool_filter` | `ToolFilter` | Tool dispatch filter. `Allowlist(names)` or `Blocklist(names)`. Use `Blocklist(vec![])` to allow all tools. | `ToolFilter::Blocklist(vec!["exec_command"])` |

### Configuration Examples

**ReadOnly config** (for documentation assistants, read-only consumers):

```rust
use mcp_server::io::access::McpServerConfig;
use mcp_server::dispatch::access::AccessMode;

let config = McpServerConfig::new("/home/user/docs")
    .with_access_mode(AccessMode::ReadOnly);
```

**ReadWrite config** (for full workflow orchestration):

```rust
use mcp_server::io::access::McpServerConfig;
use mcp_server::dispatch::access::{AccessMode, ToolFilter};

let config = McpServerConfig::new("/home/user/project")
    .with_access_mode(AccessMode::ReadWrite)
    .with_tool_filter(ToolFilter::Blocklist(vec![
        "ralph_exec_command".to_string()
    ]));
```

## Access Control Model

`mcp-server` enforces its own access control model. It does not delegate access decisions
upward to the host — it enforces them at the protocol boundary before dispatching to any handler.

### Access Decision Types

| Decision | Source | Delegatable? |
|----------|--------|--------------|
| `ReadOnlyMode` | `mcp-server` (access_mode enforcement) | No |
| `OutsideRootDir` | `mcp-server` (path boundary check) | No |
| `ToolNotAllowed` | `mcp-server` (tool_filter check) | No |
| `NotInitialized` | `mcp-server` (protocol state) | No |
| `CapabilityDenied` | Host (via `HostSession::check_capability`) | **Yes** |

### Enforcement Order

When a `tools/call` request arrives:

1. **Tool filter check** — Is the tool in the allowlist, or blocked by blocklist?
   If not allowed, returns `ToolNotAllowed`. Host is not consulted.
2. **Access mode check** — Does the access mode permit this operation?
   If `ReadOnly` and the tool is mutating, returns `ReadOnlyMode`. Host is not consulted.
3. **Path boundary check** — Does the path resolve within `root_dir`?
   If outside, returns `OutsideRootDir`. Host is not consulted.
4. **Capability check** — Does the session have the required capability?
   Calls `host.check_capability(cap)`. Only this check goes to the host.

### Boundary Ownership

**`mcp-server` owns:**
- Tool filter enforcement (Allowlist/Blocklist)
- Access mode enforcement (ReadOnly/ReadWrite)
- Path boundary enforcement (root_dir)
- Protocol state machine (initialize before tools/call)
- Error code assignment for its own denials

**Host owns:**
- `CapabilityDenied` only — mapping `McpCapability` to the host's internal policy

## Adapter Trait Pattern

To use `mcp-server` with a host application, implement these traits:

### HostSession

Provides session identity and capability decisions.

```rust
use mcp_server::dispatch::access::{AccessDecision, AccessDeniedCode, McpCapability};

pub trait HostSession: Send + Sync {
    /// Unique session identifier.
    fn session_id(&self) -> &str;

    /// Check if the session has the given capability.
    /// Return `Allow` to permit, `Deny { reason, code }` to reject.
    /// Only `CapabilityDenied` should be returned here — all other codes
    /// are generated internally by mcp-server.
    fn check_capability(&self, cap: McpCapability) -> AccessDecision;

    /// Whether this session is a parallel worker (affects edit area restrictions).
    fn is_parallel_worker(&self) -> bool;

    /// Check if the given path is within the session's edit area.
    fn check_edit_area(&self, path: &str) -> AccessDecision;
}
```

### WorkspaceAdapter

Provides file system operations.

```rust
use std::path::Path;

pub trait WorkspaceAdapter: Send + Sync {
    /// Read file contents.
    fn read(&self, path: &Path) -> Result<String, String>;

    /// Write file contents.
    fn write(&self, path: &Path, content: &str) -> Result<(), String>;

    /// Check if file exists.
    fn exists(&self, path: &Path) -> bool;

    /// List directory entries.
    fn read_dir(&self, path: &Path) -> Result<Vec<DirEntry>, String>;
}
```

### AuditSink

Receives audit records from the dispatch layer.

```rust
pub trait AuditSink: Send + Sync {
    /// Emit an audit record.
    fn emit(&self, record: crate::dispatch::audit::AuditRecord);

    /// Flush buffered records (optional).
    fn flush(&self) {}
}
```

### Minimal Third-Party Host Example

```rust
use mcp_server::io::{McpServer, ServerState};
use mcp_server::io::access::McpServerConfig;
use mcp_server::dispatch::{HostSession, WorkspaceAdapter, ToolRegistry};
use std::sync::Arc;
use std::path::PathBuf;

struct MySession {
    session_id: String,
    capabilities: Vec<McpCapability>,
}

impl HostSession for MySession {
    fn session_id(&self) -> &str { &self.session_id }
    fn check_capability(&self, cap: McpCapability) -> AccessDecision {
        if self.capabilities.contains(&cap) {
            AccessDecision::Allow
        } else {
            AccessDecision::Deny {
                reason: format!("Missing {:?}", cap),
                code: AccessDeniedCode::CapabilityDenied,
            }
        }
    }
    fn is_parallel_worker(&self) -> bool { false }
    fn check_edit_area(&self, _path: &str) -> AccessDecision {
        AccessDecision::Allow
    }
}

struct MyWorkspace {
    root: PathBuf,
    files: std::collections::HashMap<PathBuf, String>,
}

impl WorkspaceAdapter for MyWorkspace {
    fn read(&self, path: &Path) -> Result<String, String> {
        self.files.get(path).cloned()
            .ok_or_else(|| "Not found".to_string())
    }
    fn write(&self, path: &Path, content: &str) -> Result<(), String> {
        self.files.insert(path.to_path_buf(), content.to_string());
        Ok(())
    }
    fn exists(&self, path: &Path) -> bool {
        self.files.contains_key(path)
    }
    fn read_dir(&self, _path: &Path) -> Result<Vec<DirEntry>, String> {
        Ok(vec![])
    }
}

// Create server
let session = Arc::new(MySession { session_id: "test".to_string(), capabilities: vec![] });
let workspace = Arc::new(MyWorkspace { root: PathBuf::from("/tmp"), files: std::collections::HashMap::new() });
let config = McpServerConfig::new("/tmp");
let registry = ToolRegistry::new(vec![]);
let server = McpServer::new(session, config, workspace, registry, None);

// Handle request
let request = JsonRpcRequest { jsonrpc: "2.0".to_string(), method: "ping".to_string(), params: None, id: Some(serde_json::json!(1)) };
let (response, state) = server.handle_request(request, ServerState::Uninitialized);
```

## RPC Contract

### JSON-RPC 2.0 Framing

All messages use Content-Length framing:

```
Content-Length: <byte-count>

<JSON body>
```

### Error Codes

| Code | Meaning | Source |
|------|---------|--------|
| -32700 | Parse error — invalid JSON | `mcp-server` |
| -32600 | Invalid request — missing required fields | `mcp-server` |
| -32601 | Method not found — unknown method | `mcp-server` |
| -32602 | Invalid params — wrong parameter types | `mcp-server` |
| -32603 | Internal error — server-side failure (not a tool error) | `mcp-server` |
| -32001 | Server not initialized | `mcp-server` |

### Methods

#### initialize

Handshake to establish protocol version and server capabilities.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": { "name": "Claude Code", "version": "1.0" }
  },
  "id": 1
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `protocolVersion` | string | Yes | Client-supported protocol version |
| `capabilities` | object | No | Client capability declarations |
| `clientInfo.name` | string | No | Client application name |
| `clientInfo.version` | string | No | Client application version |

**Response:**
```json
{
  "jsonrpc": "2.0",
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": { "tools": { "listChanged": true } },
    "serverInfo": { "name": "ralph-mcp", "version": "0.7.13" }
  },
  "id": 1
}
```

| Field | Type | Description |
|-------|------|-------------|
| `protocolVersion` | string | Server-supported protocol version |
| `capabilities.tools.listChanged` | boolean | Whether tools list may change |
| `serverInfo.name` | string | Server application name |
| `serverInfo.version` | string | Server application version |

| Property | Value |
|----------|-------|
| McpCapability required | None |
| ReadOnly-safe | **Yes** |
| Side effects | No (handshake only) |
| Idempotent | **Yes** (same result on repeated calls) |

---

#### ping

Liveness check. No processing, returns null.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "method": "ping",
  "id": 2
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "result": null,
  "id": 2
}
```

| Property | Value |
|----------|-------|
| McpCapability required | None |
| ReadOnly-safe | **Yes** |
| Side effects | No |
| Idempotent | **Yes** |

---

#### notifications/initialized

Client notification that the initialize handshake is complete and the client is ready to send tool requests.

**Notification (no response sent):**
```json
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized",
  "params": {}
}
```

This is a standard MCP protocol notification. The server does not send a response (per JSON-RPC 2.0 notification semantics).

| Property | Value |
|----------|-------|
| McpCapability required | None |
| ReadOnly-safe | **Yes** |
| Side effects | No |
| Idempotent | **Yes** |

---

#### tools/list

List all available tools the session is authorized to use.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/list",
  "params": null,
  "id": 3
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "result": {
    "tools": [
      {
        "name": "ralph_workspace_read_file",
        "description": "Read a file from the workspace",
        "inputSchema": {
          "type": "object",
          "properties": { "path": { "type": "string" } },
          "required": ["path"]
        }
      }
    ]
  },
  "id": 3
}
```

| Property | Value |
|----------|-------|
| McpCapability required | None |
| ReadOnly-safe | **Yes** |
| Side effects | No (read-only enumeration) |
| Idempotent | **Yes** |

---

#### tools/call

Invoke a tool by name with parameters.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "ralph_workspace_read_file",
    "arguments": { "path": "src/main.rs" }
  },
  "id": 4
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Tool identifier |
| `arguments` | object | No | Tool-specific parameters |

**Success Response:**
```json
{
  "jsonrpc": "2.0",
  "result": {
    "content": [
      { "type": "text", "text": "fn main() { ... }" }
    ],
    "isError": false
  },
  "id": 4
}
```

**Error Response (tool execution failed):**
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32000,
    "message": "Tool error: File not found: src/main.rs",
    "data": { "error": "File not found: src/main.rs" }
  },
  "id": 4
}
```

**Error Response (access denied):**
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32000,
    "message": "Access denied: [CapabilityDenied] Missing GitStatusRead capability",
    "data": { "reason": "Missing GitStatusRead capability", "code": "CapabilityDenied" }
  },
  "id": 4
}
```

| Property | Value |
|----------|-------|
| McpCapability required | **Tool-specific** (varies by tool) |
| ReadOnly-safe | **No** (tool-dependent) |
| Side effects | **Yes** (tool-dependent) |
| Idempotent | **No** (most tools are not idempotent) |

### RPC Contract Reference

Consolidated reference for all MCP JSON-RPC methods:

| Method | Request Shape | Response Shape | Errors | McpCapability Required | ReadOnly-Safe | Side Effects | Idempotent |
|--------|---------------|---------------|--------|----------------------|---------------|--------------|------------|
| `initialize` | `{protocolVersion: string, capabilities: object, clientInfo: {name: string, version: string}}` | `{protocolVersion: string, capabilities: {tools: {listChanged: boolean}}, serverInfo: {name: string, version: string}}` | `-32600` (invalid request), `-32602` (invalid params) | None | **Yes** | No | **Yes** |
| `ping` | `{}` (or null) | `null` | None | None | **Yes** | No | **Yes** |
| `notifications/initialized` | `{}` | None (notification) | N/A | None | **Yes** | No | **Yes** |
| `tools/list` | `null` or `{}` | `{tools: [{name, description, inputSchema}]}` | `-32001` (not initialized) | None | **Yes** | No | **Yes** |
| `tools/call` | `{name: string, arguments: object}` | `{content: [{type: "text", text: string}], isError: boolean}` | `-32000` (tool error / access denied), `-32001` (not initialized), `-32602` (invalid params) | **Tool-specific** | **No** | **Yes** | **No** |

### Error Code Reference

| Code | Meaning | Source |
|------|---------|--------|
| `-32700` | Parse error — invalid JSON | `mcp-server` |
| `-32600` | Invalid request — missing required fields | `mcp-server` |
| `-32601` | Method not found — unknown method | `mcp-server` |
| `-32602` | Invalid params — wrong parameter types | `mcp-server` |
| `-32603` | Internal error — server-side failure (not a tool error) | `mcp-server` |
| `-32001` | Server not initialized — call `initialize` first | `mcp-server` |
| `-32000` | Tool error — tool execution failed or access denied | `mcp-server` |

## Registered Tools

The following tools are registered in Ralph's MCP server. Each tool's contract:

| Tool | McpCapability | Access Mode | ReadOnly-Safe | Side Effects |
|------|---------------|-------------|---------------|-------------|
| `ralph_read_file` | `WorkspaceRead` | `ReadOnly` | Yes | No |
| `ralph_write_file` | `WorkspaceWriteAny` | `ReadWrite` | No | Yes |
| `ralph_list_directory` | `WorkspaceRead` | `ReadOnly` | Yes | No |
| `ralph_search_files` | `WorkspaceRead` | `ReadOnly` | Yes | No |
| `ralph_git_status` | `GitStatusRead` | `ReadOnly` | Yes | No |
| `ralph_git_log` | `GitStatusRead` | `ReadOnly` | Yes | No |
| `ralph_git_diff` | `GitStatusRead` | `ReadOnly` | Yes | No |
| `ralph_git_show` | `GitStatusRead` | `ReadOnly` | Yes | No |
| `ralph_exec_command` | `ProcessExecBounded` | `ReadWrite` | No | Yes |
| `ralph_submit_artifact` | `ArtifactSubmit` | `ReadWrite` | No | Yes |
| `ralph_report_progress` | `RunReportProgress` | `ReadWrite` | No | Yes |
| `ralph_declare_complete` | `ArtifactSubmit` | `ReadWrite` | No | Yes |
| `ralph_read_env` | `EnvRead` | `ReadOnly` | Yes | No |

## Protocol Versioning

Current: `2024-11-05`

The protocol version is negotiated during the `initialize` handshake. The server
advertises its supported version in the `InitializeResult`.

## Testing

Run tests with:

```bash
cargo test -p mcp-server
```

Run integration tests with:

```bash
cargo test -p mcp-server --test integration
```

## Dependencies

See `Cargo.toml` for the full dependency list. Key dependencies:

- `serde` / `serde_json` — JSON serialization
- `tokio` — Async runtime
- `thiserror` — Error handling

## Crate-Level Documentation

See `src/lib.rs` for crate-level documentation with module map.

See individual module docs:
- `src/protocol/types.rs` — JSON-RPC types and error constructors
- `src/dispatch/access.rs` — Capability, access mode, and denial types
- `src/dispatch/host.rs` — HostSession and WorkspaceAdapter trait definitions
- `src/io/access.rs` — McpServerConfig and EnforcementContext
- `src/io/mod.rs` — McpServer state machine and handle_request contract
- `src/io/transport.rs` — Content-Length framing, Unix socket, stdio transports
