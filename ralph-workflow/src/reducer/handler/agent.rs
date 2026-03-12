use super::MainEffectHandler;
use crate::agents::{AgentDrain, AgentRole};
use crate::phases::PhaseContext;
use crate::pipeline::PipelineRuntime;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::PipelinePhase;
use crate::reducer::event::WorkspaceIoErrorKind;
use crate::reducer::fault_tolerant_executor::{
    execute_agent_fault_tolerantly, AgentExecutionConfig, AgentExecutionResult,
};
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;

impl MainEffectHandler {
    pub(super) fn invoke_agent(
        &self,
        ctx: &mut PhaseContext<'_>,
        role: AgentRole,
        agent: &str,
        model: Option<&str>,
        prompt: String,
    ) -> Result<EffectResult> {
        let in_dev_fix = self.state.phase == PipelinePhase::AwaitingDevFix;

        // For most phases, the reducer-driven agent chain selects the effective agent.
        // During AwaitingDevFix, remediation must always run under the configured
        // developer agent (not whatever agent happened to fail).
        let effective_agent = if in_dev_fix {
            agent.to_owned()
        } else {
            self.state
                .agent_chain
                .current_agent()
                .map_or_else(|| agent.to_owned(), Clone::clone)
        };

        // Use continuation prompt if available (from rate-limited predecessor).
        //
        // When an agent hits a rate limit, the prompt is saved in
        // rate_limit_continuation_prompt. On the next invocation (with a new
        // agent), we use this saved prompt to continue the same work.
        // The reducer clears the saved prompt only after an invocation succeeds
        // (`InvocationSucceeded`) or when an auth failure forces a clean switch.
        //
        // Important: Some follow-up attempts must override the continuation prompt.
        // For example:
        // - Same-agent retries prepend timeout/internal-error guidance
        // - XSD retries rebuild the prompt with validation error context
        //
        // In those cases, ignoring the newly prepared prompt would silently drop the
        // retry-specific guidance and can lead to repeated failures.
        //
        let effective_prompt = if in_dev_fix {
            prompt
        } else {
            match &self.state.agent_chain.rate_limit_continuation_prompt {
                Some(saved)
                    if saved.role == role
                        && role != AgentRole::Analysis
                        && !self.state.continuation.xsd_retry_session_reuse_pending
                        && !super::retry_guidance::is_same_agent_retry_prompt(&prompt) =>
                {
                    saved.prompt.clone()
                }
                _ => prompt,
            }
        };

        let model_name = if in_dev_fix {
            None
        } else {
            self.state.agent_chain.current_model()
        };

        ctx.logger.info(&format!(
            "Executing with agent: {effective_agent}, model: {model_name:?}"
        ));

        // Get agent configuration from registry
        let agent_config = ctx
            .registry
            .resolve_config(&effective_agent)
            .ok_or_else(|| ErrorEvent::AgentNotFound {
                agent: effective_agent.clone(),
            })?;

        // Determine log file path using per-run log directory.
        //
        // Logs are simplified to phase_index[_aN] format since they're already
        // isolated in per-run directories. Agent identity is recorded in log headers.
        // Logs must uniquely identify the invocation attempt to avoid collisions across:
        // - model fallback (model index)
        // - agent fallback cycles (retry_cycle)
        // - XSD retries and continuation attempts
        let (phase_name, phase_index) = match self.state.phase {
            PipelinePhase::Planning => ("planning", self.state.iteration + 1),
            PipelinePhase::Development => {
                if role == AgentRole::Analysis {
                    ("analysis", self.state.iteration + 1)
                } else {
                    ("developer", self.state.iteration + 1)
                }
            }
            PipelinePhase::Review => ("reviewer", self.state.reviewer_pass + 1),
            PipelinePhase::CommitMessage => {
                let commit_attempt = match &self.state.commit {
                    crate::reducer::state::CommitState::Generating { attempt, .. } => *attempt,
                    _ => 1,
                };
                ("commit", commit_attempt)
            }
            PipelinePhase::FinalValidation => ("final_validation", 1),
            PipelinePhase::Finalizing => ("finalizing", 1),
            PipelinePhase::Complete => ("complete", 1),
            PipelinePhase::AwaitingDevFix => ("awaiting_dev_fix", 1),
            PipelinePhase::Interrupted => ("interrupted", 1),
        };

        // Get base log path from run_log_context (without attempt suffix)
        let base_log_path = ctx.run_log_context.agent_log(phase_name, phase_index, None);

        // Determine a collision-free logfile attempt index.
        //
        // Rationale: The reducer tracks multiple retry counters (retry_cycle,
        // continuation_attempt, xsd_retry_count). Packing them into a single
        // arithmetic attempt value can collide when counters exceed assumed
        // bounds, causing later attempts to overwrite earlier logs.
        //
        // We avoid collisions by scanning existing logs in the per-run agents/
        // directory for this `{phase}_{index}` family and using the next
        // available attempt index.
        let attempt = crate::pipeline::logfile::next_simplified_logfile_attempt_index(
            &base_log_path,
            ctx.workspace,
        );

        let logfile = if attempt == 0 {
            // First attempt: no suffix
            base_log_path.to_string_lossy().to_string()
        } else {
            // Subsequent attempts: add _aN suffix
            ctx.run_log_context
                .agent_log(phase_name, phase_index, Some(attempt))
                .to_string_lossy()
                .to_string()
        };

        // Write log file header with agent metadata
        // Use append_bytes to avoid overwriting if file exists (defense-in-depth)
        let is_resume = ctx.run_context.parent_run_id.is_some();
        let resume_indicator = if is_resume {
            format!(
                "# Resume: true (Original Run ID: {})\n",
                ctx.run_context
                    .parent_run_id
                    .as_deref()
                    .unwrap_or("(unknown)")
            )
        } else {
            "# Resume: false\n".to_string()
        };
        let header_model_index = if in_dev_fix {
            0
        } else {
            self.state.agent_chain.current_model_index
        };

        let log_header = format!(
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
            header_model_index,
            attempt,
            self.state.phase,
            chrono::Utc::now().to_rfc3339(),
            resume_indicator
        );
        ctx.workspace
            .append_bytes(std::path::Path::new(&logfile), log_header.as_bytes())
            .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                path: logfile.clone(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        // Build command string, honoring reducer-selected model (if any).
        // The reducer's agent chain drives model fallback (advance_to_next_model).
        // When present, the selected model must be threaded into the command.
        let model_override = model_name.map(std::string::String::as_str).or(model);

        // Session ID reuse for XSD retry: preserve a "reuse session id" signal across
        // prompt preparation (which clears xsd_retry_pending to avoid effect loops).
        let session_id = if in_dev_fix {
            None
        } else if self.state.continuation.xsd_retry_session_reuse_pending {
            self.state.agent_chain.last_session_id.as_deref()
        } else {
            None
        };

        let cmd_str =
            agent_config.build_cmd_with_session(true, true, true, model_override, session_id);

        // Build pipeline runtime
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
            model_name.cloned().or_else(|| model.map(str::to_owned)),
        );

        let model_index = if in_dev_fix {
            0
        } else {
            self.state.agent_chain.current_model_index
        };

        // Execute agent with fault-tolerant wrapper
        let config = AgentExecutionConfig {
            role,
            agent_name: &effective_agent,
            cmd_str: &cmd_str,
            parser_type: agent_config.json_parser,
            env_vars: &agent_config.env_vars,
            prompt: &effective_prompt,
            display_name: &effective_agent,
            log_prefix: &format!("{phase_name}_{phase_index}"), // For attribution only
            model_index,
            attempt,
            logfile: &logfile,
        };

        let AgentExecutionResult { event, session_id } =
            execute_agent_fault_tolerantly(config, &mut runtime)?;

        // Emit UI event for agent activity
        let ui_event = UIEvent::AgentActivity {
            agent: effective_agent.clone(),
            message: format!("Completed {role} task"),
        };

        // Build result with started event first, then the execution result(s).
        let mut result =
            EffectResult::with_ui(started_event, vec![ui_event]).with_additional_event(event);

        // If session_id was extracted, emit SessionEstablished as a separate event.
        if let Some(sid) = session_id {
            result = result.with_additional_event(PipelineEvent::agent_session_established(
                role,
                effective_agent.clone(),
                sid,
            ));
        }

        Ok(result)
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

        // Defensively validate agent chain index bounds for consistency.
        // These should never be out of bounds in normal operation, but if they are
        // (e.g., due to manual checkpoint edits or bugs), we clamp them to valid
        // ranges to prevent panics and ensure deterministic behavior.
        if self.state.agent_chain.agents.is_empty() {
            // No agents configured - reset indices to safe defaults
            self.state.agent_chain.current_agent_index = 0;
            self.state.agent_chain.current_model_index = 0;
        } else {
            // Clamp agent index to valid range
            if self.state.agent_chain.current_agent_index >= self.state.agent_chain.agents.len() {
                self.state.agent_chain.current_agent_index = 0;
                self.state.agent_chain.current_model_index = 0;
            }

            // Clamp model index to valid range for the current agent
            if let Some(models) = self
                .state
                .agent_chain
                .models_per_agent
                .get(self.state.agent_chain.current_agent_index)
            {
                if !models.is_empty() && self.state.agent_chain.current_model_index >= models.len()
                {
                    self.state.agent_chain.current_model_index = 0;
                }
            } else {
                self.state.agent_chain.current_model_index = 0;
            }
        }

        // Ensure rate_limit_continuation_prompt role matches current role.
        // If they don't match, clear the continuation prompt to prevent cross-task
        // contamination (e.g., a developer continuation prompt overriding an analysis prompt).
        if let Some(ref continuation) = self.state.agent_chain.rate_limit_continuation_prompt {
            if continuation.role != expected_role {
                self.state.agent_chain.rate_limit_continuation_prompt = None;
            }
        }

        // Normalize session ID policy based on retry mode:
        // - XSD retry: preserve session (session ID already set if available)
        // - TimeoutWithContext: preserve session to continue with prior context
        // - Same-agent retry (other): clear session to start fresh
        // - Normal: session policy already set by reducer
        //
        // Note: We don't modify last_session_id for XSD retry or TimeoutWithContext
        // because they should already be set from the previous attempt.
        // We only clear it for other same-agent retries to force a fresh conversation.
        let is_timeout_with_context = self
            .state
            .continuation
            .same_agent_retry_reason
            .is_some_and(|r| r == super::super::state::SameAgentRetryReason::TimeoutWithContext);

        if self.state.continuation.same_agent_retry_pending && !is_timeout_with_context {
            // Same-agent retry (non-timeout-with-context): clear last session id to start fresh
            self.state.agent_chain.last_session_id = None;
        }
    }
}
