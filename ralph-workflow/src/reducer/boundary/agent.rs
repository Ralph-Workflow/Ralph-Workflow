use super::MainEffectHandler;
use crate::agents::config::should_use_yolo_mode;
use crate::agents::harness::applicator::{apply_harness_config, detect_agent_type};
use crate::agents::session::{
    audit::{
        persist_audit_trail, persist_session_handshake, record_command_check,
        record_execution_telemetry,
    },
    command_policy::{check_command, parse_command},
    AgentSession, AuditRecord, AuditTrail, Capability, PolicyOutcome, SessionDrain,
    SessionHandshake,
};
use crate::agents::{AgentDrain, AgentRole};
use crate::common::domain_types::{AgentName, ModelName};
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::mcp_server::session_bridge::{SessionBridge, MCP_ENDPOINT_ENV};
use crate::phases::PhaseContext;
use crate::pipeline::PipelineRuntime;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::AgentEvent;
use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::PipelinePhase;
use crate::reducer::event::WorkspaceIoErrorKind;
use crate::reducer::fault_tolerant_executor::{
    execute_agent_fault_tolerantly, AgentExecutionConfig, AgentExecutionResult,
};
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;
use std::path::Path;
use std::sync::Arc;
use std::time::SystemTime;

/// Map an AgentDrain to the expected output file path for completion detection.
///
/// Returns `None` if the drain does not produce a structured XML output file.
fn completion_path_for_drain(drain: AgentDrain) -> Option<&'static Path> {
    match drain {
        AgentDrain::Planning => Some(Path::new(xml_paths::PLAN_XML)),
        AgentDrain::Development => Some(Path::new(xml_paths::DEVELOPMENT_RESULT_XML)),
        AgentDrain::Review => Some(Path::new(xml_paths::ISSUES_XML)),
        AgentDrain::Fix => Some(Path::new(xml_paths::FIX_RESULT_XML)),
        AgentDrain::Commit => Some(Path::new(xml_paths::COMMIT_MESSAGE_XML)),
        AgentDrain::Analysis => None, // Analysis does not produce a structured output file
    }
}

impl MainEffectHandler {
    pub(super) fn invoke_agent<F>(
        &self,
        ctx: &mut PhaseContext<'_>,
        drain: AgentDrain,
        role: AgentRole,
        agent: &str,
        model: Option<&str>,
        prompt_generator: F,
    ) -> Result<EffectResult>
    where
        F: FnOnce(&AgentSession) -> String,
    {
        let in_dev_fix = self.state.phase == PipelinePhase::AwaitingDevFix;
        let effective_agent = resolve_effective_agent(&self.state, in_dev_fix, agent);
        let model_name = resolve_model_name(&self.state, in_dev_fix);
        log_agent_invocation_start(ctx, &self.state, &effective_agent, model_name);
        let (logfile, attempt) =
            prepare_agent_logfile(ctx, in_dev_fix, &self.state, role, &effective_agent)?;

        let session = create_and_store_agent_session(ctx, drain, attempt, &self.state);
        persist_session_handshake_to_workspace(ctx, &session);
        build_and_merge_audit_trail(ctx, &session);

        let effective_prompt = resolve_effective_prompt_for_invocation(
            &self.state,
            drain,
            role,
            &session,
            prompt_generator,
        );
        let session_id = resolve_session_id(&self.state, in_dev_fix);
        let execution_start = SystemTime::now();
        let (mut session_bridge, mcp_endpoint) = start_mcp_bridge_for_session(ctx, &session)?;
        let completion_output_path = completion_path_for_drain(drain);
        let event = run_agent_execution(
            ctx,
            &self.state,
            drain,
            AgentRunInputs {
                in_dev_fix,
                role,
                model,
                model_name,
                effective_agent: &effective_agent,
                effective_prompt: &effective_prompt,
                attempt,
                logfile: &logfile,
                session: &session,
                mcp_endpoint: mcp_endpoint.as_deref(),
                completion_output_path,
            },
        );

        drain_and_merge_mcp_audit_records(ctx, &mut session_bridge);
        record_execution_telemetry_if_needed(ctx, &session, &event, execution_start, role);
        persist_audit_trail_to_workspace(ctx, &session);

        let event = event?;
        Ok(build_agent_invocation_result(
            &self.state,
            event,
            session_id,
            role,
            &effective_agent,
            model_name,
        ))
    }

    /// Normalize agent chain state before agent invocation for determinism.
    ///
    /// This function ensures that:
    /// 1. Session ID policy is consistent with the current retry mode
    /// 2. Agent and model indices are within valid bounds (defensive programming)
    /// 3. Rate limit continuation prompt role matches the expected role
    ///
    /// This is critical for checkpoint replay safety: the same pre-invocation state
    /// must produce the same agent/session selection.
    pub(super) fn normalize_agent_chain_for_invocation(
        &mut self,
        _ctx: &PhaseContext<'_>,
        expected_drain: AgentDrain,
    ) {
        let expected_role = expected_drain.role();
        normalize_legacy_drain(&mut self.state.agent_chain, expected_drain, expected_role);
        clamp_agent_chain_indices(&mut self.state.agent_chain);
        clear_mismatched_continuation_prompt(
            &mut self.state.agent_chain,
            expected_drain,
            expected_role,
        );
        clear_session_for_same_agent_retry(&mut self.state);
    }
}

include!("agent_audit.rs");

// ---------------------------------------------------------------------------
// Free helpers extracted from invoke_agent (execution path)
// ---------------------------------------------------------------------------

fn resolve_model_name(
    state: &crate::reducer::state::PipelineState,
    in_dev_fix: bool,
) -> Option<&String> {
    if in_dev_fix {
        None
    } else {
        state.agent_chain.current_model()
    }
}

fn prepare_agent_logfile(
    ctx: &mut PhaseContext<'_>,
    in_dev_fix: bool,
    state: &crate::reducer::state::PipelineState,
    role: AgentRole,
    effective_agent: &str,
) -> Result<(String, u32)> {
    let (phase_name, phase_index) = derive_phase_log_info(state, role);
    let (logfile, attempt) = resolve_logfile(ctx, in_dev_fix, state, phase_name, phase_index);
    write_agent_log_header(
        ctx,
        state,
        in_dev_fix,
        role,
        effective_agent,
        attempt,
        &logfile,
    )?;
    Ok((logfile, attempt))
}

struct AgentRunInputs<'a> {
    in_dev_fix: bool,
    role: AgentRole,
    model: Option<&'a str>,
    model_name: Option<&'a String>,
    effective_agent: &'a str,
    effective_prompt: &'a str,
    attempt: u32,
    logfile: &'a str,
    session: &'a AgentSession,
    /// MCP endpoint URI for RFC-009 agent-MCP communication.
    /// When Some, passed to agent via RALPH_MCP_ENDPOINT env var.
    mcp_endpoint: Option<&'a str>,
    completion_output_path: Option<&'a Path>,
}

fn run_agent_execution(
    ctx: &mut PhaseContext<'_>,
    state: &crate::reducer::state::PipelineState,
    _drain: AgentDrain,
    inputs: AgentRunInputs<'_>,
) -> Result<crate::reducer::event::PipelineEvent> {
    let (agent_config, base_cmd) = resolve_agent_config_and_cmd(ctx, state, &inputs)?;
    let (cmd_str, merged_env) = apply_mcp_harness_to_cmd(ctx, &inputs, &agent_config, base_cmd)?;
    if let Some(denied) = check_command_policy(ctx, &cmd_str, &inputs) {
        return Ok(denied);
    }
    let model_index = resolve_model_index(state, inputs.in_dev_fix);
    execute_with_config(
        ctx,
        &inputs,
        &cmd_str,
        &merged_env,
        model_index,
        agent_config.json_parser,
    )
}

fn resolve_agent_config_and_cmd(
    ctx: &PhaseContext<'_>,
    state: &crate::reducer::state::PipelineState,
    inputs: &AgentRunInputs<'_>,
) -> Result<(crate::agents::config::AgentConfig, String)> {
    let agent_config = ctx
        .registry
        .resolve_config(inputs.effective_agent)
        .ok_or_else(|| ErrorEvent::AgentNotFound {
            agent: AgentName::from(inputs.effective_agent.to_owned()),
        })?;
    let model_override = inputs
        .model_name
        .map(std::string::String::as_str)
        .or(inputs.model);
    let session_id = resolve_session_id(state, inputs.in_dev_fix);
    let use_yolo = should_use_yolo_mode(inputs.session);
    let base_cmd =
        agent_config.build_cmd_with_session(true, use_yolo, true, model_override, session_id);
    Ok((agent_config, base_cmd))
}

fn resolve_model_index(state: &crate::reducer::state::PipelineState, in_dev_fix: bool) -> usize {
    if in_dev_fix {
        0
    } else {
        state.agent_chain.current_model_index
    }
}

fn apply_mcp_harness_to_cmd(
    ctx: &mut PhaseContext<'_>,
    inputs: &AgentRunInputs<'_>,
    agent_config: &crate::agents::config::AgentConfig,
    base_cmd: String,
) -> Result<(String, std::collections::HashMap<String, String>)> {
    let mut mcp_env_vars = build_mcp_base_env(inputs.mcp_endpoint);
    let extra_cmd_args = apply_harness_if_needed(ctx, inputs, agent_config, &mut mcp_env_vars)?;
    let cmd_str = append_extra_args(ctx, base_cmd, extra_cmd_args);
    let mut merged_env = agent_config.env_vars.clone();
    merged_env.extend(mcp_env_vars);
    Ok((cmd_str, merged_env))
}

fn apply_harness_if_needed(
    ctx: &mut PhaseContext<'_>,
    inputs: &AgentRunInputs<'_>,
    agent_config: &crate::agents::config::AgentConfig,
    mcp_env_vars: &mut std::collections::HashMap<String, String>,
) -> Result<Vec<String>> {
    let Some(endpoint) = inputs.mcp_endpoint else {
        return Ok(Vec::new());
    };
    let agent_type = detect_agent_type(&agent_config.cmd);
    match apply_harness_config(agent_type, inputs.session, endpoint, ctx.workspace) {
        Ok(harness_result) => {
            ctx.logger.info(&format!(
                "RFC-009 harness applied for {:?}: {} extra env var(s){}",
                agent_type,
                harness_result.extra_env_vars.len(),
                harness_result.config_path.as_ref().map_or_else(String::new, |p| format!(", config={p}")),
            ));
            mcp_env_vars.extend(harness_result.extra_env_vars);
            Ok(harness_result.extra_cmd_args)
        }
        Err(e) => Err(anyhow::anyhow!(
            "MCP harness setup failed for agent {:?} (session {}): {}. MCP is mandatory and execution was aborted.",
            agent_type, inputs.session.session_id, e
        )),
    }
}

fn build_mcp_base_env(mcp_endpoint: Option<&str>) -> std::collections::HashMap<String, String> {
    mcp_endpoint.map_or_else(std::collections::HashMap::new, |endpoint| {
        let mut vars = std::collections::HashMap::new();
        vars.insert(MCP_ENDPOINT_ENV.to_string(), endpoint.to_string());
        vars
    })
}

fn append_extra_args(
    ctx: &mut PhaseContext<'_>,
    base_cmd: String,
    extra_cmd_args: Vec<String>,
) -> String {
    if extra_cmd_args.is_empty() {
        return base_cmd;
    }
    let joined_args = extra_cmd_args.join(" ");
    ctx.logger.info(&format!(
        "RFC-009 harness extra args appended: {joined_args}"
    ));
    format!("{base_cmd} {joined_args}")
}

fn check_command_policy(
    ctx: &mut PhaseContext<'_>,
    cmd_str: &str,
    inputs: &AgentRunInputs<'_>,
) -> Option<crate::reducer::event::PipelineEvent> {
    let cmd_tokens = parse_command(cmd_str);
    let (cmd, args) = cmd_tokens.split_first()?;
    let args_refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    let policy_outcome = check_command(cmd.as_str(), &args_refs);
    let timestamp = current_unix_timestamp();
    ctx.audit_trail = record_command_check(
        &ctx.audit_trail,
        &inputs.session.session_id,
        timestamp,
        cmd_str,
        &policy_outcome,
    );
    if let PolicyOutcome::Denied { ref reason } = policy_outcome {
        ctx.logger.info(&format!(
            "RFC-009 command policy: '{cmd_str}' denied: {reason}"
        ));
        let role = inputs.session.drain.into_role();
        return Some(PipelineEvent::Agent(AgentEvent::CapabilityDenied {
            role,
            capability: "process.exec_bounded".into(),
            reason: reason.clone(),
        }));
    }
    ctx.logger
        .info(&format!("RFC-009 command policy: '{cmd_str}' approved"));
    None
}

fn execute_with_config(
    ctx: &mut PhaseContext<'_>,
    inputs: &AgentRunInputs<'_>,
    cmd_str: &str,
    merged_env: &std::collections::HashMap<String, String>,
    model_index: usize,
    parser_type: crate::agents::JsonParserType,
) -> Result<crate::reducer::event::PipelineEvent> {
    let config = AgentExecutionConfig {
        role: inputs.role,
        agent_name: inputs.effective_agent,
        cmd_str,
        parser_type,
        env_vars: merged_env,
        prompt: inputs.effective_prompt,
        display_name: inputs.effective_agent,
        log_prefix: "agent",
        model_index,
        attempt: inputs.attempt,
        logfile: inputs.logfile,
        completion_output_path: inputs.completion_output_path,
    };
    let AgentExecutionResult {
        event,
        session_id: _,
    } = execute_agent_fault_tolerantly(
        config,
        &mut PipelineRuntime {
            timer: ctx.timer,
            logger: ctx.logger,
            colors: ctx.colors,
            config: ctx.config,
            executor: ctx.executor,
            executor_arc: std::sync::Arc::clone(&ctx.executor_arc),
            workspace: ctx.workspace,
            workspace_arc: std::sync::Arc::clone(&ctx.workspace_arc),
        },
    )?;
    Ok(event)
}

fn build_agent_invocation_result(
    state: &crate::reducer::state::PipelineState,
    event: crate::reducer::event::PipelineEvent,
    session_id: Option<&str>,
    role: AgentRole,
    effective_agent: &str,
    model_name: Option<&String>,
) -> EffectResult {
    let chain_position = format_chain_position_outcome(state);
    let outcome_message =
        build_agent_outcome_message(&event, role, effective_agent, &chain_position);
    let ui_event = UIEvent::AgentActivity {
        agent: effective_agent.to_owned(),
        message: outcome_message,
    };
    let started_agent = AgentName::from(effective_agent.to_owned());
    let started_model = model_name.cloned().map(ModelName::from);
    let started_event =
        PipelineEvent::agent_invocation_started(role, started_agent.clone(), started_model);
    std::iter::once(event)
        .chain(session_id.into_iter().flat_map(|sid| {
            std::iter::once(PipelineEvent::agent_session_established(
                role,
                started_agent.clone(),
                sid.to_string(),
            ))
        }))
        .fold(
            EffectResult::with_ui(started_event, vec![ui_event]),
            |r, ev| r.with_additional_event(ev),
        )
}

// ---------------------------------------------------------------------------
// Free helpers extracted from normalize_agent_chain_for_invocation
// ---------------------------------------------------------------------------

fn normalize_legacy_drain(
    chain: &mut crate::reducer::state::AgentChainState,
    expected_drain: AgentDrain,
    expected_role: AgentRole,
) {
    let legacy_compatible =
        chain.current_drain != expected_drain && chain.current_drain.role() == expected_role;
    if legacy_compatible {
        apply_legacy_drain_migration(chain, expected_drain, expected_role);
    }
}

fn apply_legacy_drain_migration(
    chain: &mut crate::reducer::state::AgentChainState,
    expected_drain: AgentDrain,
    expected_role: AgentRole,
) {
    let previous_drain = chain.current_drain;
    let previous_role = chain.current_role;
    chain.current_drain = expected_drain;
    chain.current_role = expected_role;
    migrate_continuation_prompt_drain(
        chain,
        previous_drain,
        previous_role,
        expected_drain,
        expected_role,
    );
}

fn migrate_continuation_prompt_drain(
    chain: &mut crate::reducer::state::AgentChainState,
    previous_drain: AgentDrain,
    previous_role: AgentRole,
    expected_drain: AgentDrain,
    expected_role: AgentRole,
) {
    let Some(prompt) = chain.rate_limit_continuation_prompt.as_mut() else {
        return;
    };
    if prompt.drain == previous_drain && prompt.role == previous_role {
        prompt.drain = expected_drain;
        prompt.role = expected_role;
    }
}

fn clamp_agent_chain_indices(chain: &mut crate::reducer::state::AgentChainState) {
    if chain.agents.is_empty() {
        chain.current_agent_index = 0;
        chain.current_model_index = 0;
    } else {
        clamp_agent_chain_indices_nonempty(chain);
    }
}

fn clamp_agent_chain_indices_nonempty(chain: &mut crate::reducer::state::AgentChainState) {
    if chain.current_agent_index >= chain.agents.len() {
        chain.current_agent_index = 0;
        chain.current_model_index = 0;
    }
    clamp_model_index(chain);
}

fn clamp_model_index(chain: &mut crate::reducer::state::AgentChainState) {
    if let Some(models) = chain.models_per_agent.get(chain.current_agent_index) {
        if !models.is_empty() && chain.current_model_index >= models.len() {
            chain.current_model_index = 0;
        }
    } else {
        chain.current_model_index = 0;
    }
}

fn clear_mismatched_continuation_prompt(
    chain: &mut crate::reducer::state::AgentChainState,
    expected_drain: AgentDrain,
    expected_role: AgentRole,
) {
    if let Some(ref continuation) = chain.rate_limit_continuation_prompt {
        if continuation.drain != expected_drain || continuation.role != expected_role {
            chain.rate_limit_continuation_prompt = None;
        }
    }
}

fn clear_session_for_same_agent_retry(state: &mut crate::reducer::state::PipelineState) {
    let is_timeout_with_context = state
        .continuation
        .same_agent_retry_reason
        .is_some_and(|r| r == crate::reducer::state::SameAgentRetryReason::TimeoutWithContext);
    if state.continuation.same_agent_retry_pending && !is_timeout_with_context {
        state.agent_chain.last_session_id = None;
    }
}

// ---------------------------------------------------------------------------
// Pure policy helpers for continuation prompt selection
// ---------------------------------------------------------------------------

fn continuation_prompt_exists_and_matches(
    chain: &crate::reducer::state::AgentChainState,
    drain: AgentDrain,
    role: AgentRole,
) -> bool {
    chain
        .rate_limit_continuation_prompt
        .as_ref()
        .is_some_and(|saved| saved.drain == drain && saved.role == role)
}

fn role_allows_continuation_prompt(role: AgentRole) -> bool {
    role != AgentRole::Analysis
}

fn retry_mode_allows_continuation_prompt(state: &crate::reducer::state::PipelineState) -> bool {
    !state.continuation.xsd_retry_session_reuse_pending
}

/// Check state-based conditions for whether continuation prompt should be considered.
///
/// Returns true only if ALL state-based conditions are met:
/// 1. `rate_limit_continuation_prompt` is set in agent chain and matches drain/role
/// 2. The role is not Analysis (analysis agent has its own continuation mechanism)
/// 3. XSD retry is NOT pending (XSD retry has its own continuation mechanism)
///
/// Note: This does NOT check if the generated prompt is a retry prompt.
/// The caller must check that separately by calling is_same_agent_retry_prompt on the generated prompt.
fn should_use_continuation_prompt(
    state: &crate::reducer::state::PipelineState,
    drain: AgentDrain,
    role: AgentRole,
) -> bool {
    continuation_prompt_exists_and_matches(&state.agent_chain, drain, role)
        && role_allows_continuation_prompt(role)
        && retry_mode_allows_continuation_prompt(state)
}

/// Pure domain enum representing the choice of prompt source.
#[derive(Debug, Clone, Copy)]
enum PromptSource {
    /// Use the rate limit continuation prompt
    Continuation,
    /// Use the generated prompt
    Generated,
}

/// Determine which prompt source to use based on state and generated prompt.
///
/// This is the pure policy logic extracted from the invocation flow.
fn determine_prompt_source(
    state: &crate::reducer::state::PipelineState,
    drain: AgentDrain,
    role: AgentRole,
    generated_prompt: &str,
) -> PromptSource {
    if should_use_continuation_prompt(state, drain, role) {
        if super::retry_guidance::is_same_agent_retry_prompt(generated_prompt) {
            PromptSource::Generated
        } else {
            PromptSource::Continuation
        }
    } else {
        PromptSource::Generated
    }
}

/// Resolve the effective prompt for agent invocation.
///
/// This is a thin wiring function that:
/// 1. Generates the prompt using the closure
/// 2. Determines which prompt source to use via pure policy
/// 3. Returns the appropriate prompt
fn resolve_effective_prompt_for_invocation<F>(
    state: &crate::reducer::state::PipelineState,
    drain: AgentDrain,
    role: AgentRole,
    session: &AgentSession,
    prompt_generator: F,
) -> String
where
    F: FnOnce(&AgentSession) -> String,
{
    let generated_prompt = prompt_generator(session);
    let source = determine_prompt_source(state, drain, role, &generated_prompt);
    match source {
        PromptSource::Continuation => state
            .agent_chain
            .rate_limit_continuation_prompt
            .as_ref()
            .map(|p| p.prompt.clone())
            .unwrap_or(generated_prompt),
        PromptSource::Generated => generated_prompt,
    }
}

// ---------------------------------------------------------------------------
// Free helpers extracted from invoke_agent to reduce per-function complexity
// ---------------------------------------------------------------------------

fn resolve_effective_agent(
    state: &crate::reducer::state::PipelineState,
    in_dev_fix: bool,
    agent: &str,
) -> String {
    if in_dev_fix {
        agent.to_owned()
    } else {
        state
            .agent_chain
            .current_agent()
            .map_or_else(|| agent.to_owned(), Clone::clone)
    }
}

fn log_agent_invocation_start(
    ctx: &PhaseContext<'_>,
    state: &crate::reducer::state::PipelineState,
    effective_agent: &str,
    model_name: Option<&String>,
) {
    let chain_position = if state.agent_chain.agents.len() > 1 {
        let pos = state.agent_chain.current_agent_index + 1;
        let total = state.agent_chain.agents.len();
        let kind = if pos == 1 { "primary" } else { "fallback" };
        format!(", {pos}/{total}, {kind}")
    } else {
        String::new()
    };
    let failure_context = state
        .agent_chain
        .last_failure_reason
        .as_ref()
        .map(|reason| format!(" (previous agent {reason})"))
        .unwrap_or_default();
    ctx.logger.info(&format!(
        "Executing with agent: {effective_agent}{chain_position}, model: {model_name:?}{failure_context}"
    ));
}

fn derive_phase_log_info(
    state: &crate::reducer::state::PipelineState,
    role: AgentRole,
) -> (&'static str, u32) {
    match state.phase {
        PipelinePhase::Planning => ("planning", state.iteration + 1),
        PipelinePhase::Development => development_phase_log_name(role, state.iteration),
        PipelinePhase::Review => ("reviewer", state.reviewer_pass + 1),
        PipelinePhase::CommitMessage => ("commit", commit_attempt_index(&state.commit)),
        PipelinePhase::FinalValidation => ("final_validation", 1),
        PipelinePhase::Finalizing => ("finalizing", 1),
        PipelinePhase::Complete => ("complete", 1),
        PipelinePhase::AwaitingDevFix => ("awaiting_dev_fix", 1),
        PipelinePhase::Interrupted => ("interrupted", 1),
    }
}

fn development_phase_log_name(role: AgentRole, iteration: u32) -> (&'static str, u32) {
    if role == AgentRole::Analysis {
        ("analysis", iteration + 1)
    } else {
        ("developer", iteration + 1)
    }
}

fn commit_attempt_index(commit: &crate::reducer::state::CommitState) -> u32 {
    match commit {
        crate::reducer::state::CommitState::Generating { attempt, .. } => *attempt,
        _ => 1,
    }
}

fn resolve_logfile(
    ctx: &PhaseContext<'_>,
    in_dev_fix: bool,
    state: &crate::reducer::state::PipelineState,
    phase_name: &str,
    phase_index: u32,
) -> (String, u32) {
    let _ = in_dev_fix;
    let _ = state;
    let base_log_path = ctx.run_log_context.agent_log(phase_name, phase_index, None);
    let attempt = crate::pipeline::logfile::next_simplified_logfile_attempt_index(
        &base_log_path,
        ctx.workspace,
    );
    let logfile = if attempt == 0 {
        base_log_path.to_string_lossy().to_string()
    } else {
        ctx.run_log_context
            .agent_log(phase_name, phase_index, Some(attempt))
            .to_string_lossy()
            .to_string()
    };
    (logfile, attempt)
}

fn write_agent_log_header(
    ctx: &PhaseContext<'_>,
    state: &crate::reducer::state::PipelineState,
    in_dev_fix: bool,
    role: AgentRole,
    effective_agent: &str,
    attempt: u32,
    logfile: &str,
) -> Result<()> {
    let resume_indicator = build_resume_indicator(ctx);
    let header_model_index = if in_dev_fix {
        0
    } else {
        state.agent_chain.current_model_index
    };
    let log_header = build_log_header(
        role,
        effective_agent,
        header_model_index,
        attempt,
        state,
        &resume_indicator,
    );
    ctx.workspace
        .append_bytes(std::path::Path::new(logfile), log_header.as_bytes())
        .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
            path: logfile.to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        })?;
    Ok(())
}

fn build_resume_indicator(ctx: &PhaseContext<'_>) -> String {
    if ctx.run_context.parent_run_id.is_some() {
        format!(
            "# Resume: true (Original Run ID: {})\n",
            ctx.run_context
                .parent_run_id
                .as_deref()
                .unwrap_or("(unknown)")
        )
    } else {
        "# Resume: false\n".to_string()
    }
}

fn build_log_header(
    role: AgentRole,
    effective_agent: &str,
    model_index: usize,
    attempt: u32,
    state: &crate::reducer::state::PipelineState,
    resume_indicator: &str,
) -> String {
    format!(
        "# Ralph Agent Invocation Log\n\
         # Role: {:?}\n\
         # Agent: {}\n\
         # Model Index: {}\n\
         # Attempt: {}\n\
         # Phase: {:?}\n\
         # Timestamp: {}\n\
         {}\n",
        role,
        effective_agent,
        model_index,
        attempt,
        state.phase,
        chrono::Utc::now().to_rfc3339(),
        resume_indicator
    )
}

fn resolve_session_id(
    state: &crate::reducer::state::PipelineState,
    in_dev_fix: bool,
) -> Option<&str> {
    if in_dev_fix {
        None
    } else if state.continuation.xsd_retry_session_reuse_pending {
        state.agent_chain.last_session_id.as_deref()
    } else {
        None
    }
}

fn format_chain_position_outcome(state: &crate::reducer::state::PipelineState) -> String {
    if state.agent_chain.agents.len() > 1 {
        let pos = state.agent_chain.current_agent_index + 1;
        let total = state.agent_chain.agents.len();
        let kind = if pos == 1 { "primary" } else { "fallback" };
        format!(" ({pos}/{total}, {kind})")
    } else {
        String::new()
    }
}

fn build_agent_outcome_message(
    event: &PipelineEvent,
    role: AgentRole,
    effective_agent: &str,
    chain_position: &str,
) -> String {
    match event {
        PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. }) => {
            format!("Completed {role} task successfully{chain_position}")
        }
        PipelineEvent::Agent(ev) => {
            agent_event_outcome_message(ev, effective_agent, chain_position)
        }
        _ => format!("Agent {effective_agent}{chain_position} completed"),
    }
}

fn agent_event_outcome_message(
    ev: &AgentEvent,
    effective_agent: &str,
    chain_position: &str,
) -> String {
    match ev {
        AgentEvent::RateLimited { .. } => {
            format!("Agent {effective_agent}{chain_position} rate-limited")
        }
        AgentEvent::AuthFailed { .. } => {
            format!("Agent {effective_agent}{chain_position} auth failed")
        }
        AgentEvent::TimedOut { .. } => {
            format!("Agent {effective_agent}{chain_position} timed out")
        }
        AgentEvent::InvocationFailed { error_kind, .. } => {
            format!("Agent {effective_agent}{chain_position} failed: {error_kind:?}")
        }
        _ => format!("Agent {effective_agent}{chain_position} completed"),
    }
}
