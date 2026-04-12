//! Dispatch layer for MCP tool handling.
//!
//! This module contains the application logic for tool dispatch. It is NOT
//! a boundary module - handlers should be pure functions where possible.
//!
//! # Module Structure
//!
//! - [`host`] - Host abstraction traits (Session, Workspace)
//! - [`registry`] - Tool registry and dispatch logic
//! - [`audit`] - Pure audit record types
//! - [`access`] - Access control types (capabilities, access mode, tool filter)
//!
//! # Enforcement Chain Order
//!
//! Every `tools/call` request passes through four sequential enforcement checks.
//! The chain short-circuits on first denial — later checks are not evaluated.
//!
//! ```text
//! tools/call
//!   → 1. ToolFilter (mcp-server, never delegated to host)
//!   → 2. AccessMode (mcp-server, never delegated to host)
//!   → 3. PathBoundary (mcp-server, never delegated to host)
//!   → 4. Capability  (host via HostSession::check_capability — the ONLY host delegation)
//!   → handler
//! ```
//!
//! **Denial codes per level:**
//!
//! | Level | Denial Code | Error |
//! |-------|-------------|-------|
//! | ToolFilter | `ToolNotAllowed` | `-32000` |
//! | AccessMode | `ReadOnlyMode` | `-32000` |
//! | PathBoundary | `OutsideRootDir` | `-32000` |
//! | Capability | `CapabilityDenied` | `-32000` |
//! | Pre-init | `NotInitialized` | `-32001` |
//!
//! # Request/Response Contract
//!
//! All tool calls use `tools/call` with params `{ "name": "<tool>", "arguments": {...} }`.
//! Success responses use `ToolResult { content: [...], isError: false }`.
//! Error responses are JSON-RPC errors with code `-32000`.
//!
//! # Design Principles
//!
//! 1. **Framework-provided handlers** - `handle_read_file` and `handle_write_file` are
//!    provided by the framework and use `WorkspaceAdapter` directly. These are the only
//!    built-in handlers.
//!
//! 2. **Host-registered tools** - All other tool semantics (git, exec, artifacts,
//!    coordination) are registered by the host application via `ToolRegistry`.
//!    The framework does not provide stub implementations for these.
//!
//! 3. **Capability gating at registry level** - The registry checks capabilities
//!    before invoking handlers, ensuring consistent policy enforcement.
//!
//! 4. **Typed errors** - Use `ToolError` for tool-specific failures rather than
//!    generic string errors.
//!
//! 5. **Host isolation** - The host is only consulted for capability check (level 4).
//!    Access mode, tool filtering, and path boundary enforcement are internal.

pub mod access;
pub mod audit;
pub mod host;
pub mod registry;

pub use access::{AuditSink, McpCapability, NoOpAuditSink};
pub use audit::AuditRecord;
pub use host::{DirEntry, HostSession, WorkspaceAdapter};
pub use registry::{ToolError, ToolHandler, ToolMetadata, ToolRegistry};

use crate::protocol::{ToolContent, ToolResult};
use serde_json::Value;
use std::path::Path;

/// Dispatch target for request routing.
#[derive(Debug, Clone, Copy)]
pub enum DispatchTarget {
    /// Route to initialize handshake handler.
    Initialize,
    /// Route to not-ready rejection (server state requires initialize before tools).
    NotReady,
    /// Route to ping handler.
    Ping,
    /// Route to tools/list handler.
    ToolsList,
    /// Route to tools/call handler.
    ToolsCall,
    /// Route to method-not-found error.
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

    workspace.write(Path::new(path), content).map_err(|e| {
        ToolError::ExecutionError(format!("Failed to write file '{}': {}", path, e))
    })?;

    Ok(ToolResult::success(vec![ToolContent::text(format!(
        "Successfully wrote {} bytes to {}",
        content.len(),
        path
    ))]))
}
