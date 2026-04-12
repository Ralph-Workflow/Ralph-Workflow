//! Bridge between ralph-workflow tool implementations and mcp-server's ToolRegistry.
//!
//! This module provides adapters that wrap Ralph's `AgentSession` and `Workspace`
//! to implement `mcp_server::HostSession` and `mcp_server::WorkspaceAdapter`.
//! It also provides functions to build a `ToolRegistry` populated with handlers
//! that call the real Ralph tool implementations.
//!
//! # Architecture
//!
//! ```text
//! ralph-workflow tool_*.rs -> tool_bridge.rs -> mcp_server::ToolRegistry -> McpServer
//!                                    |
//!                                    +-- RalphHostSessionAdapter (implements HostSession)
//!                                    +-- RalphWorkspaceAdapter (implements WorkspaceAdapter)
//! ```
//!
//! The `build_ralph_tool_registry` function creates a `ToolRegistry` with all
//! Ralph MCP tools registered. The handlers capture `Arc<AgentSession>` and
//! `Arc<dyn Workspace>` at creation time and delegate to the real tool implementations.

use crate::agents::session::{AgentSession, Capability};
use crate::mcp_server::capability_mapping::{check_mcp_capability_policy, lookup_ralph_capability};
use crate::mcp_server::tool_artifact;
use crate::mcp_server::tool_coordination;
use crate::mcp_server::tool_exec;
use crate::mcp_server::tool_git_read;
use crate::mcp_server::tool_workspace;
use crate::workspace::Workspace;
use mcp_server::dispatch::access::{AccessDecision, McpCapability};
use mcp_server::dispatch::host::{DirEntry, HostSession, WorkspaceAdapter};
use mcp_server::dispatch::{ToolHandler, ToolMetadata, ToolRegistry};
use mcp_server::protocol::ToolDefinition;
use std::path::Path;
use std::sync::Arc;

// ---------------------------------------------------------------------------
// HostSession adapter
// ---------------------------------------------------------------------------

/// Adapter that wraps Ralph's `AgentSession` to implement `mcp_server::HostSession`.
pub(crate) struct RalphHostSessionAdapter {
    session: Arc<AgentSession>,
}

impl RalphHostSessionAdapter {
    /// Create a new adapter wrapping the given session.
    pub(crate) fn new(session: Arc<AgentSession>) -> Self {
        Self { session }
    }
}

impl HostSession for RalphHostSessionAdapter {
    fn session_id(&self) -> &str {
        self.session.session_id.as_str()
    }

    fn run_id(&self) -> &str {
        self.session.run_id.as_str()
    }

    fn check_capability(&self, cap: McpCapability) -> AccessDecision {
        // Thin wiring: gather inputs and delegate to pure policy
        let ephemeral = self
            .session
            .check_capability(Capability::WorkspaceWriteEphemeral);
        let tracked = self
            .session
            .check_capability(Capability::WorkspaceWriteTracked);
        let mapped_outcome = lookup_ralph_capability(cap).map(|c| {
            let outcome = self.session.check_capability(c);
            (c, outcome)
        });
        check_mcp_capability_policy(cap, ephemeral, tracked, mapped_outcome)
    }
}

// ---------------------------------------------------------------------------
// WorkspaceAdapter adapter
// ---------------------------------------------------------------------------

/// Adapter that wraps Ralph's `Workspace` to implement `mcp_server::WorkspaceAdapter`.
pub(crate) struct RalphWorkspaceAdapter {
    workspace: Arc<dyn Workspace>,
}

impl RalphWorkspaceAdapter {
    /// Create a new adapter wrapping the given workspace.
    pub(crate) fn new(workspace: Arc<dyn Workspace>) -> Self {
        Self { workspace }
    }
}

impl WorkspaceAdapter for RalphWorkspaceAdapter {
    fn read(&self, path: &Path) -> Result<String, String> {
        self.workspace.read(path).map_err(|e| e.to_string())
    }

    fn write(&self, path: &Path, content: &str) -> Result<(), String> {
        self.workspace
            .write(path, content)
            .map_err(|e| e.to_string())
    }

    fn exists(&self, path: &Path) -> bool {
        self.workspace.exists(path)
    }

    fn read_dir(&self, path: &Path) -> Result<Vec<DirEntry>, String> {
        let entries = self.workspace.read_dir(path).map_err(|e| e.to_string())?;
        Ok(entries
            .into_iter()
            .map(|e| DirEntry {
                path: e.path().display().to_string(),
                is_dir: e.is_dir(),
            })
            .collect())
    }
}

// ---------------------------------------------------------------------------
// Tool handler factory functions
// ---------------------------------------------------------------------------

/// ## `read_file` — RPC Contract
///
/// Reads the complete contents of a file from the workspace.
///
/// ### Capabilities
/// `WorkspaceRead` — Caller must have workspace read access.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `path` | `string` | Absolute or relative path to the file to read |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a single `text` block with the file contents.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `path` is missing or malformed
/// - `ToolError::ExecutionError` if the file cannot be read (not found, permission denied, etc.)
///
/// ### Side Effects/Idempotency
/// No — this operation only reads data.
///
/// ### Access Mode
/// ReadOnly-safe: YES — this operation only reads data and does not modify any state.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_read_file_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "read_file".to_string(),
            description: "Read a file from the workspace".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Path to the file to read" }
                },
                "required": ["path"]
            }),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_workspace::handle_read_file(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `write_file` — RPC Contract
///
/// Writes content to a file in the workspace, creating it if it does not exist
/// or overwriting it if it does.
///
/// ### Capabilities
/// `WorkspaceWriteAny` — Handler determines at runtime whether the target file
/// is tracked (requires `WorkspaceWriteTracked`) or untracked (requires
/// `WorkspaceWriteEphemeral`) and performs the appropriate capability check.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `path` | `string` | Absolute or relative path to the file to write |
/// | `content` | `string` | Content to write to the file |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a single `text` block with `"ok"`.
/// ### Errors
/// - `ToolError::InvalidParams` if `path` or `content` is missing
/// - `ToolError::ExecutionError` if the file cannot be written (permission denied, disk full, etc.)
///
/// ### Side Effects/Idempotency
/// Yes — creates or overwrites the target file.
///
/// ### Access Mode
/// ReadOnly-safe: NO — this operation creates or overwrites files, which is a mutation.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_write_file_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "write_file".to_string(),
            description: "Write content to a file in the workspace".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Path to the file to write" },
                    "content": { "type": "string", "description": "Content to write" }
                },
                "required": ["path", "content"]
            }),
        },
        // WorkspaceWriteAny allows both tracked and ephemeral file writes.
        // The handler itself determines whether the file is tracked or ephemeral
        // and performs the appropriate capability check.
        required_capability: McpCapability::WorkspaceWriteAny,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_workspace::handle_write_file(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `list_directory` — RPC Contract
///
/// Lists the contents of a directory, optionally recursing into subdirectories.
///
/// ### Capabilities
/// `WorkspaceRead` — Caller must have workspace read access.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `path` | `string` | Directory path to list |
/// | `recursive` | `boolean` | Whether to list subdirectories recursively (default: `false`) |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing directory entries.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `path` is missing
/// - `ToolError::ExecutionError` if the directory cannot be read
///
/// ### Side Effects/Idempotency
/// No — this operation only reads directory metadata.
///
/// ### Access Mode
/// ReadOnly-safe: YES — this operation only reads directory metadata.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_list_directory_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "list_directory".to_string(),
            description: "List directory contents".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Directory path to list" },
                    "recursive": { "type": "boolean", "description": "Whether to list recursively", "default": false }
                },
                "required": ["path"]
            }),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_workspace::handle_list_directory(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `search_files` — RPC Contract
///
/// Searches for files matching a glob pattern within a directory tree.
///
/// ### Capabilities
/// `WorkspaceRead` — Caller must have workspace read access.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `pattern` | `string` | Glob pattern to match (e.g., `**/*.rs`) |
/// | `path` | `string` | Directory path to search beneath |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing matching file paths.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `pattern` or `path` is missing
/// - `ToolError::ExecutionError` if the search fails
///
/// ### Side Effects/Idempotency
/// No — this operation only searches for files.
///
/// ### Access Mode
/// ReadOnly-safe: YES — this operation only searches for files without modifying state.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_search_files_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "search_files".to_string(),
            description: "Search for files matching a pattern".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "pattern": { "type": "string", "description": "Search pattern" },
                    "path": { "type": "string", "description": "Directory path to search in" }
                },
                "required": ["pattern", "path"]
            }),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_workspace::handle_search_files(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `git_status` — RPC Contract
///
/// Returns the git status of the workspace, showing modified, staged, and untracked files.
///
/// ### Capabilities
/// `GitStatusRead` — Caller must have git status read access.
///
/// ### Request Shape
/// None.
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a text block with git status output.
///
/// ### Errors
/// - `ToolError::ExecutionError` if git status fails (not a git repo, git not installed, etc.)
///
/// ### Side Effects/Idempotency
/// No — this operation only reads git state.
///
/// ### Access Mode
/// ReadOnly-safe: YES — this operation only reads git state without modifications.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_git_status_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "git_status".to_string(),
            description: "Get git status of the workspace".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {}
            }),
        },
        required_capability: McpCapability::GitStatusRead,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_git_read::handle_git_status(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `git_diff` — RPC Contract
///
/// Returns the git diff of changes (unstaged, staged, or between commits).
///
/// ### Capabilities
/// `GitStatusRead` — Caller must have git status read access.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `args` | `string[]` | Optional additional git diff arguments |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a text block with diff output.
///
/// ### Errors
/// - `ToolError::ExecutionError` if git diff fails
///
/// ### Side Effects/Idempotency
/// No — this operation only reads git state.
///
/// ### Access Mode
/// ReadOnly-safe: YES — this operation only reads git state without modifications.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_git_diff_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "git_diff".to_string(),
            description: "Get git diff of changes".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "args": { "type": "array", "items": { "type": "string" }, "description": "Additional git diff arguments" }
                }
            }),
        },
        required_capability: McpCapability::GitStatusRead,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_git_read::handle_git_diff(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `git_log` — RPC Contract
///
/// Returns the git commit log, showing recent commits with their hashes, authors,
/// dates, and messages.
///
/// ### Capabilities
/// `GitStatusRead` — Caller must have git status read access.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `count` | `number` | Number of commits to show (default: 10) |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a text block with commit log output.
///
/// ### Errors
/// - `ToolError::ExecutionError` if git log fails
///
/// ### Side Effects/Idempotency
/// No — this operation only reads git history.
///
/// ### Access Mode
/// ReadOnly-safe: YES — this operation only reads git history without modifications.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_git_log_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "git_log".to_string(),
            description: "Get git commit log".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "count": { "type": "number", "description": "Number of commits to show", "default": 10 }
                }
            }),
        },
        required_capability: McpCapability::GitStatusRead,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_git_read::handle_git_log(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `git_show` — RPC Contract
///
/// Shows a git object (commit, tag, tree, blob) by reference.
///
/// ### Capabilities
/// `GitStatusRead` — Caller must have git status read access.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `ref` | `string` | Git object reference (commit hash, tag name, etc.) |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a text block with the object contents.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `ref` is missing
/// - `ToolError::ExecutionError` if the git object cannot be shown
///
/// ### Side Effects/Idempotency
/// No — this operation only reads git data.
///
/// ### Access Mode
/// ReadOnly-safe: YES — this operation only reads git data without modifications.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_git_show_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "git_show".to_string(),
            description: "Show a git object (commit, tag, etc.)".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "ref": { "type": "string", "description": "Git object reference" }
                },
                "required": ["ref"]
            }),
        },
        required_capability: McpCapability::GitStatusRead,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_git_read::handle_git_show(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `exec` — RPC Contract
///
/// Executes a shell command with resource limits (bounded execution).
///
/// ### Capabilities
/// `ProcessExecBounded` — Caller must have bounded process execution capability.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `command` | `string` | Command to execute |
/// | `args` | `string[]` | Optional command arguments |
/// | `timeout_ms` | `number` | Timeout in milliseconds (default: 30000) |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a text block with stdout/stderr output.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `command` is missing
/// - `ToolError::ExecutionError` if the command fails, times out, or exceeds resource limits
///
/// ### Side Effects/Idempotency
/// Depends on the command being executed — the tool itself is considered mutating
/// because arbitrary command execution can have side effects.
///
/// ### Access Mode
/// ReadOnly-safe: NO — command execution can have arbitrary side effects depending on the command.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_exec_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "exec".to_string(),
            description: "Execute a shell command".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "command": { "type": "string", "description": "Command to execute" },
                    "args": { "type": "array", "items": { "type": "string" }, "description": "Command arguments" },
                    "timeout_ms": { "type": "number", "description": "Timeout in milliseconds", "default": 30000 }
                },
                "required": ["command"]
            }),
        },
        required_capability: McpCapability::ProcessExecBounded,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_exec::handle_exec_command(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `ralph_submit_artifact` — RPC Contract
///
/// Submits a structured artifact to the workflow for processing.
///
/// ### Capabilities
/// `ArtifactSubmit` — Caller must have artifact submission capability.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `artifact_type` | `string` | Type of artifact (plan, development_result, issues, fix_result, commit_message) |
/// | `content` | `string` | Artifact content as a JSON string |
/// | `partial` | `boolean` | Optional. If true, accepts artifact even with validation errors (default: false) |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a text block with a JSON object:
/// - `accepted`: `true` if the artifact was accepted
/// - `partial`: `true` if accepted in partial mode (has validation errors)
/// - `artifact_type`: The type of artifact accepted
/// - `validated_at`: ISO 8601 timestamp
///
/// ### Errors
/// - `ToolError::InvalidParams` if `artifact_type` or `content` is missing, or if artifact_type is unknown
/// - `ToolError::ExecutionError` if submission fails (e.g., JSON parsing error)
/// - `ToolError::CapabilityDenied` if session lacks `ArtifactSubmit` capability
///
/// ### Side Effects/Idempotency
/// Yes — submits an artifact to the workflow for processing (triggers a pipeline state transition).
///
/// ### Access Mode
/// ReadOnly-safe: YES — the `ArtifactSubmit` capability is classified as non-mutating by
/// `capability_is_mutating`. Artifact submission is a workflow signal to the host; it does
/// not write to the filesystem, modify git state, or execute processes. The ReadOnly access
/// mode is intended to block filesystem/process mutations, not workflow coordination signals.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_submit_artifact_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_submit_artifact".to_string(),
            description: "Submit a structured artifact".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "artifact_type": { "type": "string", "description": "Type of artifact (plan, development_result, issues, fix_result, commit_message)" },
                    "content": { "type": "string", "description": "JSON-serialized artifact payload" },
                    "partial": { "type": "boolean", "description": "If true, accepts artifact even with validation errors (default: false)" }
                },
                "required": ["artifact_type", "content"]
            }),
        },
        required_capability: McpCapability::ArtifactSubmit,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_artifact::handle_submit_artifact(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `report_progress` — RPC Contract
///
/// Reports progress status to the running workflow.
///
/// ### Capabilities
/// `RunReportProgress` — Caller must have progress reporting capability.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `status` | `string` | Status message describing current progress |
/// | `note` | `string` | Optional additional notes or context |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a text block confirming report.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `status` is missing
/// - `ToolError::ExecutionError` if reporting fails
///
/// ### Side Effects/Idempotency
/// No — this operation only reports progress to the workflow.
///
/// ### Access Mode
/// ReadOnly-safe: YES — progress reporting does not modify workflow state.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_report_progress_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "report_progress".to_string(),
            description: "Report progress status to the agent".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "status": { "type": "string", "description": "Status message describing current progress" },
                    "note": { "type": "string", "description": "Optional additional notes or context" }
                },
                "required": ["status"]
            }),
        },
        required_capability: McpCapability::RunReportProgress,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_coordination::handle_report_progress(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `declare_complete` — RPC Contract
///
/// Declares that the agent has completed its task and provides a summary.
///
/// ### Capabilities
/// `ArtifactSubmit` — Caller must have artifact submission capability.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `summary` | `string` | Summary of what was accomplished |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a text block confirming completion.
///
/// ### Errors
/// - `ToolError::ExecutionError` if declaration fails
///
/// ### Side Effects/Idempotency
/// Yes — signals the workflow to transition to a completed state.
///
/// ### Access Mode
/// ReadOnly-safe: NO — signals workflow state transition.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_declare_complete_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "declare_complete".to_string(),
            description: "Declare that the agent has completed its task".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "summary": { "type": "string", "description": "Summary of what was accomplished" }
                }
            }),
        },
        required_capability: McpCapability::ArtifactSubmit,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_coordination::handle_declare_complete(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `read_env` — RPC Contract
///
/// Reads an environment variable from the agent's environment.
///
/// ### Capabilities
/// `EnvRead` — Caller must have environment read access.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `name` | `string` | Environment variable name |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a text block with `name=value`.
/// If the variable is not set, returns `name=[not found]` (not an error).
///
/// ### Errors
/// - `ToolError::InvalidParams` if `name` is missing
/// - `ToolError::CapabilityDenied` if session lacks `EnvRead` capability
///
/// ### Side Effects/Idempotency
/// No — this operation only reads environment data.
///
/// ### Access Mode
/// ReadOnly-safe: YES — this operation only reads environment data without modifications.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_read_env_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "read_env".to_string(),
            description: "Read an environment variable".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "name": { "type": "string", "description": "Environment variable name" }
                },
                "required": ["name"]
            }),
        },
        required_capability: McpCapability::EnvRead,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_coordination::handle_read_env(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `list_directory_recursive` — RPC Contract
///
/// Lists the contents of a directory and all subdirectories recursively.
///
/// ### Capabilities
/// `WorkspaceRead` — Caller must have workspace read access.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `path` | `string` | Directory path to list recursively |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing all directory entries recursively.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `path` is missing
/// - `ToolError::ExecutionError` if the directory cannot be read
///
/// ### Side Effects/Idempotency
/// No — this operation only reads directory metadata.
///
/// ### Access Mode
/// ReadOnly-safe: YES — this operation only reads directory metadata without modifications.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_list_directory_recursive_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "list_directory_recursive".to_string(),
            description: "List directory contents recursively".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Directory path to list recursively" }
                },
                "required": ["path"]
            }),
        },
        required_capability: McpCapability::WorkspaceRead,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_workspace::handle_list_directory_recursive(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

/// ## `coordinate` — RPC Contract
///
/// Coordinates parallel worker activities such as claiming work units,
/// reporting status, or acknowledging task distribution.
///
/// ### Capabilities
/// `ArtifactSubmit` — Caller must have artifact submission capability.
///
/// ### Request Shape
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `action` | `string` | Coordination action (e.g., "claim", "release", "status", "ack") |
/// | `work_unit_id` | `string` | Optional identifier for the work unit being coordinated |
/// | `payload` | `object` | Optional JSON payload for coordination data |
///
/// ### Response Shape
/// `ToolResult` with `content` array containing a text block confirming coordination.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `action` is missing
/// - `ToolError::CapabilityDenied` if session lacks `ArtifactSubmit` capability
///
/// ### Side Effects/Idempotency
/// No — this operation only coordinates workflow state.
///
/// ### Access Mode
/// ReadOnly-safe: YES — coordination actions do not directly modify workspace files.
///
/// ### Versioning
/// Stable since protocol version `2024-11-05`. No breaking changes planned.
fn make_coordinate_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "coordinate".to_string(),
            description: "Coordinate parallel worker activities".to_string(),
            input_schema: serde_json::json!({
                "type": "object",
                "properties": {
                    "action": { "type": "string", "description": "Coordination action (claim, release, status, ack)" },
                    "work_unit_id": { "type": "string", "description": "Optional work unit identifier" },
                    "payload": { "type": "object", "description": "Optional coordination payload" }
                },
                "required": ["action"]
            }),
        },
        required_capability: McpCapability::ArtifactSubmit,
        is_mutating: None, // derived from required_capability at registration
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_coordination::handle_coordinate(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

// ---------------------------------------------------------------------------
// Registry builder
// ---------------------------------------------------------------------------

/// Build a `mcp_server::ToolRegistry` populated with all Ralph MCP tools.
///
/// The registry captures `session` and `workspace` by `Arc` so each handler closure
/// can call the actual Ralph tool implementation without needing to downcast trait objects.
/// Capability checks are already performed by `mcp_server` before the handler is called.
pub(crate) fn build_ralph_tool_registry(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> ToolRegistry {
    let tools = vec![
        make_read_file_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_write_file_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_list_directory_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_search_files_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_git_status_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_git_diff_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_git_log_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_git_show_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_exec_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_submit_artifact_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_report_progress_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_declare_complete_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_read_env_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_list_directory_recursive_tool(Arc::clone(&session), Arc::clone(&workspace)),
        make_coordinate_tool(Arc::clone(&session), Arc::clone(&workspace)),
    ];
    ToolRegistry::new(tools)
}

// Re-export RalphAuditSinkAdapter from the audit_adapter submodule
pub(crate) use super::audit_adapter::RalphAuditSinkAdapter;
