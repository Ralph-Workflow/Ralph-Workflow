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
) -> Result<mcp_server::io::SessionBridge, mcp_server::io::SessionBridgeError> {
    use crate::mcp_server::tool_bridge::{
        build_ralph_tool_registry, RalphHostSessionAdapter, RalphWorkspaceAdapter,
    };
    use mcp_server::io::access::McpServerConfig;
    use std::sync::Arc;

    // Wrap session and workspace in Arc for use by adapters and tool registry
    let session_arc = Arc::new(session);
    let workspace_for_registry = Arc::clone(&workspace_arc);

    // Build the full tool registry with all Ralph tools registered
    let registry = build_ralph_tool_registry(Arc::clone(&session_arc), workspace_for_registry);

    // Create typed adapters
    let host_session: Arc<dyn mcp_server::HostSession> =
        Arc::new(RalphHostSessionAdapter::new(Arc::clone(&session_arc)));
    let workspace_adapter: Arc<dyn mcp_server::WorkspaceAdapter> =
        Arc::new(RalphWorkspaceAdapter::new(Arc::clone(&workspace_arc)));

    // Create server config with workspace root and ReadWrite access mode
    let config = McpServerConfig::new(workspace_arc.root().to_path_buf())
        .with_access_mode(mcp_server::dispatch::access::AccessMode::ReadWrite);

    // Create and start the bridge — error propagates to caller (mandatory for MCP)
    let mut bridge = mcp_server::io::SessionBridge::new(host_session, config, workspace_adapter, registry);
    bridge.start()?;
    Ok(bridge)
}
