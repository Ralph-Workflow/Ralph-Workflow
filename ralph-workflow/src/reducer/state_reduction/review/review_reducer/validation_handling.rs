//! Validation failure handling for review phase.
//!
//! This module handles XSD validation failures and retry logic for the review phase.
//! All functions are pure state transformations implementing retry policies.

use crate::agents::DrainMode;
use crate::reducer::event::PipelinePhase;
use crate::reducer::state::{ContinuationState, PipelineState};

/// Handles `ReviewEvent::OutputValidationFailed` and `ReviewEvent::IssuesXmlMissing`.
///
/// Increments XSD retry count and either:
/// - Sets `xsd_retry_pending` for another attempt (if budget remains)
/// - Switches to next agent in chain (if XSD retries exhausted)
pub(in crate::reducer::state_reduction::review) fn reduce_output_validation_failed(
    state: PipelineState,
    pass: u32,
    attempt: u32,
    error_detail: Option<String>,
) -> PipelineState {
    // Policy: The reducer maintains retry state for determinism.
    // Handlers should emit `attempt` from state (checkpoint-resume safe).
    let new_xsd_count = state.continuation.xsd_retry_count + 1;

    // Only increment metrics if we're actually retrying (not exhausted)
    let will_retry = new_xsd_count < state.continuation.max_xsd_retry_count;

    if new_xsd_count >= state.continuation.max_xsd_retry_count {
        // XSD retries exhausted - switch to next agent
        // Reset orchestration flags to ensure prompt is prepared and new agent is invoked
        let new_agent_chain = state.agent_chain.switch_to_next_agent().clear_session_id();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: pass,
            agent_chain: new_agent_chain.with_mode(DrainMode::Normal),
            continuation: ContinuationState {
                invalid_output_attempts: 0,
                xsd_retry_count: 0,
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                same_agent_retry_count: 0,
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                // Clear error when switching agents
                last_review_xsd_error: None,
                ..state.continuation
            },
            // Reset orchestration flags to ensure:
            // 1. Prompt is prepared for new agent
            // 2. New agent is invoked
            // 3. Cleanup runs before invocation
            review_prompt_prepared_pass: None,
            review_agent_invoked_pass: None,
            review_required_files_cleaned_pass: None,
            metrics: if will_retry {
                state.metrics.increment_xsd_retry_review()
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
            agent_chain: state.agent_chain.with_mode(DrainMode::XsdRetry),
            continuation: ContinuationState {
                invalid_output_attempts: attempt + 1,
                xsd_retry_count: new_xsd_count,
                xsd_retry_pending: true,
                // Reuse last session id for review XSD retry when available.
                xsd_retry_session_reuse_pending: true,
                // Preserve error detail for XSD retry prompt
                last_review_xsd_error: error_detail,
                ..state.continuation
            },
            // Reset orchestration flags to ensure:
            // 1. XSD retry prompt is prepared (review_prompt_prepared_pass = None)
            // 2. Agent is re-invoked with the retry prompt (review_agent_invoked_pass = None)
            // 3. Cleanup runs before re-invocation (review_required_files_cleaned_pass = None)
            // 4. Extraction runs after agent produces new output (already None from missing)
            review_prompt_prepared_pass: None,
            review_agent_invoked_pass: None,
            review_required_files_cleaned_pass: None,
            metrics: if will_retry {
                state.metrics.increment_xsd_retry_review()
            } else {
                state.metrics
            },
            ..state
        }
    }
}
