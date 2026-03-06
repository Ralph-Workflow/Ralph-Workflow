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
        /// Helper function to detect agent unavailability from error messages.
        /// Checks for quota/usage/rate limit indicators in error text.
        fn is_agent_unavailable_error(err_msg: &str) -> bool {
            let err_msg_lower = err_msg.to_lowercase();
            err_msg_lower.contains("usage limit")
                || err_msg_lower.contains("quota exceeded")
                || err_msg_lower.contains("rate limit")
                || err_msg_lower.contains("limit exceeded")
                || err_msg_lower.contains("workspace write failed")
        }

        ctx.logger.error("WARNING: PIPELINE FAILURE DETECTED");
        ctx.logger.warn(&format!(
            "Pipeline failure detected (phase: {failed_phase}, role: {failed_role:?}, cycle: {retry_cycle})"
        ));
        ctx.logger.info("Entering AwaitingDevFix flow...");
        ctx.logger
            .info("Dispatching dev-fix agent for remediation...");

        // Helper to read workspace files with fallback on error
        let read_or_fallback = |path: &str, label: &str| -> String {
            match ctx.workspace.read(std::path::Path::new(path)) {
                Ok(content) => content,
                Err(err) => {
                    ctx.logger.warn(&format!(
                        "Dev-fix prompt fallback: failed to read {label}: {err}"
                    ));
                    format!("(Missing {label}: {err})")
                }
            }
        };

        let prompt_content = read_or_fallback("PROMPT.md", "PROMPT.md");
        let plan_content = read_or_fallback(".agent/PLAN.md", ".agent/PLAN.md");
        let issues_content = format!(
            "# Issues\n\n- [High] Pipeline failure (phase: {failed_phase}, role: {failed_role:?}, cycle: {retry_cycle}).\n  Diagnose the root cause and fix the failure.\n"
        );
        let dev_fix_prompt = crate::prompts::prompt_fix_with_context(
            ctx.template_context,
            &prompt_content,
            &plan_content,
            &issues_content,
            ctx.workspace,
        );

        if let Err(err) = ctx.workspace.write(
            std::path::Path::new(".agent/tmp/dev_fix_prompt.txt"),
            &dev_fix_prompt,
        ) {
            ctx.logger.warn(&format!(
                "Failed to write dev-fix prompt to workspace: {err}"
            ));
        }

        // Dev-fix remediation must run under the configured developer agent.
        // Do not reuse the current agent chain selection here: the failure may have
        // occurred under a different role (Commit/Reviewer/Analysis).
        let agent = ctx.developer_agent.to_string();

        let agent_result = match self.invoke_agent(
            ctx,
            crate::agents::AgentRole::Developer,
            &agent,
            None,
            dev_fix_prompt,
        ) {
            Ok(result) => Ok(result),
            Err(err) => {
                let unavailable = is_agent_unavailable_error(&err.to_string());

                if unavailable {
                    ctx.logger.warn(&format!(
                        "Dev-fix agent unavailable: {err}. Continuing unattended recovery loop without dev-fix agent."
                    ));
                } else {
                    ctx.logger
                        .warn(&format!("Dev-fix agent invocation failed: {err}"));
                }
                Err(err)
            }
        };

        let is_agent_unavailable = agent_result
            .as_ref()
            .err()
            .is_some_and(|err| is_agent_unavailable_error(&err.to_string()));

        // Dev-fix "success" cannot be determined at invocation time.
        //
        // The agent invocation result only tells us whether the dev-fix agent ran without a
        // tool/transport error (e.g., spawn failure, quota unavailable). It does NOT guarantee
        // the underlying pipeline failure is fixed.

        // Extract error reason for logging and summary
        let error_reason = agent_result
            .as_ref()
            .err()
            .map(std::string::ToString::to_string);

        // In unattended mode, we need a concrete reducer-visible signal that a dev-fix
        // attempt completed so the recovery loop can advance attempt counters and
        // derive recovery effects. "Success" here means the dev-fix agent invocation
        // completed without error (not that the underlying failure is fixed).
        let dev_fix_completed = crate::reducer::event::AwaitingDevFixEvent::DevFixCompleted {
            success: agent_result.is_ok() && !is_agent_unavailable,
            summary: if agent_result.is_ok() {
                Some("Dev-fix agent invocation completed".to_string())
            } else {
                error_reason.clone()
            },
        };

        let mut result = agent_result.as_ref().map_or_else(
            |_| {
                EffectResult::event(PipelineEvent::AwaitingDevFix(
                    crate::reducer::event::AwaitingDevFixEvent::DevFixTriggered {
                        failed_phase,
                        failed_role,
                    },
                ))
            },
            |result| {
                EffectResult::with_ui(
                    PipelineEvent::AwaitingDevFix(
                        crate::reducer::event::AwaitingDevFixEvent::DevFixTriggered {
                            failed_phase,
                            failed_role,
                        },
                    ),
                    result.ui_events.clone(),
                )
            },
        );

        // Add any additional events from the agent result if it succeeded
        if let Ok(ref result_events) = agent_result {
            result = result.with_additional_event(result_events.event.clone());
            for event in &result_events.additional_events {
                result = result.with_additional_event(event.clone());
            }
        }

        // Emit an additional event when the dev-fix agent is unavailable.
        if is_agent_unavailable {
            // Agent unavailable (quota/usage limit)
            result = result.with_additional_event(PipelineEvent::AwaitingDevFix(
                crate::reducer::event::AwaitingDevFixEvent::DevFixAgentUnavailable {
                    failed_phase,
                    reason: error_reason.unwrap_or_else(|| "unknown".to_string()),
                },
            ));
        }
        // Note: DevFixCompleted IS emitted here to advance the reducer-visible recovery loop.
        // It represents completion of the dev-fix agent invocation, not a guarantee that the
        // pipeline will succeed on retry.

        // CompletionMarkerEmitted is NOT emitted here. Internal pipeline failures
        // must continue through the unattended recovery loop; completion markers are
        // reserved for explicit external/catastrophic termination via
        // Effect::EmitCompletionMarkerAndTerminate.

        result.with_additional_event(PipelineEvent::AwaitingDevFix(dev_fix_completed))
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
        // Write completion marker to .agent/tmp/completion_marker
        let content = if is_failure {
            format!(
                "failure\n{}",
                reason.unwrap_or_else(|| "unknown".to_string())
            )
        } else {
            "success\n".to_string()
        };

        match Self::write_completion_marker(ctx, &content, is_failure) {
            Ok(()) => {
                // Emit event to transition to Interrupted
                EffectResult::event(PipelineEvent::AwaitingDevFix(
                    crate::reducer::event::AwaitingDevFixEvent::CompletionMarkerEmitted {
                        is_failure,
                    },
                ))
            }
            Err(error) => {
                // Do NOT transition to Interrupted if the marker was not written.
                // External orchestration relies on the marker for termination semantics.
                EffectResult::event(PipelineEvent::AwaitingDevFix(
                    crate::reducer::event::AwaitingDevFixEvent::CompletionMarkerWriteFailed {
                        is_failure,
                        error,
                    },
                ))
            }
        }
    }
}
