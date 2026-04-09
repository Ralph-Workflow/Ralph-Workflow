//! Host abstraction traits for MCP server.
//!
//! These traits abstract the capabilities needed by MCP tool handlers from
//! the concrete AgentSession and Workspace implementations in ralph-workflow.
//!
//! # Design Rationale
//!
//! mcp-server is the I/O boundary crate that cannot depend on ralph-workflow
//! due to the circular dependency (ralph-workflow -> mcp-server -> ralph-workflow).
//!
//! Instead, mcp-server defines minimal traits that ralph-workflow implements.
//! This follows the hexagonal (ports and adapters) architecture pattern.
//!
//! # Trait Hierarchy
//!
//! - [`HostSession`] - Session capabilities and identity
//! - [`WorkspaceAdapter`] - Workspace file operations
//! - [`crate::dispatch::AuditSink`] - Audit event sink
//!
//! # Capability Model
//!
//! The MCP server defines its own [`McpCapability`] enum representing the
//! capabilities a tool can require. The host session's `check_capability` method
//! receives typed `McpCapability` values and returns `AccessDecision`.
//!
//! Only [`AccessDecision::Deny`] with code [`CapabilityDenied`][crate::dispatch::access::AccessDeniedCode::CapabilityDenied]
//! is returned by the host session. All other denial codes are generated internally
//! by the MCP server's [`EnforcementContext`][crate::io::access::EnforcementContext].

use crate::dispatch::access::{AccessDecision, McpCapability};
use std::path::Path;

/// Session identity and capability checking.
///
/// This trait abstracts the AgentSession type from ralph-workflow.
///
/// The only [`AccessDecision`] variant that should be returned by implementations
/// is [`AccessDecision::Deny`] with code
/// [`CapabilityDenied`][crate::dispatch::access::AccessDeniedCode::CapabilityDenied].
/// All other denial codes are generated internally by the MCP server.
pub trait HostSession: Send + Sync {
    /// Get the session identifier.
    fn session_id(&self) -> &str;

    /// Check if the session has a specific capability.
    ///
    /// The MCP server calls this method after passing its own pre-dispatch checks
    /// (tool filter, access mode, root_dir). The host session should return
    /// [`AccessDecision::Allow`] if the capability is granted, or
    /// [`AccessDecision::Deny`] with code [`CapabilityDenied`][crate::dispatch::access::AccessDeniedCode::CapabilityDenied]
    /// if the capability is not granted.
    fn check_capability(&self, cap: McpCapability) -> AccessDecision;
}

/// Workspace file operations.
///
/// This trait abstracts the Workspace trait from ralph-workflow. Implementations
/// are provided by the host application and are called by `mcp-server` only after
/// all access control checks have passed (tool filter, access mode, root_dir boundary,
/// and capability check). Implementations do NOT need to re-enforce these checks.
///
/// ## Error Semantics
///
/// All methods that can fail return `Result<_, String>`. The `String` error is converted
/// to a `ToolError::ExecutionError` by the dispatch layer and returned to the client as
/// a JSON-RPC error with code `-32000`. Implementations should return descriptive error
/// messages that are safe to surface to the MCP client.
///
/// ## Path Handling
///
/// All paths passed to adapter methods have already been validated against `root_dir`.
/// Paths are passed as-is from the MCP client request after root_dir boundary enforcement.
/// Implementations should treat them as relative to the workspace root.
///
/// ## Implementing this Trait
///
/// Implementors must provide a consistent view of the workspace across calls.
/// `exists()` returning `true` must guarantee that `read()` succeeds for the same path
/// within a single request/response cycle (absent external mutation).
pub trait WorkspaceAdapter: Send + Sync {
    /// Read a file's complete contents as a UTF-8 string.
    ///
    /// ## Errors
    ///
    /// Returns `Err(String)` if the file does not exist, cannot be read,
    /// or its contents are not valid UTF-8. The error message is returned
    /// to the MCP client as a `ToolError::ExecutionError` (JSON-RPC code `-32000`).
    fn read(&self, path: &Path) -> Result<String, String>;

    /// Write content to a file, creating it if it does not exist.
    ///
    /// ## Side Effects
    ///
    /// Creates the file and any required parent directories. Overwrites existing content.
    ///
    /// ## Errors
    ///
    /// Returns `Err(String)` if the file cannot be written (e.g., permission denied,
    /// disk full, path traversal blocked). The error is returned to the MCP client as
    /// a `ToolError::ExecutionError` (JSON-RPC code `-32000`).
    fn write(&self, path: &Path, content: &str) -> Result<(), String>;

    /// Check if a path exists in the workspace.
    ///
    /// Returns `true` if the path exists (as a file or directory), `false` otherwise.
    /// Must not panic; treat all I/O errors as `false`.
    fn exists(&self, path: &Path) -> bool;

    /// Read directory entries at the given path.
    ///
    /// Returns a flat list of immediate children. Does not recurse into subdirectories
    /// unless the implementation chooses to. Each entry indicates whether it is a
    /// directory or file via [`DirEntry::is_dir`].
    ///
    /// ## Errors
    ///
    /// Returns `Err(String)` if the path does not exist, is not a directory,
    /// or cannot be read. The error is returned as `ToolError::ExecutionError`.
    fn read_dir(&self, path: &Path) -> Result<Vec<DirEntry>, String>;
}

/// Directory entry with type information.
#[derive(Debug, Clone)]
pub struct DirEntry {
    /// Path to the entry.
    pub path: String,
    /// Whether this is a directory.
    pub is_dir: bool,
}

impl DirEntry {
    /// Check if this entry is a directory.
    pub fn is_dir(&self) -> bool {
        self.is_dir
    }

    /// Check if this entry is a file.
    pub fn is_file(&self) -> bool {
        !self.is_dir
    }
}
