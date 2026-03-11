//! Iteration lifecycle and step completion reducer
//!
//! Handles events related to:
//! - Phase transitions (`PhaseStarted`, `PhaseCompleted`)
//! - Iteration lifecycle (`IterationStarted`, `IterationCompleted`)
//! - Step completions (`ContextPrepared`, `PromptPrepared`, etc.)

use crate::agents::DrainMode;
use crate::reducer::event::DevelopmentEvent;
use crate::reducer::state::{ContinuationState, DevelopmentStatus, PipelineState};

use super::reduce_development_event;

pub(super) fn reduce_iteration_event(
    state: PipelineState,
    event: DevelopmentEvent,
) -> PipelineState {
    match event {
        DevelopmentEvent::PhaseStarted => PipelineState {
            phase: crate::reducer::event::PipelinePhase::Development,
            agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
            continuation: crate::reducer::state::ContinuationState {
                context_write_pending: false,
                context_cleanup_pending: false,
                ..state.continuation
            },
            development_context_prepared_iteration: None,
            development_prompt_prepared_iteration: None,
            development_required_files_cleaned_iteration: None,
            development_agent_invoked_iteration: None,
            analysis_agent_invoked_iteration: None,
            development_xml_extracted_iteration: None,
            development_validated_outcome: None,
            development_xml_archived_iteration: None,
            ..state
        },
        DevelopmentEvent::IterationStarted { iteration } => {
            // New iteration started - increment iterations counter
            // (not incremented for continuations within same iteration)
            // Reset per-iteration analysis attempt counter
            // Reset per-iteration continuation attempt counter
            PipelineState {
                iteration,
                agent_chain: state.agent_chain.reset(),
                // Reset continuation state when starting a new iteration
                continuation: crate::reducer::state::ContinuationState {
                    context_cleanup_pending: true,
                    ..state.continuation.reset()
                },
                development_context_prepared_iteration: None,
                development_prompt_prepared_iteration: None,
                development_required_files_cleaned_iteration: None,
                development_agent_invoked_iteration: None,
                analysis_agent_invoked_iteration: None,
                development_xml_extracted_iteration: None,
                development_validated_outcome: None,
                development_xml_archived_iteration: None,
                metrics: state
                    .metrics
                    .increment_dev_iterations_started()
                    .reset_analysis_attempts_in_current_iteration()
                    .reset_dev_continuation_attempt(),
                ..state
            }
        }
        DevelopmentEvent::ContextPrepared { iteration } => PipelineState {
            development_context_prepared_iteration: Some(iteration),
            // Clear continue_pending to prevent infinite loop.
            // Once context is prepared, the continuation attempt has started,
            // so we should not re-derive PrepareDevelopmentContext.
            continuation: crate::reducer::state::ContinuationState {
                continue_pending: false,
                ..state.continuation
            },
            ..state
        },
        DevelopmentEvent::PromptPrepared { iteration } => PipelineState {
            development_prompt_prepared_iteration: Some(iteration),
            continuation: crate::reducer::state::ContinuationState {
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: state.continuation.xsd_retry_session_reuse_pending,
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                ..state.continuation
            },
            ..state
        },
        DevelopmentEvent::XmlCleaned { iteration } => PipelineState {
            development_required_files_cleaned_iteration: Some(iteration),
            ..state
        },
        DevelopmentEvent::AgentInvoked { iteration } => {
            // Developer agent invoked - increment attempt counter
            // (includes both initial attempts and continuations)
            PipelineState {
                development_agent_invoked_iteration: Some(iteration),
                continuation: crate::reducer::state::ContinuationState {
                    xsd_retry_pending: false,
                    xsd_retry_session_reuse_pending: false,
                    same_agent_retry_pending: false,
                    same_agent_retry_reason: None,
                    ..state.continuation
                },
                metrics: state.metrics.increment_dev_attempts_total(),
                ..state
            }
        }
        DevelopmentEvent::AnalysisAgentInvoked { iteration } => {
            PipelineState {
                analysis_agent_invoked_iteration: Some(iteration),
                // If analysis was invoked as part of an XSD retry cycle, clear the retry flag here
                // so orchestration can proceed to Extract/Validate instead of repeatedly deriving
                // the XSD retry effect.
                continuation: state.continuation.clear_xsd_retry_pending(),
                metrics: state
                    .metrics
                    .increment_analysis_attempts_total()
                    .increment_analysis_attempts_in_current_iteration(),
                ..state
            }
        }
        DevelopmentEvent::XmlExtracted { iteration } => PipelineState {
            development_xml_extracted_iteration: Some(iteration),
            ..state
        },
        DevelopmentEvent::XmlValidated {
            iteration,
            status,
            summary,
            files_changed,
            next_steps,
        } => PipelineState {
            development_validated_outcome: Some(
                crate::reducer::state::DevelopmentValidatedOutcome {
                    iteration,
                    status,
                    summary,
                    files_changed: files_changed.map(std::vec::Vec::into_boxed_slice),
                    next_steps,
                },
            ),
            ..state
        },
        DevelopmentEvent::XmlArchived { iteration } => PipelineState {
            development_xml_archived_iteration: Some(iteration),
            ..state
        },
        DevelopmentEvent::OutcomeApplied { iteration } => {
            let Some(outcome) = state
                .development_validated_outcome
                .as_ref()
                .filter(|o| o.iteration == iteration)
            else {
                return state;
            };

            let continuation_state = &state.continuation;

            let next_event = if matches!(outcome.status, DevelopmentStatus::Completed) {
                if continuation_state.is_continuation() {
                    DevelopmentEvent::ContinuationSucceeded {
                        iteration,
                        total_continuation_attempts: continuation_state.continuation_attempt,
                    }
                } else {
                    DevelopmentEvent::IterationCompleted {
                        iteration,
                        output_valid: true,
                    }
                }
            } else if continuation_state.continuation_attempt + 1
                >= continuation_state.max_continue_count
            {
                // CRITICAL FIX: Check if the NEXT attempt would reach/exceed the limit.
                // With max_continue_count = 3:
                // - At attempt 2: continuation_attempt + 1 = 3 >= 3 → exhaust (correct)
                // - At attempt 1: continuation_attempt + 1 = 2 < 3 → continue (correct)
                // - At attempt 0: continuation_attempt + 1 = 1 < 3 → continue (correct)
                //
                // This prevents the off-by-one bug where ContinuationTriggered increments
                // the counter before checking exhaustion, allowing one extra attempt.
                DevelopmentEvent::ContinuationBudgetExhausted {
                    iteration,
                    // Report total attempts INCLUDING the current attempt that just completed.
                    total_attempts: continuation_state.continuation_attempt + 1,
                    last_status: outcome.status,
                }
            } else {
                DevelopmentEvent::ContinuationTriggered {
                    iteration,
                    status: outcome.status,
                    summary: outcome.summary.clone(),
                    files_changed: outcome.files_changed.as_ref().map(|b| b.to_vec()),
                    next_steps: outcome.next_steps.clone(),
                }
            };

            reduce_development_event(state, next_event)
        }
        DevelopmentEvent::IterationCompleted {
            iteration,
            output_valid,
        } => {
            if output_valid {
                // After a successful dev iteration, go to CommitMessage phase to create a commit.
                PipelineState {
                    phase: crate::reducer::event::PipelinePhase::CommitMessage,
                    previous_phase: Some(crate::reducer::event::PipelinePhase::Development),
                    iteration,
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
                    // Reset continuation state on successful completion
                    // Use reset() to preserve configured limits (max_xsd_retry_count, etc.)
                    continuation: ContinuationState {
                        context_cleanup_pending: true,
                        ..state.continuation.reset()
                    },
                    agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
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
            } else {
                // Output was not valid enough to proceed to commit; retry in Development.
                let invalid_output_attempts = state.continuation.invalid_output_attempts + 1;
                if invalid_output_attempts > crate::reducer::state::MAX_DEV_INVALID_OUTPUT_RERUNS {
                    let new_agent_chain = state
                        .agent_chain
                        .switch_to_next_agent()
                        .clear_session_id()
                        .clear_continuation_prompt();
                    PipelineState {
                        phase: crate::reducer::event::PipelinePhase::Development,
                        iteration,
                        continuation: ContinuationState {
                            invalid_output_attempts: 0,
                            xsd_retry_count: 0,
                            xsd_retry_pending: false,
                            xsd_retry_session_reuse_pending: false,
                            same_agent_retry_count: 0,
                            same_agent_retry_pending: false,
                            same_agent_retry_reason: None,
                            ..state.continuation
                        },
                        agent_chain: new_agent_chain.with_mode(DrainMode::Normal),
                        development_context_prepared_iteration: None,
                        development_prompt_prepared_iteration: None,
                        development_required_files_cleaned_iteration: None,
                        development_agent_invoked_iteration: None,
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
                        continuation: ContinuationState {
                            invalid_output_attempts,
                            ..state.continuation
                        },
                        development_context_prepared_iteration: None,
                        development_prompt_prepared_iteration: None,
                        development_required_files_cleaned_iteration: None,
                        development_agent_invoked_iteration: None,
                        analysis_agent_invoked_iteration: None,
                        development_xml_extracted_iteration: None,
                        development_validated_outcome: None,
                        development_xml_archived_iteration: None,
                        ..state
                    }
                }
            }
        }
        DevelopmentEvent::PhaseCompleted => PipelineState {
            phase: crate::reducer::event::PipelinePhase::Review,
            // Reset continuation state when phase completes, but preserve configured limits.
            continuation: state.continuation.reset(),
            development_context_prepared_iteration: None,
            development_prompt_prepared_iteration: None,
            development_required_files_cleaned_iteration: None,
            development_agent_invoked_iteration: None,
            development_xml_extracted_iteration: None,
            development_validated_outcome: None,
            development_xml_archived_iteration: None,
            ..state
        },
        // These events are handled by continuation_reducer
        _ => state,
    }
}
