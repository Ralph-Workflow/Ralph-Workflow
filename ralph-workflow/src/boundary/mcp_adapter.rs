//! MCP adapter for ralph-workflow types.
//!
//! This module provides implementations of `mcp_server::HostSession` and
//! `mcp_server::WorkspaceAdapter` for ralph-workflow's `AgentSession` and
//! `Workspace` types, bridging the boundary crate with the workflow types.

use crate::agents::session::{AgentSession, Capability};
use crate::workspace::{DirEntry, Workspace};
use mcp_server::dispatch::access::{AccessDecision, McpCapability};
use mcp_server::dispatch::host::DirEntry as McpDirEntry;
use std::path::Path;

/// Error type for adapter operations.
#[derive(Debug, thiserror::Error)]
pub enum AdapterError {
    #[error("Unknown capability: {0}")]
    UnknownCapability(String),
}

// ============================================================================
// HostSession adapter for AgentSession
// ============================================================================

impl mcp_server::HostSession for AgentSession {
    fn session_id(&self) -> &str {
        self.session_id.as_str()
    }

    fn check_capability(&self, capability: McpCapability) -> AccessDecision {
        let ephemeral = self.check_capability(Capability::WorkspaceWriteEphemeral);
        let tracked = self.check_capability(Capability::WorkspaceWriteTracked);
        let mapped = crate::mcp_server::capability_mapping::lookup_ralph_capability(capability)
            .map(|cap| (cap, self.check_capability(cap)));
        crate::mcp_server::capability_mapping::capability_policy(
            capability, ephemeral, tracked, mapped,
        )
    }

    fn is_parallel_worker(&self) -> bool {
        AgentSession::is_parallel_worker(self)
    }

    fn check_edit_area(&self, path: &str) -> AccessDecision {
        crate::mcp_server::capability_mapping::policy_from_outcome(AgentSession::check_edit_area(
            self, path,
        ))
    }
}

// ============================================================================
// WorkspaceAdapter adapter for Workspace
// ============================================================================

impl mcp_server::WorkspaceAdapter for dyn Workspace {
    fn read(&self, path: &Path) -> Result<String, String> {
        Workspace::read(self, path).map_err(|e| e.to_string())
    }

    fn write(&self, path: &Path, content: &str) -> Result<(), String> {
        Workspace::write(self, path, content).map_err(|e| e.to_string())
    }

    fn exists(&self, path: &Path) -> bool {
        Workspace::exists(self, path)
    }

    fn read_dir(&self, path: &Path) -> Result<Vec<McpDirEntry>, String> {
        Workspace::read_dir(self, path)
            .map_err(|e| e.to_string())
            .map(|entries: Vec<DirEntry>| {
                entries
                    .into_iter()
                    .map(|e| McpDirEntry {
                        path: e.path().display().to_string(),
                        is_dir: e.is_dir(),
                    })
                    .collect()
            })
    }
}
