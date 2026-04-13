//! Continuation and retry logic reducer
//!
//! Handles events related to:
//! - Continuation flow (`ContinuationTriggered`, `ContinuationSucceeded`, `ContinuationBudgetExhausted`)
//! - XSD retry logic (`OutputValidationFailed`, `XmlMissing`)
//! - Context management (`ContinuationContextWritten`, `ContinuationContextCleaned`)

use crate::agents::DrainMode;
use crate::reducer::event::DevelopmentEvent;
use crate::reducer::state::{ContinuationState, DevelopmentStatus, PipelineState};

use super::reduce_development_event;

pub(super) fn reduce_continuation_event(
    state: PipelineState,
    event: DevelopmentEvent,
) -> PipelineState {
    match event {
        DevelopmentEvent::ContinuationTriggered {
            iteration,
            status,
            summary,
            files_changed,
            next_steps,
        } => {
            // Trigger continuation with context from the previous attempt
            let old_attempt = state.continuation.continuation_attempt;
            let new_continuation =
                state
                    .continuation
                    .trigger_continuation(status, summary, files_changed, next_steps);
            let new_attempt = new_continuation.continuation_attempt;

            // Only increment metrics if the continuation counter actually incremented.
            // The defensive check in trigger_continuation may prevent the increment when
            // at the budget boundary, in which case metrics should also not increment.
            let metrics = if new_attempt > old_attempt {
                state.metrics.increment_dev_continuation_attempt()
            } else {
                state.metrics
            };

            PipelineState {
                iteration,
                agent_chain: state.agent_chain.with_mode(DrainMode::Continuation),
                continuation: new_continuation,
                development_context_prepared_iteration: None,
                development_prompt_prepared_iteration: None,
                development_required_files_cleaned_iteration: None,
                development_agent_invoked_iteration: None,
                // IMPORTANT: analysis must run after EVERY development-agent invocation.
                // Reset this marker so the orchestrator will invoke analysis for the new
                // continuation attempt within the same iteration.
                analysis_agent_invoked_iteration: None,
                development_xml_extracted_iteration: None,
                development_validated_outcome: None,
                development_xml_archived_iteration: None,
                metrics,
                ..state
            }
        }
        DevelopmentEvent::ContinuationSucceeded {
            iteration,
            total_continuation_attempts: _,
        } => {
            // Continuation succeeded after multiple attempts; proceed to Review phase.
            // After development completes (either directly or via continuations),
            // the correct workflow path is Review, not CommitMessage.
            // Reset the agent chain so the reviewer chain is used, not the developer chain.
            let agent_chain = crate::reducer::state::AgentChainState::initial()
                .with_max_cycles(state.agent_chain.max_cycles)
                .with_backoff_policy(
                    state.agent_chain.retry_delay_ms,
                    state.agent_chain.backoff_multiplier,
                    state.agent_chain.max_backoff_ms,
                )
                .reset_for_drain(crate::agents::AgentDrain::Review);
            PipelineState {
                phase: crate::reducer::event::PipelinePhase::Review,
                previous_phase: Some(crate::reducer::event::PipelinePhase::Development),
                iteration,
                // Reset reviewer_pass to 0 so each dev iteration's Review cycle starts fresh.
                reviewer_pass: 0,
                commit: crate::reducer::state::CommitState::NotStarted,
                commit_prompt_prepared: false,
                commit_diff_prepared: false,
                commit_diff_empty: false,
                commit_agent_invoked: false,
                commit_required_files_cleaned: false,
                commit_xml_extracted: false,
                commit_validated_outcome: None,
                commit_xml_archived: false,
                context_cleaned: false,
                continuation: ContinuationState {
                    context_cleanup_pending: true,
                    ..state.continuation.reset()
                },
                agent_chain,
                development_context_prepared_iteration: None,
                development_prompt_prepared_iteration: None,
                development_required_files_cleaned_iteration: None,
                development_agent_invoked_iteration: None,
                development_xml_extracted_iteration: None,
                development_validated_outcome: None,
                development_xml_archived_iteration: None,
                metrics: state.metrics.increment_dev_iterations_completed(),
                ..state
            }
        }
        DevelopmentEvent::OutputValidationFailed { iteration, attempt }
        | DevelopmentEvent::XmlMissing { iteration, attempt } => {
            // Switch to the next agent when:
            // - a same-agent retry was already pending (the retry still produced invalid output), OR
            // - the accumulated invalid-output counter has reached the threshold, OR
            // - this attempt value implies we've hit the threshold.
            // Otherwise accumulate the counter on the current agent and retry.
            const MAX_INVALID_OUTPUT_ATTEMPTS: u32 = 3;
            let should_switch = state.continuation.same_agent_retry_pending
                || state.continuation.invalid_output_attempts >= MAX_INVALID_OUTPUT_ATTEMPTS
                || attempt.saturating_add(1) >= MAX_INVALID_OUTPUT_ATTEMPTS;

            if should_switch {
                let new_agent_chain = state.agent_chain.switch_to_next_agent().clear_session_id();
                PipelineState {
                    phase: crate::reducer::event::PipelinePhase::Development,
                    iteration,
                    agent_chain: new_agent_chain.with_mode(DrainMode::Normal),
                    continuation: ContinuationState {
                        invalid_output_attempts: 0,
                        same_agent_retry_count: 0,
                        same_agent_retry_pending: false,
                        same_agent_retry_reason: None,
                        ..state.continuation
                    },
                    // Preserve developer-agent progress and retry analysis only.
                    development_context_prepared_iteration: state
                        .development_context_prepared_iteration,
                    development_prompt_prepared_iteration: state
                        .development_prompt_prepared_iteration,
                    development_required_files_cleaned_iteration: state
                        .development_required_files_cleaned_iteration,
                    development_agent_invoked_iteration: state.development_agent_invoked_iteration,
                    analysis_agent_invoked_iteration: None,
                    development_xml_extracted_iteration: None,
                    development_validated_outcome: None,
                    development_xml_archived_iteration: None,
                    ..state
                }
            } else {
                PipelineState {
                    phase: crate::reducer::event::PipelinePhase::Development,
                    iteration,
                    agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
                    continuation: ContinuationState {
                        invalid_output_attempts: attempt.saturating_add(1),
                        ..state.continuation
                    },
                    // Preserve developer-agent progress and retry analysis only.
                    development_context_prepared_iteration: state
                        .development_context_prepared_iteration,
                    development_prompt_prepared_iteration: state
                        .development_prompt_prepared_iteration,
                    development_required_files_cleaned_iteration: state
                        .development_required_files_cleaned_iteration,
                    development_agent_invoked_iteration: state.development_agent_invoked_iteration,
                    analysis_agent_invoked_iteration: None,
                    development_xml_extracted_iteration: None,
                    development_validated_outcome: None,
                    development_xml_archived_iteration: None,
                    ..state
                }
            }
        }
        DevelopmentEvent::ContinuationBudgetExhausted {
            iteration,
            total_attempts: _,
            last_status,
        } => {
            // CRITICAL FIX: After continuation budget exhaustion, COMPLETE the iteration
            // rather than falling back to another agent within the same iteration.
            //
            // Previous behavior: Switch to next agent and stay in Development phase
            // → Created infinite loop: attempt 1→2→exhaust→switch→restart→1→2→exhaust...
            //
            // New behavior: Complete the iteration (even if work incomplete) and either:
            // 1. Advance to next iteration if dev_iters remain
            // 2. Transition to AwaitingDevFix if all iterations exhausted
            //
            // This ensures bounded execution: after max_continue_count attempts fail,
            // the system moves forward rather than cycling indefinitely with fresh agents.

            let new_agent_chain = state.agent_chain.switch_to_next_agent().clear_session_id();

            // Check if we should transition to remediation flow
            if new_agent_chain.is_exhausted()
                && matches!(
                    last_status,
                    DevelopmentStatus::Failed | DevelopmentStatus::Partial
                )
            {
                // All agents exhausted AND work incomplete → try dev-fix flow
                PipelineState {
                    phase: crate::reducer::event::PipelinePhase::AwaitingDevFix,
                    previous_phase: Some(crate::reducer::event::PipelinePhase::Development),
                    iteration,
                    agent_chain: new_agent_chain.with_mode(DrainMode::Normal),
                    dev_fix_triggered: false,
                    continuation: ContinuationState {
                        continuation_attempt: 0,
                        invalid_output_attempts: 0,
                        same_agent_retry_count: 0,
                        same_agent_retry_pending: false,
                        same_agent_retry_reason: None,
                        context_cleanup_pending: false,
                        ..state.continuation
                    },
                    development_context_prepared_iteration: None,
                    development_prompt_prepared_iteration: None,
                    development_required_files_cleaned_iteration: None,
                    development_agent_invoked_iteration: None,
                    development_xml_extracted_iteration: None,
                    development_validated_outcome: None,
                    development_xml_archived_iteration: None,
                    ..state
                }
            } else {
                // Agents remain OR work complete → COMPLETE iteration and advance
                //
                // CRITICAL: Do NOT stay in Development phase with reset continuation state.
                // This would restart the continuation cycle, creating the infinite loop.
                //
                // Instead, emit IterationCompleted to advance to the next iteration or
                // proceed to the next pipeline phase.
                let next_event = DevelopmentEvent::IterationCompleted {
                    iteration,
                    // Mark as output_valid even if status is Partial/Failed, because we've
                    // exhausted our continuation budget and need to move forward rather than
                    // loop indefinitely. The summary will reflect the incomplete status.
                    output_valid: true,
                };

                // Reset continuation state and agent chain for next iteration
                let state_after_completion = PipelineState {
                    continuation: ContinuationState {
                        continuation_attempt: 0,
                        invalid_output_attempts: 0,
                        same_agent_retry_count: 0,
                        same_agent_retry_pending: false,
                        same_agent_retry_reason: None,
                        context_cleanup_pending: true,
                        ..state.continuation
                    },
                    agent_chain: new_agent_chain.reset().with_mode(DrainMode::Normal),
                    ..state
                };

                // Process IterationCompleted event through the reducer
                reduce_development_event(state_after_completion, next_event)
            }
        }
        DevelopmentEvent::ContinuationContextWritten {
            iteration,
            attempt: _,
        } => {
            // Context file was written, state remains unchanged.
            // The continuation state is already set by ContinuationTriggered.
            PipelineState {
                iteration,
                continuation: crate::reducer::state::ContinuationState {
                    context_write_pending: false,
                    ..state.continuation
                },
                ..state
            }
        }
        DevelopmentEvent::ContinuationContextCleaned => {
            // Context file was cleaned up, no state change needed.
            PipelineState {
                continuation: crate::reducer::state::ContinuationState {
                    context_cleanup_pending: false,
                    ..state.continuation
                },
                ..state
            }
        }
        // These events are handled by iteration_reducer
        _ => state,
    }
}
