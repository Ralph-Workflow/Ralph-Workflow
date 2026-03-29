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
) -> Result<
    mcp_server::io::SessionBridge,
    mcp_server::io::SessionBridgeError,
> {
    use mcp_server::io::access::McpServerConfig;
    use mcp_server::dispatch::host::DirEntry as McpDirEntry;
    use mcp_server::dispatch::ToolRegistry;
    use std::sync::Arc;

    // Wrapper to adapt Arc<dyn Workspace> to Arc<dyn WorkspaceAdapter>
    // since we cannot directly coerce between these Arc types.
    struct WorkspaceAsWorkspaceAdapter {
        inner: Arc<dyn crate::workspace::Workspace>,
    }

    impl mcp_server::WorkspaceAdapter for WorkspaceAsWorkspaceAdapter {
        fn read(&self, path: &std::path::Path) -> Result<String, String> {
            self.inner.read(path).map_err(|e| e.to_string())
        }

        fn write(&self, path: &std::path::Path, content: &str) -> Result<(), String> {
            self.inner.write(path, content).map_err(|e| e.to_string())
        }

        fn exists(&self, path: &std::path::Path) -> bool {
            self.inner.exists(path)
        }

        fn read_dir(&self, path: &std::path::Path) -> Result<Vec<McpDirEntry>, String> {
            self.inner
                .read_dir(path)
                .map_err(|e| e.to_string())
                .map(|entries: Vec<crate::workspace::DirEntry>| {
                    entries
                        .into_iter()
                        .map(|e| McpDirEntry {
                            path: e.path().display().to_string(),
                            is_dir: e.is_dir(),
                        })
                        .collect()
                })
        }
    }

    // Create HostSession from AgentSession
    // AgentSession implements HostSession via boundary/mcp_adapter.rs
    let host_session: Arc<dyn mcp_server::HostSession> = Arc::new(session);

    // Wrap workspace as WorkspaceAdapter
    let workspace_adapter: Arc<dyn mcp_server::WorkspaceAdapter> =
        Arc::new(WorkspaceAsWorkspaceAdapter { inner: workspace_arc.clone() });

    // Create server config with workspace root
    let config = McpServerConfig::new(workspace_arc.root().to_path_buf());

    // Create bridge with empty tool registry (tools are registered elsewhere)
    let mut bridge = mcp_server::io::SessionBridge::new(
        host_session,
        config,
        workspace_adapter,
        ToolRegistry::new(vec![]),
    );
    bridge.start()?;
    Ok(bridge)
}
