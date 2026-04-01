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
/// This trait abstracts the Workspace trait from ralph-workflow.
pub trait WorkspaceAdapter: Send + Sync {
    /// Read a file's contents.
    fn read(&self, path: &Path) -> Result<String, String>;

    /// Write content to a file.
    fn write(&self, path: &Path, content: &str) -> Result<(), String>;

    /// Check if a path exists.
    fn exists(&self, path: &Path) -> bool;

    /// Read directory entries.
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
