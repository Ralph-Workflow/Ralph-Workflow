//! Claude Code harness configuration.
//!
//! Generates settings.json with deny-all permissions and MCP server configuration.

use crate::agents::harness::{AgentHarness, ClaudeCodeSettings, HarnessConfig};
use crate::agents::session::AgentSession;
use crate::agents::tool_manifest::visible_mcp_tool_names;
use std::collections::HashMap;

/// Harness for Claude Code agent.
pub struct ClaudeHarness;

impl AgentHarness for ClaudeHarness {
    fn generate(&self, session: &AgentSession, mcp_endpoint: &str) -> HarnessConfig {
        let headers = std::collections::HashMap::from([
            (
                "X-Ralph-Session-Id".to_string(),
                session.session_id.as_str().to_string(),
            ),
            ("X-Ralph-Run-Id".to_string(), session.run_id.clone()),
            (
                "X-Ralph-Drain".to_string(),
                session.drain.as_str().to_string(),
            ),
        ]);

        let settings = ClaudeCodeSettings {
            mcp_servers: HashMap::from([(
                "ralph".to_string(),
                crate::agents::harness::MCPServerConfig {
                    r#type: Some("http".to_string()),
                    url: Some(mcp_endpoint.to_string()),
                    headers: Some(headers),
                    command: None,
                    args: None,
                    env: None,
                },
            )]),
            permissions: ClaudePermissions {
                allow: visible_mcp_tool_names(session.capabilities())
                    .into_iter()
                    .map(|tool| format!("mcp__ralph__{tool}"))
                    .collect(),
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
        let config = harness.generate(&session, "tcp://127.0.0.1:42000");
        match config {
            HarnessConfig::ClaudeCode(json) => {
                assert!(json.contains("mcpServers"));
                assert!(json.contains("permissions"));
                assert!(json.contains("\"ralph\""));
                assert!(json.contains("\"type\": \"http\""));
                assert!(json.contains("tcp://127.0.0.1:42000"));
                assert!(json.contains("X-Ralph-Drain"));
            }
            _ => panic!("Expected ClaudeCode variant"),
        }
    }

    #[test]
    fn test_generate_claude_settings_uses_session_manifest() {
        let session = crate::agents::session::AgentSession::for_drain(
            "planning-run".to_string(),
            crate::agents::session::SessionDrain::Planning,
            1,
        );
        let harness = ClaudeHarness;
        let config = harness.generate(&session, "tcp://127.0.0.1:42000");
        match config {
            HarnessConfig::ClaudeCode(json) => {
                assert!(json.contains("mcp__ralph__ralph_submit_artifact"));
                assert!(!json.contains("mcp__ralph__report_progress"));
                assert!(!json.contains("mcp__ralph__read_env"));
                assert!(!json.contains("mcp__ralph__write_file"));
                assert!(!json.contains("mcp__ralph__exec"));
            }
            _ => panic!("Expected ClaudeCode variant"),
        }
    }
}
