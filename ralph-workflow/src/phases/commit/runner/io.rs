pub fn create_session_and_get_attempt_number(
    log_dir: &Path,
    workspace: &dyn Workspace,
) -> (CommitLogSession, usize) {
    let mut session = CommitLogSession::new(
        log_dir
            .to_str()
            .expect("Path contains invalid UTF-8 - all paths in this codebase should be UTF-8"),
        workspace,
    )
    .unwrap_or_else(|_| CommitLogSession::noop());
    let attempt_number = session.next_attempt_number();
    (session, attempt_number)
}

pub(super) fn unique_commit_plumbing_run_id(label: &str) -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0_u128, |d| d.as_nanos());
    let compact = (nanos as u64) ^ (std::process::id() as u64);
    format!("{label}-{compact:016x}")
}

pub fn start_mcp_bridge(
    session: crate::agents::session::AgentSession,
    workspace_arc: std::sync::Arc<dyn crate::workspace::Workspace>,
) -> Result<crate::mcp_server::session_bridge::SessionBridge, crate::mcp_server::session_bridge::SessionBridgeError>
{
    use crate::mcp_server::session_bridge::SessionBridge;

    // Create the boundary SessionBridge which encapsulates internal MCP bridge + adapters
    let mut bridge = SessionBridge::new(session, workspace_arc);
    bridge.start()?;
    Ok(bridge)
}
