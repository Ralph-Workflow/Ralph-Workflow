//! Lifecycle event handlers for pipeline completion and dev-fix flows.
//!
//! This module implements handlers for pipeline lifecycle events including:
//! - Dev-fix flow triggering when pipeline failures occur
//! - Completion marker emission for pipeline termination
//!
//! # Dev-Fix Flow
//!
//! When the pipeline detects a failure (agent exhaustion, validation failures),
//! it triggers the dev-fix flow to attempt automated remediation:
//!
//! 1. Prepare dev-fix prompt with failure context
//! 2. Invoke dev-fix agent
//! 3. Emit events so the reducer can advance the recovery loop
//!
//! # Completion Markers
//!
//! Completion markers are written to `.agent/tmp/completion_marker` to signal
//! pipeline termination state (success/failure) to external orchestrators.
//! They are emitted only when the pipeline is actually terminating via
//! `Effect::EmitCompletionMarkerAndTerminate`.

use super::MainEffectHandler;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{PipelineEvent, PipelinePhase};

impl MainEffectHandler {
    /// Trigger dev-fix flow for pipeline failure remediation.
    ///
    /// This handler executes when the pipeline encounters an unrecoverable failure
    /// (agent chain exhaustion, quota limits, etc.). It attempts automated remediation
    /// by invoking a dev-fix agent with failure context.
    ///
    /// # Process
    ///
    /// 1. Load prompt/plan context from workspace
    /// 2. Generate dev-fix prompt with failure diagnostics
    /// 3. Invoke dev-fix agent
    /// 4. Emit events based on agent availability and outcome
    ///
    /// # Events Emitted
    ///
    /// - `DevFixTriggered`: Dev-fix flow initiated
    /// - `DevFixAgentUnavailable`: Agent quota/rate limit exceeded (if applicable)
    /// - `DevFixCompleted`: Attempt completed so recovery loop can advance
    /// - Additional agent events from invocation
    ///
    /// # Arguments
    ///
    /// * `ctx` - Phase context with workspace and logging
    /// * `failed_phase` - Phase where failure occurred
    /// * `failed_role` - Agent role that failed
    /// * `retry_cycle` - Retry cycle count at failure
    pub(super) fn trigger_dev_fix_flow(
        &self,
        ctx: &mut PhaseContext<'_>,
        failed_phase: PipelinePhase,
        failed_role: crate::agents::AgentRole,
        retry_cycle: u32,
    ) -> EffectResult {
        ctx.logger.warn(&format!(
            "Pipeline failure detected (phase: {failed_phase}, role: {failed_role:?}, cycle: {retry_cycle})"
        ));
        ctx.logger.info("Entering AwaitingDevFix flow...");
        ctx.logger
            .info("Dispatching dev-fix agent for remediation...");

        let dev_fix_prompt = build_dev_fix_prompt(ctx, failed_phase, failed_role, retry_cycle);
        let agent = ctx.developer_agent.to_string();

        let agent_result = invoke_dev_fix_agent(self, ctx, &agent, dev_fix_prompt);
        assemble_dev_fix_result(agent_result, failed_phase, failed_role)
    }

    /// Emit completion marker and terminate pipeline.
    ///
    /// This handler writes a completion marker to signal pipeline termination
    /// state (success/failure) to external orchestrators or monitoring systems.
    ///
    /// # Completion Marker Format
    ///
    /// Success: `success\n`
    /// Failure: `failure\n<reason>`
    ///
    /// # Arguments
    ///
    /// * `ctx` - Phase context with workspace access
    /// * `is_failure` - Whether this is a failure termination
    /// * `reason` - Optional failure reason (ignored for success)
    pub(super) fn emit_completion_marker_and_terminate(
        ctx: &PhaseContext<'_>,
        is_failure: bool,
        reason: Option<String>,
    ) -> EffectResult {
        let content = completion_marker_content(is_failure, reason);
        completion_marker_result(ctx, &content, is_failure)
    }
}

fn is_agent_unavailable_error(err_msg: &str) -> bool {
    let lower = err_msg.to_lowercase();
    lower.contains("usage limit")
        || lower.contains("quota exceeded")
        || lower.contains("rate limit")
        || lower.contains("limit exceeded")
        || lower.contains("workspace write failed")
}

fn read_workspace_or_fallback(ctx: &PhaseContext<'_>, path: &str, label: &str) -> String {
    match ctx.workspace.read(std::path::Path::new(path)) {
        Ok(content) => content,
        Err(err) => {
            ctx.logger.warn(&format!(
                "Dev-fix prompt fallback: failed to read {label}: {err}"
            ));
            format!("(Missing {label}: {err})")
        }
    }
}

fn build_dev_fix_prompt(
    ctx: &mut PhaseContext<'_>,
    failed_phase: PipelinePhase,
    failed_role: crate::agents::AgentRole,
    retry_cycle: u32,
) -> String {
    let prompt_content = read_workspace_or_fallback(ctx, "PROMPT.md", "PROMPT.md");
    let plan_content = read_workspace_or_fallback(ctx, ".agent/PLAN.md", ".agent/PLAN.md");
    let issues_content = format!(
        "# Issues\n\n- [High] Pipeline failure (phase: {failed_phase}, role: {failed_role:?}, cycle: {retry_cycle}).\n  Diagnose the root cause and fix the failure.\n"
    );
    let prompt = crate::prompts::prompt_fix_with_context(
        ctx.template_context,
        &prompt_content,
        &plan_content,
        &issues_content,
        ctx.workspace,
    );
    if let Err(err) = ctx.workspace.write(
        std::path::Path::new(".agent/tmp/dev_fix_prompt.txt"),
        &prompt,
    ) {
        ctx.logger.warn(&format!(
            "Failed to write dev-fix prompt to workspace: {err}"
        ));
    }
    prompt
}

fn invoke_dev_fix_agent(
    handler: &MainEffectHandler,
    ctx: &mut PhaseContext<'_>,
    agent: &str,
    dev_fix_prompt: String,
) -> anyhow::Result<EffectResult> {
    // RFC-009: The closure receives the AgentSession created by invoke_agent.
    // In V1, session capabilities == drain defaults, so the pre-generated prompt
    // is correct. The closure still calls capability_template_variables_from_session
    // to verify the V1 invariant holds and to exercise the RFC-009 session-aware path.
    handler.invoke_agent(
        ctx,
        crate::agents::AgentDrain::Development,
        crate::agents::AgentRole::Developer,
        agent,
        None,
        |session: &crate::agents::session::AgentSession| {
            let _session_vars =
                crate::prompts::capability_template_variables_from_session(session);
            dev_fix_prompt.clone()
        },
    ).map_err(|err| {
        let unavailable = is_agent_unavailable_error(&err.to_string());
        if unavailable {
            ctx.logger.warn(&format!(
                "Dev-fix agent unavailable: {err}. Continuing unattended recovery loop without dev-fix agent."
            ));
        } else {
            ctx.logger.warn(&format!("Dev-fix agent invocation failed: {err}"));
        }
        err
    })
}

fn assemble_dev_fix_result(
    agent_result: anyhow::Result<EffectResult>,
    failed_phase: PipelinePhase,
    failed_role: crate::agents::AgentRole,
) -> EffectResult {
    let is_agent_unavailable = agent_result
        .as_ref()
        .err()
        .is_some_and(|err| is_agent_unavailable_error(&err.to_string()));
    let error_reason = agent_result
        .as_ref()
        .err()
        .map(std::string::ToString::to_string);
    let dev_fix_completed =
        build_dev_fix_completed(&agent_result, is_agent_unavailable, &error_reason);
    let triggered_event = build_dev_fix_triggered_event(failed_phase, failed_role);
    let result = build_initial_dev_fix_result(&agent_result, triggered_event);
    let result = fold_agent_events_into_result(result, &agent_result);
    let result =
        maybe_add_unavailable_event(result, is_agent_unavailable, failed_phase, error_reason);
    result.with_additional_event(PipelineEvent::AwaitingDevFix(dev_fix_completed))
}

fn build_dev_fix_completed(
    agent_result: &anyhow::Result<EffectResult>,
    is_agent_unavailable: bool,
    error_reason: &Option<String>,
) -> crate::reducer::event::AwaitingDevFixEvent {
    crate::reducer::event::AwaitingDevFixEvent::DevFixCompleted {
        success: agent_result.is_ok() && !is_agent_unavailable,
        summary: if agent_result.is_ok() {
            Some("Dev-fix agent invocation completed".to_string())
        } else {
            error_reason.clone()
        },
    }
}

fn build_dev_fix_triggered_event(
    failed_phase: PipelinePhase,
    failed_role: crate::agents::AgentRole,
) -> PipelineEvent {
    PipelineEvent::AwaitingDevFix(
        crate::reducer::event::AwaitingDevFixEvent::DevFixTriggered {
            failed_phase,
            failed_role,
        },
    )
}

fn build_initial_dev_fix_result(
    agent_result: &anyhow::Result<EffectResult>,
    triggered_event: PipelineEvent,
) -> EffectResult {
    match agent_result.as_ref() {
        Ok(r) => EffectResult::with_ui(triggered_event, r.ui_events.clone()),
        Err(_) => EffectResult::event(triggered_event),
    }
}

fn fold_agent_events_into_result(
    result: EffectResult,
    agent_result: &anyhow::Result<EffectResult>,
) -> EffectResult {
    if let Ok(ref r) = agent_result {
        r.additional_events
            .iter()
            .fold(result.with_additional_event(r.event.clone()), |acc, ev| {
                acc.with_additional_event(ev.clone())
            })
    } else {
        result
    }
}

fn maybe_add_unavailable_event(
    result: EffectResult,
    is_agent_unavailable: bool,
    failed_phase: PipelinePhase,
    error_reason: Option<String>,
) -> EffectResult {
    if is_agent_unavailable {
        result.with_additional_event(PipelineEvent::AwaitingDevFix(
            crate::reducer::event::AwaitingDevFixEvent::DevFixAgentUnavailable {
                failed_phase,
                reason: error_reason.unwrap_or_else(|| "unknown".to_string()),
            },
        ))
    } else {
        result
    }
}

fn completion_marker_content(is_failure: bool, reason: Option<String>) -> String {
    if is_failure {
        format!(
            "failure\n{}",
            reason.unwrap_or_else(|| "unknown".to_string())
        )
    } else {
        "success\n".to_string()
    }
}

fn completion_marker_result(
    ctx: &PhaseContext<'_>,
    content: &str,
    is_failure: bool,
) -> EffectResult {
    use crate::reducer::boundary::MainEffectHandler;
    match MainEffectHandler::write_completion_marker(ctx, content, is_failure) {
        Ok(()) => EffectResult::event(PipelineEvent::AwaitingDevFix(
            crate::reducer::event::AwaitingDevFixEvent::CompletionMarkerEmitted { is_failure },
        )),
        Err(error) => EffectResult::event(PipelineEvent::AwaitingDevFix(
            crate::reducer::event::AwaitingDevFixEvent::CompletionMarkerWriteFailed {
                is_failure,
                error,
            },
        )),
    }
}
