//! # mcp-server - MCP Server Implementation for Ralph Workflow Orchestration
//!
//! This crate provides the MCP (Model Context Protocol) server that enables
//! Ralph to communicate with AI agents like Claude Code, Codex, and OpenCode.
//!
//! ## Architecture Overview
//!
//! ```text
//! Agent Process <--JSON-RPC--> mcp-server <--> Ralph Workflow
//!                                           |
//!                                           +--> HostSession (capabilities)
//!                                           |
//!                                           +--> WorkspaceAdapter (file I/O)
//! ```
//!
//! ## Module Organization
//!
//! | Module | Type | Purpose |
//! |--------|------|---------|
//! | [`protocol`] | Pure | JSON-RPC types, capability declarations, tool definitions |
//! | [`io`] | Boundary | Transport framing, socket handling, stdio I/O, McpServer |
//! | [`dispatch`] | Application | Tool registry, handler dispatch, capability gating |
//!
//! ## Boundary Rules
//!
//! - **`protocol/`** - Pure data types, no side effects
//! - **`io/`** - Boundary module, all actual I/O lives here
//! - **`dispatch/`** - Application logic, handlers are pure functions
//!
//! ## RPC Contract
//!
//! See [`protocol`] module for detailed RPC documentation.
//!
//! ### Supported Methods
//!
//! | Method | Description | Capability Required |
//! |--------|-------------|---------------------|
//! | `initialize` | Handshake, exchange capabilities | None |
//! | `ping` | Liveness check | None |
//! | `tools/list` | List available tools | None |
//! | `tools/call` | Invoke a tool | Tool-specific |
//!
//! ### Error Codes
//!
//! | Code | Meaning |
//! |------|---------|
//! | -32700 | Parse error |
//! | -32600 | Invalid request |
//! | -32601 | Method not found |
//! | -32602 | Invalid params |
//! | -32603 | Internal error |
//! | -32000 | Tool error |
//! | -32001 | Not initialized |
//!
//! ## Capability System
//!
//! Tools are gated by capabilities. The [`McpCapability`] enum defines all available
//! capabilities, and [`ToolRegistry`] checks `session.check_capability()` before
//! invoking any tool handler.
//!
//! ### Capability Mutating Flag
//!
//! Every capability has an implicit mutating flag that determines whether tools
//! requiring that capability are allowed in read-only contexts:
//!
//! | Capability | Mutating |
//! |------------|----------|
//! | `WorkspaceRead` | No |
//! | `WorkspaceWriteEphemeral` | Yes |
//! | `WorkspaceWriteTracked` | Yes |
//! | `WorkspaceWriteAny` | Yes |
//! | `GitStatusRead` | No |
//! | `GitWrite` | Yes |
//! | `EnvRead` | No |
//! | `EnvWrite` | Yes |
//! | `ProcessExecBounded` | Yes |
//! | `ProcessExecUnbounded` | Yes |
//! | `ArtifactSubmit` | No |
//! | `RunReportProgress` | No |
//!
//! See [`dispatch::registry::capability_is_mutating`] for the authoritative list.
//!
//! ## Tool Registry
//!
//! Consumers (like ralph-workflow) create a [`ToolRegistry`] by registering
//! tool handlers with their required capabilities:
//!
//! ```ignore
//! use mcp_server::dispatch::{ToolRegistry, McpCapability};
//!
//! let registry = ToolRegistry::new(vec![
//!     (tool_metadata, handler),
//!     // ...
//! ]);
//! ```
//!
//! Each tool's [`dispatch::ToolMetadata`] contains:
//! - `definition`: [`ToolDefinition`] with name, description, input schema
//! - `required_capability`: [`McpCapability`] required to invoke the tool
//! - `is_mutating`: Override for mutating flag (None = derive from capability)
//!
//! ## Access Enforcement Chain
//!
//! Every `tools/call` request passes through four sequential enforcement checks before
//! reaching any handler. A denial at any level short-circuits the chain and returns
//! an error response; no subsequent checks are evaluated.
//!
//! ```text
//! tools/call request
//!     │
//!     ▼
//! 1. ToolFilter       — Is this tool in the allowlist / not in the blocklist?
//!     │                 → Deny: AccessDeniedCode::ToolNotAllowed (-32000)
//!     ▼
//! 2. AccessMode       — Does the server's access_mode allow this operation?
//!     │                 → Deny: AccessDeniedCode::ReadOnlyMode (-32000)
//!     ▼
//! 3. PathBoundary     — Does the path resolve within root_dir?
//!     │                 → Deny: AccessDeniedCode::OutsideRootDir (-32000)
//!     ▼
//! 4. Capability       — Does the host session grant the required McpCapability?
//!     │                 → Deny: AccessDeniedCode::CapabilityDenied (-32000)
//!     ▼
//! Handler invoked     — Tool result returned to client
//! ```
//!
//! Checks 1–3 are enforced entirely by `mcp-server` without calling the host.
//! Check 4 is the only point where `HostSession::check_capability` is called.
//! The host cannot override checks 1–3.
//!
//! ### Access Mode Constraints
//!
//! | Tool | ReadOnly safe? | Notes |
//! |------|---------------|-------|
//! | `read_file` | Yes | Read-only |
//! | `list_directory` | Yes | Read-only |
//! | `list_directory_recursive` | Yes | Read-only |
//! | `search_files` | Yes | Read-only |
//! | `git_status` | Yes | Read-only |
//! | `git_diff` | Yes | Read-only |
//! | `git_log` | Yes | Read-only |
//! | `git_show` | Yes | Read-only |
//! | `read_env` | Yes | Read-only |
//! | `write_file` | **No** | Mutating — rejected in ReadOnly mode |
//! | `exec` | **No** | Mutating — rejected in ReadOnly mode |
//! | `ralph_submit_artifact` | Yes | Non-mutating workflow signal |
//! | `report_progress` | Yes | Non-mutating progress notification |
//! | `declare_complete` | Yes | Non-mutating completion signal |
//! | `coordinate` | Yes | Non-mutating coordination signal |
//!
//! ## RPC Reference: All 15 Tool Endpoints
//!
//! All tools are invoked via `tools/call`. The general request/response shape is:
//!
//! **Request:**
//! ```json
//! {
//!   "jsonrpc": "2.0",
//!   "method": "tools/call",
//!   "params": { "name": "<tool-name>", "arguments": { ... } },
//!   "id": 1
//! }
//! ```
//!
//! **Success response:**
//! ```json
//! {
//!   "jsonrpc": "2.0",
//!   "result": { "content": [{ "type": "text", "text": "..." }], "isError": false },
//!   "id": 1
//! }
//! ```
//!
//! **Error response (protocol-level):**
//! ```json
//! {
//!   "jsonrpc": "2.0",
//!   "error": { "code": -32000, "message": "Tool error: ..." },
//!   "id": 1
//! }
//! ```
//!
//! ### `read_file`
//! - **Capability:** `WorkspaceRead`
//! - **ReadOnly safe:** Yes
//! - **Arguments:** `{ "path": "<relative-path>" }`
//! - **Response:** File contents as text
//! - **Errors:** `-32000` if path does not exist or is outside root
//! - **Idempotency:** Idempotent — repeated calls with the same path return the same result
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `write_file`
//! - **Capability:** `WorkspaceWriteTracked` (tracked files) or `WorkspaceWriteEphemeral`
//! - **ReadOnly safe:** No — rejected with `ReadOnlyMode` in ReadOnly servers
//! - **Arguments:** `{ "path": "<relative-path>", "content": "<string>" }`
//! - **Response:** Confirmation message with byte count
//! - **Errors:** `-32000` if capability denied or write fails
//! - **Side effects:** Writes to filesystem; overwrites existing file
//! - **Idempotency:** Non-idempotent — repeated calls with the same content produce the same file
//!   but each call is a distinct write operation
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `list_directory`
//! - **Capability:** `WorkspaceRead`
//! - **ReadOnly safe:** Yes
//! - **Arguments:** `{ "path": "<relative-path>" }`
//! - **Response:** Directory listing as text
//! - **Errors:** `-32000` if path does not exist
//! - **Idempotency:** Idempotent — repeated calls return the same listing for unchanged directories
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `list_directory_recursive`
//! - **Capability:** `WorkspaceRead`
//! - **ReadOnly safe:** Yes
//! - **Arguments:** `{ "path": "<relative-path>" }`
//! - **Response:** Recursive directory listing as text
//! - **Errors:** `-32000` if path does not exist
//! - **Idempotency:** Idempotent — repeated calls return the same listing for unchanged trees
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `search_files`
//! - **Capability:** `WorkspaceRead`
//! - **ReadOnly safe:** Yes
//! - **Arguments:** `{ "pattern": "<substring>", "path": "<relative-path>" }`
//! - **Matching:** Filename substring match or `"*"` for all files (not a glob)
//! - **Response:** Matching file paths as text
//! - **Errors:** `-32000` on I/O error
//! - **Idempotency:** Idempotent — repeated calls with the same pattern return the same result
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `git_status`
//! - **Capability:** `GitStatusRead`
//! - **ReadOnly safe:** Yes
//! - **Arguments:** `{}` (no arguments)
//! - **Response:** Git status output as text
//! - **Errors:** `-32000` if not a git repo or git unavailable
//! - **Idempotency:** Idempotent — no filesystem mutation; reflects current working tree state
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `git_diff`
//! - **Capability:** `GitStatusRead`
//! - **ReadOnly safe:** Yes
//! - **Arguments:** `{ "args": ["--staged"] }` — optional array of extra `git diff` arguments
//! - **Response:** Unified diff as text (may be empty if no changes)
//! - **Errors:** `-32000` on git error
//! - **Idempotency:** Idempotent — no filesystem mutation; reflects current diff state
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `git_log`
//! - **Capability:** `GitStatusRead`
//! - **ReadOnly safe:** Yes
//! - **Arguments:** `{ "count": <optional-int> }` — number of recent commits to show (default: 10)
//! - **Response:** Commit log in oneline format (`<sha> <subject>` per line)
//! - **Errors:** `-32000` on git error
//! - **Idempotency:** Idempotent — no filesystem mutation; reflects current commit history
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `git_show`
//! - **Capability:** `GitStatusRead`
//! - **ReadOnly safe:** Yes
//! - **Arguments:** `{ "ref": "<commit-ref>" }`
//! - **Response:** Commit details as text
//! - **Errors:** `-32000` if ref not found
//! - **Idempotency:** Idempotent — commit objects are immutable; repeated calls return the same result
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `exec`
//! - **Capability:** `ProcessExecBounded`
//! - **ReadOnly safe:** No — rejected with `ReadOnlyMode` in ReadOnly servers
//! - **Arguments:** `{ "command": "<cmd>", "args": ["<arg1>", ...], "timeout_ms": <optional-int> }`
//! - **Response:** Command stdout/stderr as text with exit code
//! - **Errors:** `-32000` on execution failure, timeout, or policy blacklist match;
//!   `-32000` with `ReadOnlyMode` code when server is in ReadOnly mode
//! - **Side effects:** Spawns a subprocess; creates/modifies files if command does so
//! - **Idempotency:** Non-idempotent — repeated calls may produce different results or side effects
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `ralph_submit_artifact`
//! - **Capability:** `ArtifactSubmit`
//! - **ReadOnly safe:** Yes — classified as a non-mutating workflow signal; does not modify filesystem
//! - **Arguments:** `{ "artifact_type": "<type>", "content": "<json-string>", "partial": <optional-bool> }`
//! - **Accepted `artifact_type` values:** `"plan"`, `"development_result"`, `"issues"`,
//!   `"fix_result"`, `"commit_message"`, `"review_issues"`
//! - **Response:** JSON with `{ "accepted": true, "artifact_type": "...", "validated_at": "..." }`
//! - **Errors:** `-32000` if `artifact_type` is unknown or content fails schema validation;
//!   `-32000` with `CapabilityDenied` code if session lacks `ArtifactSubmit` capability
//! - **Side effects:** Triggers a workflow state transition in the host (pipeline phase advancement)
//! - **Idempotency:** Non-idempotent — each submission advances pipeline state; submitting the same
//!   artifact twice produces a second state transition
//! - **Naming note:** This tool retains the `ralph_` prefix because it is a Ralph-specific workflow
//!   signal, not a generic filesystem or process operation. All other tools use unprefixed names.
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `report_progress`
//! - **Capability:** `RunReportProgress`
//! - **ReadOnly safe:** Yes — non-mutating; does not modify filesystem or git state
//! - **Arguments:** `{ "status": "<progress-string>", "note": "<optional-string>" }`
//! - **Response:** Progress acknowledgement text with timestamp
//! - **Errors:** `-32000` with `CapabilityDenied` code if session lacks `RunReportProgress` capability
//! - **Side effects:** Emits a progress event to the host workflow; does not modify filesystem
//! - **Idempotency:** Idempotent from the server's perspective — repeated calls with the same status
//!   each emit a separate progress event to the host, but do not change server state
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `declare_complete`
//! - **Capability:** `ArtifactSubmit`
//! - **ReadOnly safe:** Yes — non-mutating; does not modify filesystem or git state
//! - **Arguments:** `{ "summary": "<optional-string>" }`
//! - **Response:** Text confirmation with session ID and timestamp
//! - **Errors:** `-32000` with `CapabilityDenied` code if session lacks `ArtifactSubmit` capability;
//!   `-32000` if the session is not in an active pipeline run
//! - **Side effects:** Signals task completion to the pipeline; advances workflow state in the host
//! - **Idempotency:** Non-idempotent — calling this tool more than once signals completion multiple
//!   times; the host may accept or reject duplicate calls depending on pipeline state
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `read_env`
//! - **Capability:** `EnvRead`
//! - **ReadOnly safe:** Yes
//! - **Arguments:** `{ "name": "<env-var-name>" }`
//! - **Response:** `NAME=VALUE` as text; returns `NAME=[not found]` if variable is not set (not an error)
//! - **Errors:** `-32000` if name is missing from request or capability denied
//! - **Idempotency:** Idempotent — reads environment variable without mutation
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ### `coordinate`
//! - **Capability:** `ArtifactSubmit`
//! - **ReadOnly safe:** Yes — non-mutating from the filesystem perspective; does not write files
//! - **Arguments:** `{ "action": "<action-type>", "work_unit_id": "<optional-string>", "payload": <optional-object> }`
//! - **Supported actions:** `"claim"`, `"release"`, `"status"`, `"ack"`
//! - **Response:** Coordination acknowledgement text with timestamp
//! - **Errors:** `-32000` if `action` is not one of the supported values;
//!   `-32000` with `CapabilityDenied` code if session lacks `ArtifactSubmit` capability;
//!   `-32000` if the coordination state machine rejects the transition (e.g., releasing a
//!   work unit not claimed by this session)
//! - **Side effects:** Updates shared coordination state in the host; does not modify filesystem
//! - **Idempotency:** Action-dependent — `"status"` is idempotent (read-only query);
//!   `"claim"`, `"release"`, `"ack"` are non-idempotent (state transitions)
//! - **Stability:** Stable since protocol version `2024-11-05`
//!
//! ## Lifecycle Methods
//!
//! ### `initialize`
//! - **Capability:** None required
//! - **Request:** `{ "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {...} }`
//! - **Response:** `{ "protocolVersion": "2024-11-05", "capabilities": {...}, "serverInfo": {...} }`
//! - **Notes:** Must be called before any `tools/call`. Calling tools before `initialize`
//!   returns error code `-32001` (NotInitialized).
//!
//! ### `ping`
//! - **Capability:** None required
//! - **Request:** `{}` (no params)
//! - **Response:** `{}` (empty result)
//! - **Notes:** Liveness check; always succeeds after `initialize`.
//!
//! ### `tools/list`
//! - **Capability:** None required
//! - **Request:** `{}` (no params)
//! - **Response:** `{ "tools": [{ "name": "...", "description": "...", "inputSchema": {...} }, ...] }`
//! - **Notes:** Returns all tools visible to this session (filtered by `ToolFilter`).
//!
//! ## Creating a Host Implementation
//!
//! To use mcp-server with Ralph workflow, implement the [`dispatch::HostSession`]
//! and [`dispatch::WorkspaceAdapter`] traits:
//!
//! ```ignore
//! use mcp_server::dispatch::{HostSession, WorkspaceAdapter};
//! use mcp_server::{AccessDecision, McpCapability};
//!
//! struct RalphHostSession {
//!     agent_session: AgentSession,
//! }
//!
//! impl HostSession for RalphHostSession {
//!     fn session_id(&self) -> &str {
//!         &self.agent_session.session_id
//!     }
//!     fn check_capability(&self, cap: McpCapability) -> AccessDecision {
//!         self.agent_session.check_capability(cap)
//!     }
//!     // ...
//! }
//! ```

#![deny(warnings)]
#![deny(clippy::all)]
#![deny(missing_docs)]
#![deny(
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    clippy::dbg_macro,
    clippy::manual_map,
    clippy::manual_filter,
    clippy::manual_find,
    clippy::manual_filter_map,
    clippy::manual_flatten,
    clippy::needless_collect
)]

pub mod dispatch;
pub mod io;
pub mod protocol;

// Re-exports from dispatch (pure types)
pub use dispatch::access::{
    AccessDecision, AccessDeniedCode, AuditSink, McpCapability, NoOpAuditSink, OperationClass,
    PolicyMode,
};
pub use dispatch::{DirEntry, HostSession, ToolError, ToolRegistry, WorkspaceAdapter};

// Boundary types remain under `mcp_server::io::*`.

// Re-exports from protocol (pure types)
pub use protocol::{
    ErrorCode, ErrorResponse, InitializeParams, InitializeResult, JsonRpcError, JsonRpcRequest,
    JsonRpcResponse, NullResult, ServerCapabilities, ServerInfo, ToolContent, ToolDefinition,
    ToolResult, ToolsCapability, ValidationError, MCP_PROTOCOL_VERSION,
};
