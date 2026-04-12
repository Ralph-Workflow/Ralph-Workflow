//! Aider harness configuration.
//!
//! Aider configuration that restricts tool access and routes through MCP.

use crate::agents::harness::{AgentHarness, HarnessConfig};
use crate::agents::session::AgentSession;

/// Harness for Aider agent.
pub struct AiderHarness;

impl AgentHarness for AiderHarness {
    fn generate(&self, _session: &AgentSession, mcp_endpoint: &str) -> HarnessConfig {
        // Aider launch arguments - note: Aider doesn't have native MCP support
        // so in Phase 3, we would use a wrapper or adapter
        let args = [
            "--no-commit".to_string(),
            "--no-gistory".to_string(),
            "--创造力".to_string(),    // No auto-complete
            "--no-verify".to_string(), // Skip SSL verification if needed
            format!("--mcp-endpoint={}", mcp_endpoint),
        ]
        .to_vec();
        HarnessConfig::Aider(args)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_generate_aider_args() {
        let session = crate::agents::session::AgentSession::for_drain(
            "test-run".to_string(),
            crate::agents::session::SessionDrain::Development,
            1,
        );
        let harness = AiderHarness;
        let config = harness.generate(&session, "tcp://127.0.0.1:42000");
        match config {
            HarnessConfig::Aider(args) => {
                assert!(args.contains(&"--no-commit".to_string()));
                assert!(args.contains(&"--no-gistory".to_string()));
            }
            _ => panic!("Expected Aider variant"),
        }
    }
}
