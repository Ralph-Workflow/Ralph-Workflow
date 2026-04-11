//! MCP server startup for ralph-workflow.
//!
//! This module lives in the boundary layer and provides the canonical entry point
//! for starting an MCP server bound to an `AgentSession`.

use crate::agents::session::{AgentSession, SessionDrain};
use crate::mcp_server::capability_mapping::drain_to_access_mode;
use crate::mcp_server::session_bridge::{SessionBridge, SessionBridgeError};
use crate::workspace::Workspace;
use mcp_server::dispatch::access::AccessMode;
use mcp_server::io::heartbeat::HeartbeatPolicy;
pub use mcp_server::io::{ControlCommand, ControlError};
use std::env;
use std::sync::Arc;
use std::time::Duration;

/// Start an MCP server for the given agent session.
///
/// Creates a `SessionBridge` configured with the drain-appropriate `AccessMode`
/// and starts it. Returns the started bridge, ready to accept agent connections
/// via the endpoint returned by `bridge.endpoint_uri()`.
///
/// # Errors
///
/// Returns `SessionBridgeError` if the TCP loopback endpoint cannot be bound or the
/// server thread fails to start.
pub fn start_mcp_server_for_session(
    session: AgentSession,
    workspace: Arc<dyn Workspace>,
) -> Result<SessionBridge, SessionBridgeError> {
    let mut bridge = SessionBridge::new(session, workspace);
    let _policy = heartbeat_policy_from_env();
    bridge.start()?;
    Ok(bridge)
}

fn heartbeat_policy_from_env() -> HeartbeatPolicy {
    const DEFAULT_INTERVAL_MS: u64 = 2000;
    const DEFAULT_MISSES: u32 = 3;
    const DEFAULT_RECONNECT_MS: u64 = 10000;

    let interval = env::var("RALPH_MCP_HEARTBEAT_INTERVAL_MS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(DEFAULT_INTERVAL_MS);
    let misses = env::var("RALPH_MCP_HEARTBEAT_MISSES")
        .ok()
        .and_then(|v| v.parse::<u32>().ok())
        .unwrap_or(DEFAULT_MISSES);
    let reconnect_ms = env::var("RALPH_MCP_HEARTBEAT_RECONNECT_MS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(DEFAULT_RECONNECT_MS);

    HeartbeatPolicy::new(
        Duration::from_millis(interval),
        misses.max(1),
        Duration::from_millis(reconnect_ms),
    )
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
    fn fix_drain_is_read_write() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Fix),
            AccessMode::ReadWrite
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
    fn commit_drain_is_read_only() {
        assert_eq!(
            access_mode_for_drain(SessionDrain::Commit),
            AccessMode::ReadOnly
        );
    }
}
