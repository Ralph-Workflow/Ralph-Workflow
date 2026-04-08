//! MCP server startup orchestration for ralph-workflow app initialization.
//!
//! This module is the canonical entry point for starting an MCP server
//! bound to an `AgentSession`. It owns the drain-to-access-mode mapping
//! and exposes a single `start_mcp_server_for_session` function that all
//! agent-launch paths should call.
//!
//! # Access Mode Policy
//!
//! The access mode is determined by the session drain per RFC-009:
//!
//! | Drain | AccessMode | Rationale |
//! |-------|------------|-----------|
//! | Planning | ReadOnly | No write mutations during planning |
//! | Analysis | ReadOnly | Read-only analysis phase |
//! | Review | ReadOnly | Reviewers read but do not write |
//! | Fix | ReadOnly | Fix phase writes go through the reducer, not MCP |
//! | Development | ReadWrite | Full tool access for coding agents |
//! | Commit | ReadWrite | Commit agents need file access for commit generation |

use crate::agents::session::{AgentSession, SessionDrain};
use crate::mcp_server::capability_mapping::drain_to_access_mode;
use crate::mcp_server::session_bridge::{SessionBridge, SessionBridgeError};
use crate::workspace::Workspace;
use mcp_server::dispatch::access::AccessMode;
use std::sync::Arc;

/// Start an MCP server for the given agent session.
///
/// Creates a `SessionBridge` configured with the drain-appropriate `AccessMode`
/// and starts it. Returns the started bridge, ready to accept agent connections
/// via the endpoint returned by `bridge.endpoint_uri()`.
///
/// # Errors
///
/// Returns `SessionBridgeError` if the Unix socket cannot be bound or the
/// server thread fails to start.
pub fn start_mcp_server_for_session(
    session: AgentSession,
    workspace: Arc<dyn Workspace>,
) -> Result<SessionBridge, SessionBridgeError> {
    let mut bridge = SessionBridge::new(session, workspace);
    bridge.start()?;
    Ok(bridge)
}

/// Pure mapping: determine the MCP `AccessMode` for a session drain.
///
/// Delegates to `mcp_server::capability_mapping::drain_to_access_mode`.
/// Exposed here so callers can inspect the mode without constructing a bridge.
pub fn access_mode_for_drain(drain: SessionDrain) -> AccessMode {
    drain_to_access_mode(drain)
}

#[cfg(test)]
mod tests {
    use super::*;
    use mcp_server::dispatch::access::AccessMode;

    #[test]
    fn planning_drain_is_read_only() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Planning),
            AccessMode::ReadOnly
        );
    }

    #[test]
    fn analysis_drain_is_read_only() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Analysis),
            AccessMode::ReadOnly
        );
    }

    #[test]
    fn review_drain_is_read_only() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Review),
            AccessMode::ReadOnly
        );
    }

    #[test]
    fn fix_drain_is_read_only() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Fix),
            AccessMode::ReadOnly
        );
    }

    #[test]
    fn development_drain_is_read_write() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Development),
            AccessMode::ReadWrite
        );
    }

    #[test]
    fn commit_drain_is_read_write() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Commit),
            AccessMode::ReadWrite
        );
    }
}
