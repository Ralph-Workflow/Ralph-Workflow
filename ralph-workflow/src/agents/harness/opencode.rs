//! OpenCode harness configuration.
//!
//! Generates MCP server configuration for OpenCode CLI.
//! The MCP server entry tells OpenCode to spawn `ralph --mcp-proxy` which
//! bridges stdio to Ralph's Unix socket MCP server.

use crate::agents::harness::{AgentHarness, HarnessConfig};
use crate::agents::session::AgentSession;

/// Harness for OpenCode agent.
pub struct OpenCodeHarness;

impl AgentHarness for OpenCodeHarness {
    fn generate(&self, session: &AgentSession, mcp_endpoint: &str) -> HarnessConfig {
        let config = serde_json::json!({
            "mcp": {
                "ralph": {
                    "type": "local",
                    "command": ["ralph", "--mcp-proxy"],
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
        let config = harness.generate(&session, "unix:///tmp/ralph-mcp/test.sock");
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
