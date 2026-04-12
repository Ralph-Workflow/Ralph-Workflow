use anyhow::{anyhow, Context, Result};
use std::path::PathBuf;
use std::sync::Arc;

use crate::agents::session::{AgentSession, AgentSessionId, SessionDrain};
use crate::agents::tool_manifest::visible_mcp_tool_names_owned;
use crate::mcp_server::capability_mapping::{
    drain_class_for_session, drain_to_access_mode, drain_to_policy_mode,
};
use crate::mcp_server::tool_bridge::{
    build_ralph_tool_registry, RalphHostSessionAdapter, RalphWorkspaceAdapter,
};
use crate::workspace::{Workspace, WorkspaceFs};
use mcp_server::dispatch::access::ToolFilter;
use mcp_server::io::access::McpServerConfig;
use mcp_server::io::{McpServer, McpStream, ServerState, StdioTransport};

const SESSION_ID_ENV: &str = "RALPH_SESSION_ID";
const RUN_ID_ENV: &str = "RALPH_MCP_RUN_ID";
const DRAIN_ENV: &str = "RALPH_SESSION_DRAIN";
const GENERATION_ENV: &str = "RALPH_MCP_GENERATION";

pub fn run_mcp_stdio() -> Result<()> {
    let root = std::env::current_dir().context("failed to resolve current directory")?;
    let session = build_session_from_env()?;
    run_stdio_server_for_session(root, session)
}

fn run_stdio_server_for_session(root: PathBuf, session: AgentSession) -> Result<()> {
    let workspace: Arc<dyn Workspace> = Arc::new(WorkspaceFs::new(root.clone()));
    let session_arc = Arc::new(session);
    let host = Arc::new(RalphHostSessionAdapter::new(Arc::clone(&session_arc)));
    let ws = Arc::new(RalphWorkspaceAdapter::new(Arc::clone(&workspace)));
    let registry = build_ralph_tool_registry(Arc::clone(&session_arc), Arc::clone(&workspace));
    let drain = session_arc.drain;
    let visible_tools = visible_mcp_tool_names_owned(session_arc.capabilities());

    let mut config = McpServerConfig::new(root)
        .with_session_id(session_arc.session_id.as_str().to_string())
        .with_access_mode(drain_to_access_mode(drain))
        .with_policy_mode(drain_to_policy_mode(drain))
        .with_drain(drain.as_str().to_string())
        .with_drain_class(drain_class_for_session(drain))
        .with_tool_filter(ToolFilter::Allowlist(visible_tools))
        .with_run_id(session_arc.run_id.clone());

    if let Ok(raw_generation) = std::env::var(GENERATION_ENV) {
        let generation = raw_generation
            .parse::<u32>()
            .with_context(|| format!("invalid {GENERATION_ENV}='{raw_generation}'"))?;
        config = config.with_generation(generation);
    }

    let server = McpServer::new(host, config, ws, registry, None);
    run_server_loop(server)
}

fn run_server_loop(server: McpServer) -> Result<()> {
    let mut stream = StdioTransport::with_default_stdio();
    let mut state = ServerState::Uninitialized;

    loop {
        let maybe_request = McpStream::read_request(&mut stream)
            .map_err(|e| anyhow!("transport read error: {e}"))?;
        let Some(request) = maybe_request else {
            return Ok(());
        };

        let (response, next_state) = server.handle_request(request, state);
        state = next_state;

        if let Some(payload) = response {
            McpStream::write_response(&mut stream, &payload)
                .map_err(|e| anyhow!("transport write error: {e}"))?;
        }
        if state == ServerState::Shutdown {
            return Ok(());
        }
    }
}

fn build_session_from_env() -> Result<AgentSession> {
    let session_id = std::env::var(SESSION_ID_ENV)
        .with_context(|| format!("missing environment variable {SESSION_ID_ENV}"))?;
    let run_id = std::env::var(RUN_ID_ENV).with_context(|| format!("missing {RUN_ID_ENV}"))?;
    let drain_raw = std::env::var(DRAIN_ENV).with_context(|| format!("missing {DRAIN_ENV}"))?;
    let drain = parse_drain(&drain_raw)?;

    let mut session = AgentSession::for_drain(run_id, drain, 1);
    session.session_id = AgentSessionId::from_string(session_id);
    Ok(session)
}

fn parse_drain(raw: &str) -> Result<SessionDrain> {
    match raw {
        "planning" => Ok(SessionDrain::Planning),
        "development" => Ok(SessionDrain::Development),
        "analysis" => Ok(SessionDrain::Analysis),
        "review" => Ok(SessionDrain::Review),
        "fix" => Ok(SessionDrain::Fix),
        "commit" => Ok(SessionDrain::Commit),
        _ => Err(anyhow!("unsupported session drain '{raw}'")),
    }
}
