//! Claude Code harness configuration.
//!
//! Generates settings.json with deny-all permissions and MCP server configuration.

use std::collections::HashMap;

use crate::agents::harness::{AgentHarness, ClaudeCodeSettings, HarnessConfig};
use crate::agents::session::AgentSession;

/// Harness for Claude Code agent.
pub struct ClaudeHarness;

impl AgentHarness for ClaudeHarness {
    fn generate(&self, session: &AgentSession, mcp_endpoint: &str) -> HarnessConfig {
        // Build MCP env vars in a single functional pass.
        let mcp_env = HashMap::from([
            ("RALPH_MCP_ENDPOINT".to_string(), mcp_endpoint.to_string()),
            (
                "RALPH_SESSION_ID".to_string(),
                session.session_id.as_str().to_string(),
            ),
        ]);

        // Resolve the absolute path to the ralph binary using the current executable path.
        // When ralph itself is the running process, current_exe() returns the absolute
        // path to the ralph binary, which is embedded in settings.json so agents can
        // spawn `ralph --mcp-proxy` without relying on PATH being set in their environment.
        let ralph_command = std::env::current_exe()
            .ok()
            .and_then(|p| p.to_str().map(String::from))
            .unwrap_or_else(|| "ralph".to_string());

        let settings = ClaudeCodeSettings {
            mcp_servers: HashMap::from([(
                "ralph".to_string(),
                crate::agents::harness::MCPServerConfig {
                    command: ralph_command,
                    args: vec!["--mcp-proxy".to_string()],
                    env: mcp_env,
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
        };

        let json = serde_json::to_string_pretty(&settings)
            .unwrap_or_else(|e| format!("{{\"error\": \"{e}\"}}"));
        HarnessConfig::ClaudeCode(json)
    }
}

use crate::agents::harness::ClaudePermissions;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_generate_claude_settings() {
        let session = crate::agents::session::AgentSession::for_drain(
            "test-run".to_string(),
            crate::agents::session::SessionDrain::Development,
            1,
        );
        let harness = ClaudeHarness;
        let config = harness.generate(&session, "unix:///tmp/ralph-mcp/test.sock");
        match config {
            HarnessConfig::ClaudeCode(json) => {
                assert!(json.contains("mcpServers"));
                assert!(json.contains("permissions"));
                assert!(json.contains("\"ralph\""));
                assert!(json.contains("--mcp-proxy"));
                assert!(json.contains("RALPH_MCP_ENDPOINT"));
            }
            _ => panic!("Expected ClaudeCode variant"),
        }
    }
}
