//! Codex CLI harness configuration.
//!
//! Generates MCP server configuration for OpenAI Codex CLI.

use crate::agents::harness::{AgentHarness, HarnessConfig};
use crate::agents::session::AgentSession;

/// Harness for Codex agent.
pub struct CodexHarness;

impl AgentHarness for CodexHarness {
    fn generate(&self, session: &AgentSession, mcp_endpoint: &str) -> HarnessConfig {
        let config = format!(
            "[mcp_servers.ralph]\ncommand = \"ralph\"\nargs = [\"--mcp-proxy\"]\n[mcp_servers.ralph.env]\nRALPH_MCP_ENDPOINT = \"{}\"\nRALPH_SESSION_ID = \"{}\"\n",
            mcp_endpoint,
            session.session_id.as_str()
        );
        HarnessConfig::Codex(config)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_generate_codex_config() {
        let session = crate::agents::session::AgentSession::for_drain(
            "test-run".to_string(),
            crate::agents::session::SessionDrain::Development,
            1,
        );
        let harness = CodexHarness;
        let config = harness.generate(&session, "unix:///tmp/ralph-mcp/test.sock");
        match config {
            HarnessConfig::Codex(toml) => {
                assert!(toml.contains("[mcp_servers.ralph]"));
                assert!(toml.contains("command = \"ralph\""));
                assert!(toml.contains("--mcp-proxy"));
                assert!(toml.contains("RALPH_MCP_ENDPOINT"));
            }
            _ => panic!("Expected Codex variant"),
        }
    }
}
