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

use crate::agents::session::{
    AgentSession, AuditRecord as RalphAuditRecord, Capability, PolicyOutcome,
};
use crate::mcp_server::tool_artifact;
use crate::mcp_server::tool_coordination;
use crate::mcp_server::tool_exec;
use crate::mcp_server::tool_git_read;
use crate::mcp_server::tool_workspace;
use crate::workspace::Workspace;
use mcp_server::dispatch::access::{AccessDecision, AccessDeniedCode, AuditSink, McpCapability};
use mcp_server::dispatch::audit::AuditRecord as McpAuditRecord;
use mcp_server::dispatch::host::{DirEntry, HostSession, WorkspaceAdapter};
use mcp_server::dispatch::{ToolHandler, ToolMetadata, ToolRegistry};
use mcp_server::protocol::ToolDefinition;
use std::path::Path;
use std::sync::{Arc, Mutex};

// ---------------------------------------------------------------------------
// Capability mapping
// ---------------------------------------------------------------------------

/// Policy: map McpCapability to Ralph Capability.
fn map_mcp_capability(cap: McpCapability) -> Option<Capability> {
    match cap {
        McpCapability::WorkspaceRead => Some(Capability::WorkspaceRead),
        McpCapability::WorkspaceWriteEphemeral => Some(Capability::WorkspaceWriteEphemeral),
        McpCapability::WorkspaceWriteTracked => Some(Capability::WorkspaceWriteTracked),
        McpCapability::GitStatusRead => Some(Capability::GitStatusRead),
        McpCapability::GitWrite => Some(Capability::GitWrite),
        McpCapability::EnvRead => Some(Capability::EnvRead),
        McpCapability::ProcessExecBounded => Some(Capability::ProcessExecBounded),
        McpCapability::ArtifactSubmit => Some(Capability::ArtifactSubmit),
        McpCapability::RunReportProgress => Some(Capability::RunReportProgress),
        _ => None, // GitDiffRead, etc. not in McpCapability
    }
}

/// Policy: convert Ralph PolicyOutcome to AccessDecision.
fn policy_from_outcome(outcome: PolicyOutcome) -> AccessDecision {
    match outcome {
        PolicyOutcome::Approved => AccessDecision::Allow,
        PolicyOutcome::ApprovedWithRestriction { .. } => AccessDecision::Allow,
        PolicyOutcome::Denied { reason } => AccessDecision::Deny {
            reason,
            code: AccessDeniedCode::CapabilityDenied,
        },
    }
}

/// Policy: decide access for WorkspaceWriteAny capability.
fn decide_workspace_write_any(
    ephemeral_outcome: PolicyOutcome,
    tracked_outcome: PolicyOutcome,
) -> Option<AccessDecision> {
    let allowed = matches!(
        (ephemeral_outcome, tracked_outcome),
        (PolicyOutcome::Approved, _)
            | (_, PolicyOutcome::Approved)
            | (PolicyOutcome::ApprovedWithRestriction { .. }, _)
            | (_, PolicyOutcome::ApprovedWithRestriction { .. })
    );
    if allowed {
        Some(AccessDecision::Allow)
    } else {
        Some(AccessDecision::Deny {
            reason: "Workspace write capability not granted".to_string(),
            code: AccessDeniedCode::CapabilityDenied,
        })
    }
}

/// Unified policy: decide access for any capability given all session outcomes.
fn capability_policy(
    cap: McpCapability,
    ephemeral: PolicyOutcome,
    tracked: PolicyOutcome,
    mapped: Option<(Capability, PolicyOutcome)>,
) -> AccessDecision {
    if cap == McpCapability::WorkspaceWriteAny {
        return decide_workspace_write_any(ephemeral, tracked)
            .expect("WorkspaceWriteAny always returns Some");
    }
    match mapped {
        Some((_, outcome)) => policy_from_outcome(outcome),
        None => AccessDecision::Deny {
            reason: format!("Unknown capability: {:?}", cap),
            code: AccessDeniedCode::CapabilityDenied,
        },
    }
}

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
        let mapped_cap = map_mcp_capability(cap);
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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_workspace::handle_read_file(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_workspace::handle_write_file(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_workspace::handle_list_directory(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_workspace::handle_search_files(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_git_read::handle_git_status(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_git_read::handle_git_diff(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_git_read::handle_git_log(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_git_read::handle_git_show(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_exec::handle_exec_command(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_artifact::handle_submit_artifact(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_coordination::handle_report_progress(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_coordination::handle_declare_complete(&session, workspace.as_ref(), params)
    });
    (meta, handler)
}

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
    };
    let handler: ToolHandler = Arc::new(move |_host, _ws, params| {
        tool_coordination::handle_read_env(&session, workspace.as_ref(), params)
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
    ];
    ToolRegistry::new(tools)
}

// ---------------------------------------------------------------------------
// Audit sink adapter
// ---------------------------------------------------------------------------

/// Policy: map McpCapability back to Ralph Capability for audit record emission.
///
/// This is the inverse of `map_mcp_capability` used in `RalphHostSessionAdapter`.
fn map_capability_to_ralph(cap: McpCapability) -> Capability {
    match cap {
        McpCapability::WorkspaceRead => Capability::WorkspaceRead,
        McpCapability::WorkspaceWriteEphemeral => Capability::WorkspaceWriteEphemeral,
        McpCapability::WorkspaceWriteTracked => Capability::WorkspaceWriteTracked,
        McpCapability::GitStatusRead => Capability::GitStatusRead,
        McpCapability::GitWrite => Capability::GitWrite,
        McpCapability::EnvRead => Capability::EnvRead,
        McpCapability::ProcessExecBounded => Capability::ProcessExecBounded,
        McpCapability::ArtifactSubmit => Capability::ArtifactSubmit,
        McpCapability::RunReportProgress => Capability::RunReportProgress,
        _ => Capability::WorkspaceRead, // Sensible default for unmapped capabilities
    }
}

/// Policy: convert McpAuditRecord decision to Ralph PolicyOutcome.
fn outcome_from_decision(decision: &AccessDecision) -> PolicyOutcome {
    match decision {
        AccessDecision::Allow => PolicyOutcome::Approved,
        AccessDecision::Deny { .. } => PolicyOutcome::Denied {
            reason: decision.to_error_string(),
        },
    }
}

/// Adapter that implements `mcp_server::dispatch::access::AuditSink` to bridge
/// MCP audit records into Ralph's `AuditTrail`.
///
/// This adapter translates `mcp_server::dispatch::audit::AuditRecord` (with
/// `timestamp_nanos`, `session_id`, `tool_name`, `decision`, `path`, `capability`)
/// into Ralph's `AuditRecord` format (with `session_id: AgentSessionId`,
/// `timestamp: u64` in seconds, `capability: Capability`, `outcome: PolicyOutcome`,
/// `description: String`).
///
/// Stores records in an internal buffer that can be drained via `drain_records()`.
pub(crate) struct RalphAuditSinkAdapter {
    records: Mutex<Vec<RalphAuditRecord>>,
}

impl RalphAuditSinkAdapter {
    /// Create a new empty audit sink adapter.
    pub(crate) fn new() -> Self {
        Self {
            records: Mutex::new(Vec::new()),
        }
    }

    /// Drain all accumulated audit records, returning them and clearing the buffer.
    pub(crate) fn drain_records(&self) -> Vec<RalphAuditRecord> {
        let mut records = self.records.lock().unwrap();
        std::mem::take(&mut records)
    }
}

impl Default for RalphAuditSinkAdapter {
    fn default() -> Self {
        Self::new()
    }
}

impl AuditSink for RalphAuditSinkAdapter {
    fn emit(&self, record: McpAuditRecord) {
        let capability = record
            .capability
            .map(map_capability_to_ralph)
            .unwrap_or(Capability::WorkspaceRead);

        // Convert nanoseconds timestamp to seconds
        let timestamp_secs = record.timestamp_nanos / 1_000_000_000;

        // Build description from tool name and decision
        let description = if record.decision.is_allowed() {
            format!("MCP tool '{}' executed successfully", record.tool_name)
        } else {
            format!(
                "MCP tool '{}' access denied: {}",
                record.tool_name,
                record.decision.to_error_string()
            )
        };

        let ralph_record = RalphAuditRecord::new(
            crate::agents::session::AgentSessionId::from_string(record.session_id.clone()),
            timestamp_secs,
            capability,
            outcome_from_decision(&record.decision),
            description,
        );

        self.records.lock().unwrap().push(ralph_record);
    }

    fn flush(&self) {
        // No-op: records are already stored in memory
    }
}
