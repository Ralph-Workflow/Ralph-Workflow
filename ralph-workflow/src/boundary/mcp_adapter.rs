//! MCP adapter for ralph-workflow types.
//!
//! This module provides implementations of `mcp_server::HostSession` and
//! `mcp_server::WorkspaceAdapter` for ralph-workflow's `AgentSession` and
//! `Workspace` types, bridging the boundary crate with the workflow types.

use crate::agents::session::{AgentSession, Capability};
use crate::workspace::{DirEntry, Workspace};
use mcp_server::dispatch::access::{AccessDecision, AccessDeniedCode, McpCapability};
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
        let cap = match parse_capability(capability) {
            Some(cap) => cap,
            None => {
                // Unknown McpCapability variant — deny explicitly
                return AccessDecision::Deny {
                    reason: format!("Unknown capability variant: {:?}", capability),
                    code: AccessDeniedCode::CapabilityDenied,
                };
            }
        };
        translate_policy_outcome(self.check_capability(cap))
    }

    fn is_parallel_worker(&self) -> bool {
        AgentSession::is_parallel_worker(self)
    }

    fn check_edit_area(&self, path: &str) -> AccessDecision {
        translate_policy_outcome(AgentSession::check_edit_area(self, path))
    }
}

/// Translate our PolicyOutcome to mcp_server's AccessDecision.
fn translate_policy_outcome(outcome: crate::agents::session::PolicyOutcome) -> AccessDecision {
    match outcome {
        crate::agents::session::PolicyOutcome::Approved => AccessDecision::Allow,
        crate::agents::session::PolicyOutcome::Denied { reason } => AccessDecision::Deny {
            reason,
            code: AccessDeniedCode::CapabilityDenied,
        },
        crate::agents::session::PolicyOutcome::ApprovedWithRestriction { .. } => {
            // ApprovedWithRestriction means approved but with internal tracking.
            // For the MCP server, treat as Allow since the session tracks restrictions.
            AccessDecision::Allow
        }
    }
}

/// Parse an McpCapability to the internal Capability enum.
///
/// Returns `None` if the capability is unknown (new variant not yet mapped),
/// which signals that the check_capability call should deny.
fn parse_capability(cap: McpCapability) -> Option<Capability> {
    match cap {
        McpCapability::WorkspaceRead => Some(Capability::WorkspaceRead),
        McpCapability::WorkspaceWriteEphemeral => Some(Capability::WorkspaceWriteEphemeral),
        McpCapability::WorkspaceWriteTracked => Some(Capability::WorkspaceWriteTracked),
        McpCapability::ProcessExecBounded => Some(Capability::ProcessExecBounded),
        McpCapability::ArtifactSubmit => Some(Capability::ArtifactSubmit),
        McpCapability::GitStatusRead => Some(Capability::GitStatusRead),
        McpCapability::GitWrite => Some(Capability::GitWrite),
        McpCapability::EnvRead => Some(Capability::EnvRead),
        // #[non_exhaustive] McpCapability — deny unknown variants explicitly
        _ => None,
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
