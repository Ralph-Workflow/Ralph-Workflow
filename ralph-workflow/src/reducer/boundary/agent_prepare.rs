use super::*;
use crate::agents::session::audit::persist_endpoint_lease;
use std::time::SystemTime;

pub(super) struct PreparedAgentInvocation {
    pub(super) in_dev_fix: bool,
    pub(super) role: AgentRole,
    pub(super) drain: AgentDrain,
    pub(super) model: Option<String>,
    pub(super) model_name: Option<String>,
    pub(super) effective_agent: String,
    pub(super) effective_prompt: String,
    pub(super) attempt: u32,
    pub(super) logfile: String,
    pub(super) session: AgentSession,
    pub(super) session_id: Option<String>,
    pub(super) execution_start: SystemTime,
    pub(super) session_bridge: SessionBridge,
    pub(super) mcp_endpoint: Option<String>,
    pub(super) lease: Option<EndpointLease>,
    pub(super) completion_output_path: Option<&'static Path>,
}

fn persist_endpoint_lease_if_available(
    ctx: &mut PhaseContext<'_>,
    session: &AgentSession,
    lease: Option<&EndpointLease>,
) {
    if let Some(lease) = lease {
        match persist_endpoint_lease(ctx.workspace, lease) {
            Ok(()) => ctx
                .logger
                .info(&format!("RFC-009 MCP endpoint lease recorded: {lease}")),
            Err(e) => ctx.logger.warn(&format!(
                "Failed to persist MCP endpoint lease for session {}: {e}",
                session.session_id
            )),
        }
    }
}

pub(super) fn prepare_agent_invocation<F>(
    handler: &MainEffectHandler,
    ctx: &mut PhaseContext<'_>,
    drain: AgentDrain,
    role: AgentRole,
    agent: &str,
    model: Option<&str>,
    prompt_generator: F,
) -> Result<PreparedAgentInvocation>
where
    F: FnOnce(&AgentSession) -> String,
{
    let in_dev_fix = handler.state.phase == PipelinePhase::AwaitingDevFix;
    let effective_agent = resolve_effective_agent(&handler.state, in_dev_fix, agent);
    let model_name = resolve_model_name(&handler.state, in_dev_fix);
    log_agent_invocation_start(ctx, &handler.state, &effective_agent, model_name);
    let (logfile, attempt) =
        prepare_agent_logfile(ctx, in_dev_fix, &handler.state, role, &effective_agent)?;
    let session = create_and_store_agent_session(ctx, drain, attempt, &handler.state);
    persist_session_handshake_to_workspace(ctx, &session);
    build_and_merge_audit_trail(ctx, &session);

    let effective_prompt = resolve_effective_prompt_for_invocation(
        &handler.state,
        drain,
        role,
        &session,
        prompt_generator,
    );

    let (session_bridge, mcp_endpoint) = start_mcp_bridge_for_session(ctx, &session)?;
    let lease = session_bridge.endpoint_lease();
    persist_endpoint_lease_if_available(ctx, &session, lease.as_ref());

    Ok(PreparedAgentInvocation {
        in_dev_fix,
        model_name: model_name.cloned(),
        effective_agent,
        logfile,
        attempt,
        session,
        effective_prompt,
        session_id: resolve_session_id(&handler.state, in_dev_fix).map(str::to_string),
        execution_start: SystemTime::now(),
        session_bridge,
        mcp_endpoint,
        lease,
        completion_output_path: completion_path_for_drain(drain),
        model: model.map(str::to_string),
        drain,
        role,
    })
}
