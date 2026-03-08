use crate::reducer::state::{CommitState, PipelineState};

/// Handle commit message validation failure with XSD retry logic.
///
/// This now integrates with the XSD retry tracking in `ContinuationState`
/// for uniformity with other phases.
pub(super) fn reduce_commit_validation_failed(
    state: PipelineState,
    attempt: u32,
    reason: String,
) -> PipelineState {
    let new_xsd_count = state.continuation.xsd_retry_count + 1;
    let max_attempts = crate::reducer::state::MAX_VALIDATION_RETRY_ATTEMPTS;

    // Only increment metrics if we're actually retrying (not exhausted)
    let will_retry =
        new_xsd_count < state.continuation.max_xsd_retry_count && new_xsd_count < max_attempts;

    // Check if XSD retries are exhausted (configured limit) or global safety limit hit.
    //
    // NOTE: Commit XSD retries intentionally reuse the same commit attempt number so we
    // can safely reuse attempt-scoped materialized inputs (diff, references, etc.).
    if new_xsd_count >= state.continuation.max_xsd_retry_count || new_xsd_count >= max_attempts {
        // XSD retries exhausted - switch to next agent
        let new_agent_chain = state.agent_chain.switch_to_next_agent().clear_session_id();

        // Check if we successfully advanced to next agent
        let advanced = new_agent_chain.current_agent_index != state.agent_chain.current_agent_index
            && new_agent_chain.retry_cycle == state.agent_chain.retry_cycle;

        if advanced {
            // Reset for new agent
            PipelineState {
                agent_chain: new_agent_chain,
                commit: CommitState::Generating {
                    attempt: 1,
                    max_attempts,
                },
                commit_prompt_prepared: false,
                commit_agent_invoked: false,
                commit_required_files_cleaned: false,
                commit_xml_extracted: false,
                commit_validated_outcome: None,
                commit_xml_archived: false,
                continuation: crate::reducer::state::ContinuationState {
                    xsd_retry_count: 0,
                    xsd_retry_pending: false,
                    xsd_retry_session_reuse_pending: false,
                    same_agent_retry_count: 0,
                    same_agent_retry_pending: false,
                    same_agent_retry_reason: None,
                    last_xsd_error: None,
                    ..state.continuation
                },
                metrics: if will_retry {
                    state.metrics.increment_xsd_retry_commit()
                } else {
                    state.metrics
                },
                ..state
            }
        } else {
            // All agents exhausted - reset so orchestration can handle
            PipelineState {
                agent_chain: new_agent_chain,
                commit: CommitState::NotStarted,
                commit_prompt_prepared: false,
                commit_agent_invoked: false,
                commit_required_files_cleaned: false,
                commit_xml_extracted: false,
                commit_validated_outcome: None,
                commit_xml_archived: false,
                continuation: crate::reducer::state::ContinuationState {
                    xsd_retry_count: 0,
                    xsd_retry_pending: false,
                    xsd_retry_session_reuse_pending: false,
                    same_agent_retry_count: 0,
                    same_agent_retry_pending: false,
                    same_agent_retry_reason: None,
                    last_xsd_error: None,
                    ..state.continuation
                },
                metrics: if will_retry {
                    state.metrics.increment_xsd_retry_commit()
                } else {
                    state.metrics
                },
                ..state
            }
        }
    } else {
        // Set XSD retry pending - orchestration will trigger retry with same agent/session
        PipelineState {
            commit: CommitState::Generating {
                attempt,
                max_attempts,
            },
            commit_prompt_prepared: false,
            commit_agent_invoked: false,
            commit_required_files_cleaned: false,
            commit_xml_extracted: false,
            commit_validated_outcome: None,
            commit_xml_archived: false,
            continuation: crate::reducer::state::ContinuationState {
                xsd_retry_count: new_xsd_count,
                xsd_retry_pending: true,
                // Reuse last session id for commit XSD retry when available.
                xsd_retry_session_reuse_pending: true,
                last_xsd_error: Some(reason),
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                ..state.continuation
            },
            metrics: if will_retry {
                state.metrics.increment_xsd_retry_commit()
            } else {
                state.metrics
            },
            ..state
        }
    }
}
