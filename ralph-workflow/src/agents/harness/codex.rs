//! Codex CLI harness configuration.
//!
//! Generates MCP server configuration for OpenAI Codex CLI.

use crate::agents::harness::{AgentHarness, HarnessConfig};
use crate::agents::session::AgentSession;

/// Harness for Codex agent.
pub struct CodexHarness;

impl AgentHarness for CodexHarness {
    fn generate(&self, session: &AgentSession, mcp_endpoint: &str) -> HarnessConfig {
        // Resolve the absolute path to the ralph binary. Falls back to bare "ralph"
        // if current_exe() cannot be determined.
        let ralph_command = std::env::current_exe()
            .ok()
            .and_then(|p| p.to_str().map(String::from))
            .unwrap_or_else(|| "ralph".to_string());
        let config = format!(
            "[mcp_servers.ralph]\ncommand = \"{}\"\nargs = [\"--mcp-proxy\"]\n[mcp_servers.ralph.env]\nRALPH_MCP_ENDPOINT = \"{}\"\nRALPH_SESSION_ID = \"{}\"\n",
            ralph_command,
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
        let config = harness.generate(&session, "tcp://127.0.0.1:42000");
        match config {
            HarnessConfig::Codex(toml) => {
                assert!(toml.contains("[mcp_servers.ralph]"));
                // command resolves to current_exe() at runtime; just assert the key is present
                assert!(toml.contains("command = "));
                assert!(toml.contains("--mcp-proxy"));
                assert!(toml.contains("RALPH_MCP_ENDPOINT"));
            }
            _ => panic!("Expected Codex variant"),
        }
    }
}
