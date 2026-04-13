//! Validation failure handling for review phase.

use crate::agents::DrainMode;
use crate::reducer::event::PipelinePhase;
use crate::reducer::state::{ContinuationState, PipelineState};

/// Handles `ReviewEvent::OutputValidationFailed` and `ReviewEvent::IssuesXmlMissing`.
///
/// Switches to the next agent when a same-agent retry was already pending (the
/// retry still produced invalid output) or the accumulated invalid-output counter
/// reaches the threshold.  Otherwise stays on the current agent and increments the
/// counter so several failures can accumulate before the agent switches.
pub(in crate::reducer::state_reduction::review) fn reduce_output_validation_failed(
    state: PipelineState,
    pass: u32,
    attempt: u32,
    _error_detail: Option<String>,
) -> PipelineState {
    const MAX_INVALID_OUTPUT_ATTEMPTS: u32 = 3;
    let should_switch = state.continuation.same_agent_retry_pending
        || state.continuation.invalid_output_attempts >= MAX_INVALID_OUTPUT_ATTEMPTS
        || attempt.saturating_add(1) >= MAX_INVALID_OUTPUT_ATTEMPTS;

    if should_switch {
        let new_agent_chain = state.agent_chain.switch_to_next_agent().clear_session_id();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: pass,
            agent_chain: new_agent_chain.with_mode(DrainMode::Normal),
            continuation: ContinuationState {
                invalid_output_attempts: 0,
                same_agent_retry_count: 0,
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                ..state.continuation
            },
            review_prompt_prepared_pass: None,
            review_agent_invoked_pass: None,
            review_required_files_cleaned_pass: None,
            ..state
        }
    } else {
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: pass,
            agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
            continuation: ContinuationState {
                invalid_output_attempts: attempt.saturating_add(1),
                ..state.continuation
            },
            review_prompt_prepared_pass: None,
            review_agent_invoked_pass: None,
            review_required_files_cleaned_pass: None,
            ..state
        }
    }
}
