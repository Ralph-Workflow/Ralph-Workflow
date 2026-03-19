//! Agent invocation handler - boundary module.
//!
//! This module contains the impure agent execution code that requires
//! mutable state and I/O operations. Dylint rules are exempt here.

use crate::agents::{AgentDrain, AgentRole};
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

pub(super) fn invoke_agent_impl(
    ctx: &mut PhaseContext<'_>,
    drain: AgentDrain,
    role: AgentRole,
    agent: &str,
    model: Option<&str>,
    prompt: String,
    in_dev_fix: bool,
    current_agent: Option<&str>,
    current_model_index: usize,
    phase: PipelinePhase,
    agent_chain_agents: &[String],
    agent_chain_index: usize,
) -> Result<EffectResult> {
    let effective_agent = if in_dev_fix {
        agent.to_owned()
    } else {
        current_agent.map_or_else(|| agent.to_owned(), Clone::clone)
    };

    let cmd_str = format!("agent_command_{}", effective_agent);

    // Build pipeline runtime - THIS IS THE BOUNDARY CODE
    // Mutation is required for executing agents
    let mut runtime = PipelineRuntime {
        timer: ctx.timer,
        logger: ctx.logger,
        colors: ctx.colors,
        config: ctx.config,
        executor: ctx.executor,
        executor_arc: std::sync::Arc::clone(&ctx.executor_arc),
        workspace: ctx.workspace,
        workspace_arc: std::sync::Arc::clone(&ctx.workspace_arc),
    };

    let started_event = PipelineEvent::agent_invocation_started(
        role,
        effective_agent.clone(),
        model.map(str::to_owned),
    );

    let model_index = if in_dev_fix { 0 } else { current_model_index };

    let config = AgentExecutionConfig {
        role,
        agent_name: &effective_agent,
        cmd_str: &cmd_str,
        parser_type: crate::agents::AgentJsonParser::default(),
        env_vars: &[],
        prompt: &effective_prompt,
        display_name: &effective_agent,
        log_prefix: "agent",
        model_index,
        attempt: 1,
        logfile: "/tmp/agent.log",
    };

    let AgentExecutionResult { event, session_id } =
        execute_agent_fault_tolerantly(config, &mut runtime)?;

    let chain_position = if agent_chain_agents.len() > 1 {
        let pos = agent_chain_index + 1;
        let total = agent_chain_agents.len();
        let kind = if pos == 1 { "primary" } else { "fallback" };
        format!(" ({pos}/{total}, {kind})")
    } else {
        String::new()
    };

    let outcome_message = match &event {
        PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. }) => {
            format!("Completed {role} task successfully{chain_position}")
        }
        PipelineEvent::Agent(AgentEvent::RateLimited { .. }) => {
            format!("Agent {effective_agent}{chain_position} rate-limited")
        }
        PipelineEvent::Agent(AgentEvent::AuthFailed { .. }) => {
            format!("Agent {effective_agent}{chain_position} auth failed")
        }
        PipelineEvent::Agent(AgentEvent::TimedOut { .. }) => {
            format!("Agent {effective_agent}{chain_position} timed out")
        }
        PipelineEvent::Agent(AgentEvent::InvocationFailed { error_kind, .. }) => {
            format!("Agent {effective_agent}{chain_position} failed: {error_kind:?}")
        }
        _ => {
            format!("Agent {effective_agent}{chain_position} completed")
        }
    };

    let ui_event = UIEvent::AgentActivity {
        agent: effective_agent.clone(),
        message: outcome_message,
    };

    let events: Vec<_> = std::iter::once(event)
        .chain(session_id.into_iter().flat_map(|sid| {
            std::iter::once(PipelineEvent::agent_session_established(
                role,
                effective_agent.clone(),
                sid,
            ))
        }))
        .collect();

    Ok(EffectResult::events(started_event, ui_event, events))
}
