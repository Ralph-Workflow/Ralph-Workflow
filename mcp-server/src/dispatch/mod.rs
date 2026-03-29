//! Dispatch layer for MCP tool handling.
//!
//! This module contains the application logic for tool dispatch. It is NOT
//! a boundary module - handlers should be pure functions where possible.
//!
//! # Module Structure
//!
//! - [`host`] - Host abstraction traits (Session, Workspace)
//! - [`registry`] - Tool registry and dispatch logic
//!
//! # Design Principles
//!
//! 1. **No side effects in handlers** - Tool handlers should be pure functions
//!    that transform inputs to outputs. Actual I/O happens in the tool implementations
//!    which live in ralph-workflow.
//!
//! 2. **Capability gating at registry level** - The registry checks capabilities
//!    before invoking handlers, ensuring consistent policy enforcement.
//!
//! 3. **Typed errors** - Use `ToolError` for tool-specific failures rather than
//!    generic string errors.

pub mod access;
pub mod host;
pub mod registry;

pub use access::{AuditSink, McpCapability, NoOpAuditSink};
pub use host::{DirEntry, HostSession, WorkspaceAdapter};
pub use registry::{ToolError, ToolHandler, ToolMetadata, ToolRegistry};

use crate::protocol::{ToolContent, ToolResult};
use serde_json::Value;
use std::path::Path;

/// Dispatch target for request routing.
#[derive(Debug, Clone, Copy)]
pub enum DispatchTarget {
    Initialize,
    NotReady,
    Ping,
    ToolsList,
    ToolsCall,
    Unknown,
}

/// Pure routing: determine which handler should process a request.
///
/// Returns `DispatchTarget` indicating which handler to invoke.
/// This is structural protocol routing, not policy enforcement.
pub fn route_dispatch(method: &str, is_ready: bool) -> DispatchTarget {
    if method == "initialize" {
        return DispatchTarget::Initialize;
    }
    if !is_ready {
        return DispatchTarget::NotReady;
    }
    match method {
        "ping" => DispatchTarget::Ping,
        "tools/list" => DispatchTarget::ToolsList,
        "tools/call" => DispatchTarget::ToolsCall,
        _ => DispatchTarget::Unknown,
    }
}

/// Handle workspace read file operation.
///
/// Requires: `WorkspaceRead` capability
pub fn handle_read_file(
    _session: &dyn HostSession,
    workspace: &dyn WorkspaceAdapter,
    params: Value,
) -> Result<ToolResult, ToolError> {
    let path = params
        .get("path")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'path' parameter".to_string()))?;

    let content = workspace
        .read(Path::new(path))
        .map_err(|e| ToolError::ExecutionError(format!("Failed to read file '{}': {}", path, e)))?;

    Ok(ToolResult::success(vec![ToolContent::text(content)]))
}

/// Handle workspace write file operation.
///
/// Requires: `WorkspaceWriteTracked` or `WorkspaceWriteEphemeral` capability
pub fn handle_write_file(
    session: &dyn HostSession,
    workspace: &dyn WorkspaceAdapter,
    params: Value,
) -> Result<ToolResult, ToolError> {
    let path = params
        .get("path")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'path' parameter".to_string()))?;

    let content = params
        .get("content")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'content' parameter".to_string()))?;

    // For now, determine tracked status based on path patterns
    let is_tracked = !path.contains(".agent/") && !path.contains("/target/");

    // Check write capability
    let cap = if is_tracked {
        McpCapability::WorkspaceWriteTracked
    } else {
        McpCapability::WorkspaceWriteEphemeral
    };
    let outcome = session.check_capability(cap);
    if !outcome.is_allowed() {
        return Err(ToolError::CapabilityDenied(format!(
            "Write to '{}' requires capability '{}': {:?}",
            path, cap, outcome
        )));
    }

    // Check edit area for parallel workers
    if session.is_parallel_worker() {
        let edit_outcome = session.check_edit_area(path);
        if !edit_outcome.is_allowed() {
            return Err(ToolError::CapabilityDenied(format!(
                "Write to '{}' denied: edit area restriction",
                path
            )));
        }
    }

    workspace.write(Path::new(path), content).map_err(|e| {
        ToolError::ExecutionError(format!("Failed to write file '{}': {}", path, e))
    })?;

    Ok(ToolResult::success(vec![ToolContent::text(format!(
        "Successfully wrote {} bytes to {}",
        content.len(),
        path
    ))]))
}

/// Handle environment variable read.
///
/// Requires: `EnvRead` capability
pub fn handle_read_env(
    session: &dyn HostSession,
    _workspace: &dyn WorkspaceAdapter,
    params: Value,
) -> Result<ToolResult, ToolError> {
    let outcome = session.check_capability(McpCapability::EnvRead);
    if !outcome.is_allowed() {
        return Err(ToolError::CapabilityDenied(format!(
            "Environment read requires EnvRead capability: {:?}",
            outcome
        )));
    }

    let name = params
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'name' parameter".to_string()))?;

    // Environment access is a boundary concern - actual implementation would use
    // a HostEnv trait passed from the io layer. For now, return placeholder.
    let value = "[environment access requires HostEnv implementation]";
    Ok(ToolResult::success(vec![ToolContent::text(format!(
        "{}={}",
        name, value
    ))]))
}

/// Handle git status read.
///
/// Requires: `GitStatusRead` capability
pub fn handle_git_status(
    session: &dyn HostSession,
    _workspace: &dyn WorkspaceAdapter,
    _params: Value,
) -> Result<ToolResult, ToolError> {
    let outcome = session.check_capability(McpCapability::GitStatusRead);
    if !outcome.is_allowed() {
        return Err(ToolError::CapabilityDenied(format!(
            "Git status requires GitStatusRead capability: {:?}",
            outcome
        )));
    }

    // Git status is informational - actual implementation would call git
    Ok(ToolResult::success(vec![ToolContent::text(
        "[Git status placeholder - actual git status from ralph-workflow]".to_string(),
    )]))
}

/// Handle process execution.
///
/// Requires: `ProcessExecBounded` capability
pub fn handle_exec(
    session: &dyn HostSession,
    _workspace: &dyn WorkspaceAdapter,
    params: Value,
) -> Result<ToolResult, ToolError> {
    let outcome = session.check_capability(McpCapability::ProcessExecBounded);
    if !outcome.is_allowed() {
        return Err(ToolError::CapabilityDenied(format!(
            "Process execution requires ProcessExecBounded capability: {:?}",
            outcome
        )));
    }

    let command = params
        .get("command")
        .and_then(|v| v.as_str())
        .ok_or_else(|| ToolError::InvalidParams("Missing 'command' parameter".to_string()))?;

    // Actual execution happens in ralph-workflow via Effect system
    Ok(ToolResult::success(vec![ToolContent::text(format!(
        "[Process execution placeholder: {} - actual execution from ralph-workflow]",
        command
    ))]))
}

/// Handle artifact submission.
///
/// Requires: `ArtifactSubmit` capability
pub fn handle_artifact_submit(
    session: &dyn HostSession,
    _workspace: &dyn WorkspaceAdapter,
    params: Value,
) -> Result<ToolResult, ToolError> {
    let outcome = session.check_capability(McpCapability::ArtifactSubmit);
    if !outcome.is_allowed() {
        return Err(ToolError::CapabilityDenied(format!(
            "Artifact submission requires ArtifactSubmit capability: {:?}",
            outcome
        )));
    }

    let artifact_type = params
        .get("artifact_type")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");

    Ok(ToolResult::success(vec![ToolContent::text(format!(
        "[Artifact submission placeholder: type={} - actual submission from ralph-workflow]",
        artifact_type
    ))]))
}
