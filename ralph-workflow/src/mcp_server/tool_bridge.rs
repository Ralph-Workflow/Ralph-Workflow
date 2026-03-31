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
use crate::mcp_server::capability_mapping::{
    capability_policy, lookup_ralph_capability, policy_from_outcome,
};
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

    fn check_capability(&self, cap: McpCapability) -> AccessDecision {
        // Gather all session outcomes at the boundary
        let ephemeral = self
            .session
            .check_capability(Capability::WorkspaceWriteEphemeral);
        let tracked = self
            .session
            .check_capability(Capability::WorkspaceWriteTracked);
        let mapped_cap = lookup_ralph_capability(cap);
        let mapped_outcome = mapped_cap.map(|c| self.session.check_capability(c));

        // Delegate to pure policy
        capability_policy(cap, ephemeral, tracked, mapped_cap.zip(mapped_outcome))
    }

    fn is_parallel_worker(&self) -> bool {
        self.session.is_parallel_worker()
    }

    fn check_edit_area(&self, path: &str) -> AccessDecision {
        let outcome = self.session.check_edit_area(path);
        policy_from_outcome(outcome)
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

/// ## `ralph_read_file` — RPC Contract
///
/// Reads the complete contents of a file from the workspace.
///
/// ### Required Capability
/// `WorkspaceRead` — Caller must have workspace read access.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `path` | `string` | Absolute or relative path to the file to read |
///
/// ### Returns
/// `ToolResult` with `content` array containing a single `text` block with the file contents.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `path` is missing or malformed
/// - `ToolError::ExecutionError` if the file cannot be read (not found, permission denied, etc.)
///
/// ### Mutating
/// No — this operation only reads data.
fn make_read_file_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_read_file".to_string(),
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

/// ## `ralph_write_file` — RPC Contract
///
/// Writes content to a file in the workspace, creating it if it does not exist
/// or overwriting it if it does.
///
/// ### Required Capability
/// `WorkspaceWriteAny` — Handler determines at runtime whether the target file
/// is tracked (requires `WorkspaceWriteTracked`) or untracked (requires
/// `WorkspaceWriteEphemeral`) and performs the appropriate capability check.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `path` | `string` | Absolute or relative path to the file to write |
/// | `content` | `string` | Content to write to the file |
///
/// ### Returns
/// `ToolResult` with `content` array containing a single `text` block with `"ok"`.
/// ### Errors
/// - `ToolError::InvalidParams` if `path` or `content` is missing
/// - `ToolError::ExecutionError` if the file cannot be written (permission denied, disk full, etc.)
///
/// ### Mutating
/// Yes — creates or overwrites the target file.
fn make_write_file_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_write_file".to_string(),
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

/// ## `ralph_list_directory` — RPC Contract
///
/// Lists the contents of a directory, optionally recursing into subdirectories.
///
/// ### Required Capability
/// `WorkspaceRead` — Caller must have workspace read access.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `path` | `string` | Directory path to list |
/// | `recursive` | `boolean` | Whether to list subdirectories recursively (default: `false`) |
///
/// ### Returns
/// `ToolResult` with `content` array containing directory entries.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `path` is missing
/// - `ToolError::ExecutionError` if the directory cannot be read
///
/// ### Mutating
/// No — this operation only reads directory metadata.
fn make_list_directory_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_list_directory".to_string(),
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

/// ## `ralph_search_files` — RPC Contract
///
/// Searches for files matching a glob pattern within a directory tree.
///
/// ### Required Capability
/// `WorkspaceRead` — Caller must have workspace read access.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `pattern` | `string` | Glob pattern to match (e.g., `**/*.rs`) |
/// | `path` | `string` | Directory path to search beneath |
///
/// ### Returns
/// `ToolResult` with `content` array containing matching file paths.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `pattern` or `path` is missing
/// - `ToolError::ExecutionError` if the search fails
///
/// ### Mutating
/// No — this operation only searches for files.
fn make_search_files_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_search_files".to_string(),
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

/// ## `ralph_git_status` — RPC Contract
///
/// Returns the git status of the workspace, showing modified, staged, and untracked files.
///
/// ### Required Capability
/// `GitStatusRead` — Caller must have git status read access.
///
/// ### Parameters
/// None.
///
/// ### Returns
/// `ToolResult` with `content` array containing a text block with git status output.
///
/// ### Errors
/// - `ToolError::ExecutionError` if git status fails (not a git repo, git not installed, etc.)
///
/// ### Mutating
/// No — this operation only reads git state.
fn make_git_status_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_git_status".to_string(),
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

/// ## `ralph_git_diff` — RPC Contract
///
/// Returns the git diff of changes (unstaged, staged, or between commits).
///
/// ### Required Capability
/// `GitStatusRead` — Caller must have git status read access.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `args` | `string[]` | Optional additional git diff arguments |
///
/// ### Returns
/// `ToolResult` with `content` array containing a text block with diff output.
///
/// ### Errors
/// - `ToolError::ExecutionError` if git diff fails
///
/// ### Mutating
/// No — this operation only reads git state.
fn make_git_diff_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_git_diff".to_string(),
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

/// ## `ralph_git_log` — RPC Contract
///
/// Returns the git commit log, showing recent commits with their hashes, authors,
/// dates, and messages.
///
/// ### Required Capability
/// `GitStatusRead` — Caller must have git status read access.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `count` | `number` | Number of commits to show (default: 10) |
///
/// ### Returns
/// `ToolResult` with `content` array containing a text block with commit log output.
///
/// ### Errors
/// - `ToolError::ExecutionError` if git log fails
///
/// ### Mutating
/// No — this operation only reads git history.
fn make_git_log_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_git_log".to_string(),
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

/// ## `ralph_git_show` — RPC Contract
///
/// Shows a git object (commit, tag, tree, blob) by reference.
///
/// ### Required Capability
/// `GitStatusRead` — Caller must have git status read access.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `ref` | `string` | Git object reference (commit hash, tag name, etc.) |
///
/// ### Returns
/// `ToolResult` with `content` array containing a text block with the object contents.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `ref` is missing
/// - `ToolError::ExecutionError` if the git object cannot be shown
///
/// ### Mutating
/// No — this operation only reads git data.
fn make_git_show_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_git_show".to_string(),
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

/// ## `ralph_exec_command` — RPC Contract
///
/// Executes a shell command with resource limits (bounded execution).
///
/// ### Required Capability
/// `ProcessExecBounded` — Caller must have bounded process execution capability.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `command` | `string` | Command to execute |
/// | `args` | `string[]` | Optional command arguments |
/// | `timeout_ms` | `number` | Timeout in milliseconds (default: 30000) |
///
/// ### Returns
/// `ToolResult` with `content` array containing a text block with stdout/stderr output.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `command` is missing
/// - `ToolError::ExecutionError` if the command fails, times out, or exceeds resource limits
///
/// ### Mutating
/// Depends on the command being executed — the tool itself is considered mutating
/// because arbitrary command execution can have side effects.
fn make_exec_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_exec_command".to_string(),
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
/// ### Required Capability
/// `ArtifactSubmit` — Caller must have artifact submission capability.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `artifact_type` | `string` | Type of artifact (plan, development_result, issues, fix_result, commit_message) |
/// | `content` | `string` | Artifact content as a JSON string |
/// | `partial` | `boolean` | Optional. If true, accepts artifact even with validation errors (default: false) |
///
/// ### Returns
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
/// ### Mutating
/// Yes — submits an artifact to the workflow for processing.
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
                    "artifact": { "type": "object", "description": "Artifact content" }
                },
                "required": ["artifact_type", "artifact"]
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

/// ## `ralph_report_progress` — RPC Contract
///
/// Reports progress status to the running workflow.
///
/// ### Required Capability
/// `RunReportProgress` — Caller must have progress reporting capability.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `status` | `string` | Status message describing current progress |
/// | `note` | `string` | Optional additional notes or context |
///
/// ### Returns
/// `ToolResult` with `content` array containing a text block confirming report.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `status` is missing
/// - `ToolError::ExecutionError` if reporting fails
///
/// ### Mutating
/// No — this operation only reports progress to the workflow.
fn make_report_progress_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_report_progress".to_string(),
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

/// ## `ralph_declare_complete` — RPC Contract
///
/// Declares that the agent has completed its task and provides a summary.
///
/// ### Required Capability
/// `ArtifactSubmit` — Caller must have artifact submission capability.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `summary` | `string` | Summary of what was accomplished |
///
/// ### Returns
/// `ToolResult` with `content` array containing a text block confirming completion.
///
/// ### Errors
/// - `ToolError::ExecutionError` if declaration fails
///
/// ### Mutating
/// Yes — signals the workflow to transition to a completed state.
fn make_declare_complete_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_declare_complete".to_string(),
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

/// ## `ralph_read_env` — RPC Contract
///
/// Reads an environment variable from the agent's environment.
///
/// ### Required Capability
/// `EnvRead` — Caller must have environment read access.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `name` | `string` | Environment variable name |
///
/// ### Returns
/// `ToolResult` with `content` array containing a text block with `name=value`.
/// If the variable is not set, returns `name=[not found]` (not an error).
///
/// ### Errors
/// - `ToolError::InvalidParams` if `name` is missing
/// - `ToolError::CapabilityDenied` if session lacks `EnvRead` capability
///
/// ### Mutating
/// No — this operation only reads environment data.
fn make_read_env_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_read_env".to_string(),
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

/// ## `ralph_list_directory_recursive` — RPC Contract
///
/// Lists the contents of a directory and all subdirectories recursively.
///
/// ### Required Capability
/// `WorkspaceRead` — Caller must have workspace read access.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `path` | `string` | Directory path to list recursively |
///
/// ### Returns
/// `ToolResult` with `content` array containing all directory entries recursively.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `path` is missing
/// - `ToolError::ExecutionError` if the directory cannot be read
///
/// ### Mutating
/// No — this operation only reads directory metadata.
fn make_list_directory_recursive_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_list_directory_recursive".to_string(),
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

/// ## `ralph_coordinate` — RPC Contract
///
/// Coordinates parallel worker activities such as claiming work units,
/// reporting status, or acknowledging task distribution.
///
/// ### Required Capability
/// `ArtifactSubmit` — Caller must have artifact submission capability.
///
/// ### Parameters
/// | Name | Type | Description |
/// |------|------|-------------|
/// | `action` | `string` | Coordination action (e.g., "claim", "release", "status", "ack") |
/// | `work_unit_id` | `string` | Optional identifier for the work unit being coordinated |
/// | `payload` | `object` | Optional JSON payload for coordination data |
///
/// ### Returns
/// `ToolResult` with `content` array containing a text block confirming coordination.
///
/// ### Errors
/// - `ToolError::InvalidParams` if `action` is missing
/// - `ToolError::CapabilityDenied` if session lacks `ArtifactSubmit` capability
///
/// ### Mutating
/// No — this operation only coordinates workflow state.
fn make_coordinate_tool(
    session: Arc<AgentSession>,
    workspace: Arc<dyn Workspace>,
) -> (ToolMetadata, ToolHandler) {
    let meta = ToolMetadata {
        definition: ToolDefinition {
            name: "ralph_coordinate".to_string(),
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
