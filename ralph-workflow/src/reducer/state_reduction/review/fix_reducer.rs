// NOTE: split from reducer/state_reduction/review.rs (fix attempt events).

use crate::agents::{AgentDrain, DrainMode};
use crate::reducer::event::{PipelinePhase, ReviewEvent};
use crate::reducer::state::{
    AgentChainState, CommitState, ContinuationState, FixStatus, FixValidatedOutcome, PipelineState,
};

fn clear_fix_drain_progress(state: PipelineState) -> PipelineState {
    PipelineState {
        review_issues_found: false,
        fix_prompt_prepared_pass: None,
        fix_required_files_cleaned_pass: None,
        fix_agent_invoked_pass: None,
        fix_result_xml_extracted_pass: None,
        fix_validated_outcome: None,
        fix_result_xml_archived_pass: None,
        ..state
    }
}

/// Handles `ReviewEvent::FixAttemptStarted`.
///
/// Starts a new fix attempt by resetting the agent chain for the Fix drain
/// and clearing pending flags to prevent infinite loops.
///
/// Fix attempts use the Reviewer agent chain by design. The pipeline has three
/// agent roles: Developer, Reviewer, and Commit. Fixes are performed by the same
/// agent chain configured for review (there is no separate "Fixer" role), since
/// the fix phase is part of the review workflow.
pub(super) fn reduce_fix_attempt_started(state: PipelineState) -> PipelineState {
    PipelineState {
        agent_chain: AgentChainState::initial()
            .with_max_cycles(state.agent_chain.max_cycles)
            .with_backoff_policy(
                state.agent_chain.retry_delay_ms,
                state.agent_chain.backoff_multiplier,
                state.agent_chain.max_backoff_ms,
            )
            .reset_for_drain(AgentDrain::Fix),
        // Clear pending flags when fix attempt starts to prevent infinite loops.
        // xsd_retry_pending is cleared to ensure the XSD retry effect doesn't re-trigger
        // after the fix attempt starts a fresh agent invocation.
        continuation: ContinuationState {
            invalid_output_attempts: 0,
            fix_continue_pending: false,
            xsd_retry_pending: false,
            same_agent_retry_pending: false,
            same_agent_retry_reason: None,
            // Clear fix error when starting a new fix attempt
            last_fix_xsd_error: None,
            ..state.continuation
        },
        fix_prompt_prepared_pass: None,
        fix_required_files_cleaned_pass: None,
        fix_agent_invoked_pass: None,
        fix_result_xml_extracted_pass: None,
        fix_validated_outcome: None,
        fix_result_xml_archived_pass: None,
        ..state
    }
}

/// Handles `ReviewEvent::FixPromptPrepared`.
///
/// Marks fix prompt as prepared for this pass.
/// Clears retry and continuation flags to prevent infinite loops.
pub(super) fn reduce_fix_prompt_prepared(state: PipelineState, pass: u32) -> PipelineState {
    PipelineState {
        agent_chain: state.agent_chain.with_drain(AgentDrain::Fix),
        fix_prompt_prepared_pass: Some(pass),
        continuation: ContinuationState {
            xsd_retry_pending: false,
            xsd_retry_session_reuse_pending: state.continuation.xsd_retry_session_reuse_pending,
            same_agent_retry_pending: false,
            same_agent_retry_reason: None,
            // Clear fix_continue_pending to prevent infinite loop.
            // Once the fix prompt is prepared, the fix continuation attempt has started,
            // so we should not re-derive PrepareFixPrompt.
            fix_continue_pending: false,
            ..state.continuation
        },
        ..state
    }
}

/// Handles `ReviewEvent::FixResultXmlCleaned`.
///
/// Marks fix result XML as cleaned for this pass (pre-invocation cleanup).
pub(super) fn reduce_fix_result_xml_cleaned(state: PipelineState, pass: u32) -> PipelineState {
    PipelineState {
        fix_required_files_cleaned_pass: Some(pass),
        ..state
    }
}

/// Handles `ReviewEvent::FixAgentInvoked`.
///
/// Marks fix agent as invoked for this pass and increments metrics.
/// Clears retry flags since agent invocation is a fresh attempt.
pub(super) fn reduce_fix_agent_invoked(state: PipelineState, pass: u32) -> PipelineState {
    PipelineState {
        agent_chain: state.agent_chain.with_drain(AgentDrain::Fix),
        fix_agent_invoked_pass: Some(pass),
        continuation: ContinuationState {
            xsd_retry_pending: false,
            xsd_retry_session_reuse_pending: false,
            same_agent_retry_pending: false,
            same_agent_retry_reason: None,
            ..state.continuation
        },
        metrics: state.metrics.increment_fix_runs_total(),
        ..state
    }
}

/// Handles `ReviewEvent::FixResultXmlExtracted`.
///
/// Marks fix result XML as extracted for this pass.
pub(super) fn reduce_fix_result_xml_extracted(state: PipelineState, pass: u32) -> PipelineState {
    PipelineState {
        fix_result_xml_extracted_pass: Some(pass),
        ..state
    }
}

/// Handles `ReviewEvent::FixResultXmlValidated`.
///
/// Stores fix validation outcome and clears XSD error (validation succeeded).
pub(super) fn reduce_fix_result_xml_validated(
    state: PipelineState,
    pass: u32,
    status: FixStatus,
    summary: Option<String>,
) -> PipelineState {
    PipelineState {
        fix_validated_outcome: Some(FixValidatedOutcome {
            pass,
            status,
            summary,
        }),
        continuation: ContinuationState {
            // Clear error when validation succeeds
            last_fix_xsd_error: None,
            ..state.continuation
        },
        ..state
    }
}

/// Handles `ReviewEvent::FixResultXmlArchived`.
///
/// Marks fix result XML as archived for this pass.
pub(super) fn reduce_fix_result_xml_archived(state: PipelineState, pass: u32) -> PipelineState {
    PipelineState {
        fix_result_xml_archived_pass: Some(pass),
        ..state
    }
}

/// Handles `ReviewEvent::FixOutcomeApplied`.
///
/// Applies the fix outcome by checking if continuation is needed or fix is complete.
/// Recursively reduces the derived event (`FixContinuationTriggered`, `FixContinuationBudgetExhausted`, or `FixAttemptCompleted`).
pub(super) fn reduce_fix_outcome_applied(state: PipelineState, pass: u32) -> PipelineState {
    let Some(outcome) = state
        .fix_validated_outcome
        .as_ref()
        .filter(|o| o.pass == pass)
    else {
        return state;
    };

    let next_event = if outcome.status.needs_continuation() {
        let next_attempt = state.continuation.fix_continuation_attempt + 1;
        if next_attempt >= state.continuation.max_fix_continue_count {
            ReviewEvent::FixContinuationBudgetExhausted {
                pass,
                total_attempts: next_attempt,
                last_status: outcome.status,
            }
        } else {
            ReviewEvent::FixContinuationTriggered {
                pass,
                status: outcome.status,
                summary: outcome.summary.clone(),
            }
        }
    } else {
        let changes_made = matches!(outcome.status, FixStatus::AllIssuesAddressed);
        ReviewEvent::FixAttemptCompleted { pass, changes_made }
    };

    // Recursively reduce the derived event
    super::reduce_review_event(state, next_event)
}

/// Handles `ReviewEvent::FixAttemptCompleted`.
///
/// Completes fix attempt and transitions to `CommitMessage` phase.
/// Increments completed passes counter.
pub(super) fn reduce_fix_attempt_completed(
    state: PipelineState,
    pass: u32,
    _changes_made: bool,
) -> PipelineState {
    // Fix completed successfully - increment completed passes counter
    PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::Review),
        agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
        reviewer_pass: pass,
        review_issues_found: false,
        fix_prompt_prepared_pass: None,
        fix_required_files_cleaned_pass: None,
        fix_agent_invoked_pass: None,
        fix_result_xml_extracted_pass: None,
        fix_validated_outcome: None,
        fix_result_xml_archived_pass: None,
        commit: CommitState::NotStarted,
        commit_prompt_prepared: false,
        commit_diff_prepared: false,
        commit_diff_empty: false,
        commit_agent_invoked: false,
        commit_required_files_cleaned: false,
        commit_xml_extracted: false,
        commit_validated_outcome: None,
        commit_xml_archived: false,
        continuation: ContinuationState {
            invalid_output_attempts: 0,
            // Clear fix error when transitioning to commit phase
            last_fix_xsd_error: None,
            ..state.continuation
        },
        metrics: state.metrics.increment_review_passes_completed(),
        ..state
    }
}

/// Handles `ReviewEvent::FixContinuationTriggered`.
///
/// Triggers a fix continuation when fix output indicates work is incomplete.
/// Increments continuation metrics and sets `fix_continue_pending`.
pub(super) fn reduce_fix_continuation_triggered(
    state: PipelineState,
    pass: u32,
    status: FixStatus,
    summary: Option<String>,
) -> PipelineState {
    // Fix output is valid but indicates work is incomplete (issues_remain)
    PipelineState {
        agent_chain: state
            .agent_chain
            .with_drain(AgentDrain::Fix)
            .with_mode(DrainMode::Continuation),
        reviewer_pass: pass,
        fix_prompt_prepared_pass: None,
        fix_required_files_cleaned_pass: None,
        fix_agent_invoked_pass: None,
        fix_result_xml_extracted_pass: None,
        fix_validated_outcome: None,
        fix_result_xml_archived_pass: None,
        continuation: state.continuation.trigger_fix_continuation(status, summary),
        metrics: state
            .metrics
            .increment_fix_continuations_total()
            .increment_fix_continuation_attempt(),
        ..state
    }
}

/// Handles `ReviewEvent::FixContinuationSucceeded`.
///
/// Completes fix continuation successfully and transitions to `CommitMessage`.
/// Increments completed passes counter.
pub(super) fn reduce_fix_continuation_succeeded(
    state: PipelineState,
    pass: u32,
    _total_attempts: u32,
) -> PipelineState {
    // Fix continuation succeeded - transition to CommitMessage
    // Use reset() instead of new() to preserve configured limits
    // Fix succeeded after continuation - increment review passes completed
    let state = clear_fix_drain_progress(state);

    PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::Review),
        reviewer_pass: pass,
        commit: CommitState::NotStarted,
        commit_prompt_prepared: false,
        commit_diff_prepared: false,
        commit_diff_empty: false,
        commit_diff_content_id_sha256: None,
        commit_agent_invoked: false,
        commit_required_files_cleaned: false,
        commit_xml_extracted: false,
        commit_validated_outcome: None,
        commit_xml_archived: false,
        continuation: state.continuation.reset(),
        agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
        metrics: state.metrics.increment_review_passes_completed(),
        ..state
    }
}

/// Handles `ReviewEvent::FixContinuationBudgetExhausted`.
///
/// Fix continuation budget exhausted - proceed to commit with current state.
/// Policy: We accept partial fixes rather than blocking the pipeline.
pub(super) fn reduce_fix_continuation_budget_exhausted(
    state: PipelineState,
    pass: u32,
    _total_attempts: u32,
    _last_status: FixStatus,
) -> PipelineState {
    // Fix continuation budget exhausted - proceed to commit with current state
    // Policy: We accept partial fixes rather than blocking the pipeline
    // Use reset() instead of new() to preserve configured limits
    let state = clear_fix_drain_progress(state);

    PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::Review),
        reviewer_pass: pass,
        commit: CommitState::NotStarted,
        commit_prompt_prepared: false,
        commit_diff_prepared: false,
        commit_diff_empty: false,
        commit_diff_content_id_sha256: None,
        commit_agent_invoked: false,
        commit_required_files_cleaned: false,
        commit_xml_extracted: false,
        commit_validated_outcome: None,
        commit_xml_archived: false,
        continuation: state.continuation.reset(),
        agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
        ..state
    }
}

/// Handles `ReviewEvent::FixOutputValidationFailed` and `ReviewEvent::FixResultXmlMissing`.
///
/// Increments XSD retry count and either:
/// - Sets `xsd_retry_pending` for another attempt (if budget remains)
/// - Switches to next agent in chain (if XSD retries exhausted)
pub(super) fn reduce_fix_output_validation_failed(
    state: PipelineState,
    pass: u32,
    attempt: u32,
    error_detail: Option<String>,
) -> PipelineState {
    // Same policy as review output validation failure
    let new_xsd_count = state.continuation.xsd_retry_count + 1;

    // Only increment metrics if we're actually retrying (not exhausted)
    let will_retry = new_xsd_count < state.continuation.max_xsd_retry_count;

    if new_xsd_count >= state.continuation.max_xsd_retry_count {
        // XSD retries exhausted - switch to next agent
        // Reset orchestration flags to ensure prompt is prepared and new agent is invoked
        let new_agent_chain = state
            .agent_chain
            .with_drain(AgentDrain::Fix)
            .switch_to_next_agent()
            .clear_session_id();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: pass,
            agent_chain: new_agent_chain
                .with_drain(AgentDrain::Fix)
                .with_mode(DrainMode::Normal),
            continuation: ContinuationState {
                invalid_output_attempts: 0,
                xsd_retry_count: 0,
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                // Clear error when switching agents
                last_fix_xsd_error: None,
                ..state.continuation
            },
            // Reset orchestration flags to ensure:
            // 1. Prompt is prepared for new agent
            // 2. New agent is invoked
            // 3. Cleanup runs before invocation
            fix_prompt_prepared_pass: None,
            fix_agent_invoked_pass: None,
            fix_required_files_cleaned_pass: None,
            metrics: if will_retry {
                state.metrics.increment_xsd_retry_fix()
            } else {
                state.metrics
            },
            ..state
        }
    } else {
        // Stay in Review, increment attempt counters, set retry pending
        // Reset orchestration flags to ensure XSD retry prompt is prepared
        // and agent is re-invoked with the retry prompt.
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: pass,
            agent_chain: state
                .agent_chain
                .with_drain(AgentDrain::Fix)
                .with_mode(DrainMode::XsdRetry),
            continuation: ContinuationState {
                invalid_output_attempts: attempt + 1,
                xsd_retry_count: new_xsd_count,
                xsd_retry_pending: true,
                // Reuse last session id for fix XSD retry when available.
                xsd_retry_session_reuse_pending: true,
                // Preserve error detail for XSD retry prompt
                last_fix_xsd_error: error_detail,
                ..state.continuation
            },
            // Reset orchestration flags to ensure:
            // 1. XSD retry prompt is prepared (fix_prompt_prepared_pass = None)
            // 2. Agent is re-invoked with the retry prompt (fix_agent_invoked_pass = None)
            // 3. Cleanup runs before re-invocation (fix_required_files_cleaned_pass = None)
            fix_prompt_prepared_pass: None,
            fix_agent_invoked_pass: None,
            fix_required_files_cleaned_pass: None,
            metrics: if will_retry {
                state.metrics.increment_xsd_retry_fix()
            } else {
                state.metrics
            },
            ..state
        }
    }
}
