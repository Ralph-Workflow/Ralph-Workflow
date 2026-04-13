use crate::reducer::state::{CommitState, PipelineState};

/// Handle commit message validation failure.
///
/// On validation failure, switch to the next agent in the chain.
/// Uses `MAX_VALIDATION_RETRY_ATTEMPTS` as a safety cap.
pub(super) fn reduce_commit_validation_failed(
    state: PipelineState,
    _attempt: u32,
    _reason: String,
) -> PipelineState {
    let max_attempts = crate::reducer::state::MAX_VALIDATION_RETRY_ATTEMPTS;

    // Validation failed - switch to next agent
    let new_agent_chain = state.agent_chain.switch_to_next_agent().clear_session_id();

    let continuation = crate::reducer::state::ContinuationState {
        invalid_output_attempts: state.continuation.invalid_output_attempts.saturating_add(1),
        same_agent_retry_count: 0,
        same_agent_retry_pending: false,
        same_agent_retry_reason: None,
        ..state.continuation
    };

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
            commit_selected_files: vec![],
            commit_xml_archived: false,
            continuation,
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
            commit_selected_files: vec![],
            commit_xml_archived: false,
            continuation,
            ..state
        }
    }
}
