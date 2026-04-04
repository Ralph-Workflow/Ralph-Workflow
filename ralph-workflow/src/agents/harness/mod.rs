//! Agent harness module for MCP-style agent-orchestrator communication.
//!
//! This module generates per-provider configuration that disables native agent tools
//! and routes all operations through Ralph's MCP server.
//!
//! # Supported Providers
//!
//! - **Claude Code**: Generates `settings.json` with deny-all permissions
//! - **OpenCode**: Restricted configuration with MCP endpoint
//! - **Aider**: Launch configuration without autonomous edits
//! - **Codex**: Sandboxed execution with MCP endpoint
//!
//! # Security Model
//!
//! All agents MUST be configured to route their operations through Ralph's MCP server.
//! Native tools (Edit, Write, Bash, Git, etc.) are disabled via harness configuration.
//! The ONLY way agents can perform side effects is through Ralph's brokered MCP tools.

mod aider;
pub mod applicator;
pub mod claude;
pub mod codex;
pub mod opencode;

pub use aider::AiderHarness;
pub use applicator::{apply_harness_config, detect_agent_type, AgentType, HarnessApplyResult};
pub use claude::ClaudeHarness;
pub use codex::CodexHarness;
pub use opencode::OpenCodeHarness;

use crate::agents::session::AgentSession;

#[cfg(test)]
use crate::agents::session::SessionDrain;
use serde::{Deserialize, Serialize};

/// Claude Code settings.json configuration.
///
/// Generates the correct camelCase JSON format expected by Claude Code,
/// with `mcpServers` for MCP server configuration and `permissions` for
/// allow/deny tool lists.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClaudeCodeSettings {
    /// MCP servers keyed by name (serializes to `mcpServers`).
    pub mcp_servers: std::collections::HashMap<String, MCPServerConfig>,
    /// Permission allow/deny lists.
    pub permissions: ClaudePermissions,
}

/// Configuration for a single MCP server entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MCPServerConfig {
    pub command: String,
    pub args: Vec<String>,
    pub env: std::collections::HashMap<String, String>,
}

/// Claude Code permission allow/deny lists.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClaudePermissions {
    pub allow: Vec<String>,
    pub deny: Vec<String>,
}

impl Default for ClaudeCodeSettings {
    fn default() -> Self {
        Self {
            mcp_servers: std::collections::HashMap::from([(
                "ralph".to_string(),
                MCPServerConfig {
                    command: "ralph".to_string(),
                    args: vec!["--mcp-proxy".to_string()],
                    env: std::collections::HashMap::new(),
                },
            )]),
            permissions: ClaudePermissions {
                allow: vec![
                    "mcp__ralph__ralph_submit_artifact".to_string(),
                    "mcp__ralph__read_file".to_string(),
                    "mcp__ralph__write_file".to_string(),
                    "mcp__ralph__list_directory".to_string(),
                    "mcp__ralph__list_directory_recursive".to_string(),
                    "mcp__ralph__search_files".to_string(),
                    "mcp__ralph__git_status".to_string(),
                    "mcp__ralph__git_diff".to_string(),
                    "mcp__ralph__git_log".to_string(),
                    "mcp__ralph__git_show".to_string(),
                    "mcp__ralph__exec".to_string(),
                    "mcp__ralph__report_progress".to_string(),
                    "mcp__ralph__declare_complete".to_string(),
                    "mcp__ralph__read_env".to_string(),
                    "mcp__ralph__coordinate".to_string(),
                ],
                deny: vec![
                    "Edit".to_string(),
                    "Write".to_string(),
                    "Bash".to_string(),
                    "Read".to_string(),
                    "Glob".to_string(),
                    "Grep".to_string(),
                    "NotebookEdit".to_string(),
                    "WebFetch".to_string(),
                    "TodoWrite".to_string(),
                ],
            },
        }
    }
}

/// Base harness trait for generating agent configuration.
pub trait AgentHarness {
    /// Generate the harness configuration for a session.
    fn generate(&self, session: &AgentSession, mcp_endpoint: &str) -> HarnessConfig;
}

/// Harness configuration output.
#[derive(Debug, Clone)]
pub enum HarnessConfig {
    /// Claude Code settings.json content.
    ClaudeCode(String),
    /// OpenCode restricted config.
    OpenCode(String),
    /// Aider launch arguments.
    Aider(Vec<String>),
    /// Codex sandboxed config.
    Codex(String),
}

/// Extension trait for extracting MCP-relevant session information.
pub trait SessionMCPInfo {
    /// Get the capabilities relevant for MCP tool routing.
    fn mcp_capabilities(&self) -> Vec<String>;
    /// Get policy flags relevant for MCP operations.
    fn mcp_policy_flags(&self) -> Vec<String>;
    /// Check if the session has a specific capability.
    fn has_capability(&self, cap: &str) -> bool;
}

/// Map a capability identifier string to the corresponding `Capability` variant.
///
/// Returns `None` for unrecognised identifiers.
fn capability_from_identifier(cap: &str) -> Option<crate::agents::session::Capability> {
    use crate::agents::session::Capability;
    match cap {
        "workspace.read" => Some(Capability::WorkspaceRead),
        "workspace.write_ephemeral" => Some(Capability::WorkspaceWriteEphemeral),
        "workspace.write_tracked" => Some(Capability::WorkspaceWriteTracked),
        "process.exec_bounded" => Some(Capability::ProcessExecBounded),
        "artifact.submit" => Some(Capability::ArtifactSubmit),
        "run.report_progress" => Some(Capability::RunReportProgress),
        "git.status_read" => Some(Capability::GitStatusRead),
        "git.diff_read" => Some(Capability::GitDiffRead),
        "git.write" => Some(Capability::GitWrite),
        "env.read" => Some(Capability::EnvRead),
        _ => None,
    }
}

impl SessionMCPInfo for AgentSession {
    fn mcp_capabilities(&self) -> Vec<String> {
        self.capabilities
            .iter()
            .map(|c| c.identifier().to_string())
            .collect()
    }

    fn mcp_policy_flags(&self) -> Vec<String> {
        self.policy_flags
            .iter()
            .map(|f| f.identifier().to_string())
            .collect()
    }

    fn has_capability(&self, cap: &str) -> bool {
        capability_from_identifier(cap)
            .map(|c| self.capabilities.contains(c))
            .unwrap_or(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_claude_settings_default() {
        let settings = ClaudeCodeSettings::default();
        let ralph_server = settings
            .mcp_servers
            .get("ralph")
            .expect("ralph server should exist");
        assert_eq!(ralph_server.command, "ralph");
        assert_eq!(ralph_server.args, vec!["--mcp-proxy"]);
        assert!(settings
            .permissions
            .allow
            .contains(&"mcp__ralph__read_file".to_string()));
        assert!(settings.permissions.deny.contains(&"Edit".to_string()));
    }

    #[test]
    fn test_session_mcp_capabilities() {
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Development, 1);
        let caps = session.mcp_capabilities();
        assert!(caps.contains(&"workspace.read".to_string()));
        assert!(caps.contains(&"workspace.write_tracked".to_string()));
    }

    #[test]
    fn test_session_has_capability() {
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
        // Planning has read but not write
        assert!(session.has_capability("workspace.read"));
        assert!(!session.has_capability("workspace.write_tracked"));
    }

    #[test]
    fn test_session_mcp_policy_flags() {
        let session = AgentSession::for_drain("test-run".to_string(), SessionDrain::Planning, 1);
        let flags = session.mcp_policy_flags();
        assert!(flags.contains(&"no_edit".to_string()));
    }
}
