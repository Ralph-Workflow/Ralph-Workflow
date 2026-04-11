//! OpenCode harness configuration.
//!
//! Generates MCP server configuration for OpenCode CLI.
//! The MCP server entry tells OpenCode to spawn `ralph --mcp-proxy` which
//! bridges stdio to Ralph's TCP loopback MCP server.

use crate::agents::harness::{AgentHarness, HarnessConfig};
use crate::agents::session::AgentSession;

/// Harness for OpenCode agent.
pub struct OpenCodeHarness;

impl AgentHarness for OpenCodeHarness {
    fn generate(&self, session: &AgentSession, mcp_endpoint: &str) -> HarnessConfig {
        // Resolve the absolute path to the ralph binary. Falls back to bare "ralph"
        // if current_exe() cannot be determined.
        let ralph_command = std::env::current_exe()
            .ok()
            .and_then(|p| p.to_str().map(String::from))
            .unwrap_or_else(|| "ralph".to_string());
        let config = serde_json::json!({
            "mcp": {
                "ralph": {
                    "type": "local",
                    "command": [ralph_command, "--mcp-proxy"],
                    "enabled": true,
                    "environment": {
                        "RALPH_MCP_ENDPOINT": mcp_endpoint,
                        "RALPH_SESSION_ID": session.session_id.as_str()
                    }
                }
            }
        });
        HarnessConfig::OpenCode(
            serde_json::to_string_pretty(&config).unwrap_or_else(|_| "{}".to_string()),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_generate_opencode_config() {
        let session = crate::agents::session::AgentSession::for_drain(
            "test-run".to_string(),
            crate::agents::session::SessionDrain::Development,
            1,
        );
        let harness = OpenCodeHarness;
        let config = harness.generate(&session, "tcp://127.0.0.1:42000");
        match config {
            HarnessConfig::OpenCode(json) => {
                assert!(json.contains("\"mcp\""));
                assert!(json.contains("ralph"));
                assert!(json.contains("--mcp-proxy"));
                assert!(json.contains("RALPH_MCP_ENDPOINT"));
            }
            _ => panic!("Expected OpenCode variant"),
        }
    }
}
