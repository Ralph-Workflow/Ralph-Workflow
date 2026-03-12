//! Pass completion management for review phase.
//!
//! This module handles the logic for completing review passes and transitioning
//! between passes or to the commit phase. All functions are pure state transformations.

use crate::agents::{AgentDrain, DrainMode};
use crate::reducer::event::PipelinePhase;
use crate::reducer::state::{AgentChainState, CommitState, ContinuationState, PipelineState};

/// Handles `ReviewEvent::Completed`.
///
/// Completes review pass. If no issues found and all passes complete, transitions to `CommitMessage`.
/// Otherwise, stays in Review for fix or next pass.
pub(in crate::reducer::state_reduction::review) fn reduce_completed(
    state: PipelineState,
    pass: u32,
    issues_found: bool,
) -> PipelineState {
    let next_pass = if issues_found { pass } else { pass + 1 };
    let transitioning_to_commit = !issues_found && next_pass >= state.total_reviewer_passes;
    let next_phase = if transitioning_to_commit {
        PipelinePhase::CommitMessage
    } else {
        state.phase
    };

    if next_phase == PipelinePhase::CommitMessage {
        PipelineState {
            phase: next_phase,
            previous_phase: Some(PipelinePhase::Review),
            agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
            // When leaving Review for CommitMessage, keep the current pass index.
            // The commit reducer will increment and route to the next phase.
            reviewer_pass: pass,
            review_issues_found: issues_found,
            review_context_prepared_pass: None,
            review_prompt_prepared_pass: None,
            review_required_files_cleaned_pass: None,
            review_agent_invoked_pass: None,
            review_issues_xml_extracted_pass: None,
            review_validated_outcome: None,
            review_issues_markdown_written_pass: None,
            review_issue_snippets_extracted_pass: None,
            review_issues_xml_archived_pass: None,
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
                xsd_retry_count: 0,
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                // Clear review error when transitioning to commit phase
                last_review_xsd_error: None,
                ..state.continuation
            },
            fix_required_files_cleaned_pass: None,
            metrics: if issues_found {
                state.metrics
            } else {
                state.metrics.increment_review_passes_completed()
            },
            ..state
        }
    } else {
        let agent_chain = if issues_found {
            AgentChainState::initial()
                .with_max_cycles(state.agent_chain.max_cycles)
                .with_backoff_policy(
                    state.agent_chain.retry_delay_ms,
                    state.agent_chain.backoff_multiplier,
                    state.agent_chain.max_backoff_ms,
                )
                .reset_for_drain(AgentDrain::Fix)
        } else {
            state.agent_chain.with_mode(DrainMode::Normal)
        };

        PipelineState {
            phase: next_phase,
            agent_chain,
            reviewer_pass: next_pass,
            review_issues_found: issues_found,
            review_context_prepared_pass: None,
            review_prompt_prepared_pass: None,
            review_required_files_cleaned_pass: None,
            review_agent_invoked_pass: None,
            review_issues_xml_extracted_pass: None,
            review_validated_outcome: None,
            review_issues_markdown_written_pass: None,
            review_issue_snippets_extracted_pass: None,
            review_issues_xml_archived_pass: None,
            continuation: ContinuationState {
                invalid_output_attempts: 0,
                xsd_retry_count: 0,
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                ..state.continuation
            },
            fix_required_files_cleaned_pass: None,
            metrics: if issues_found {
                state.metrics
            } else {
                state.metrics.increment_review_passes_completed()
            },
            ..state
        }
    }
}

/// Handles `ReviewEvent::PhaseCompleted`.
///
/// Completes entire review phase and transitions to `CommitMessage`.
pub(in crate::reducer::state_reduction::review) fn reduce_phase_completed(
    state: PipelineState,
) -> PipelineState {
    PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::Review),
        agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
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
            ..state.continuation
        },
        review_required_files_cleaned_pass: None,
        fix_required_files_cleaned_pass: None,
        ..state
    }
}

/// Handles `ReviewEvent::PassCompletedClean`.
///
/// Completes a clean review pass (no issues found).
/// Advances to next pass or transitions to `CommitMessage` if all passes complete.
pub(in crate::reducer::state_reduction::review) fn reduce_pass_completed_clean(
    state: PipelineState,
    pass: u32,
) -> PipelineState {
    // Clean pass means no issues found in this pass.
    // Advance to the next pass when more passes remain.
    let next_pass = pass + 1;
    let next_phase = if next_pass >= state.total_reviewer_passes {
        PipelinePhase::CommitMessage
    } else {
        PipelinePhase::Review
    };

    if next_phase == PipelinePhase::CommitMessage {
        PipelineState {
            phase: next_phase,
            previous_phase: Some(PipelinePhase::Review),
            agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
            // Keep current pass index; commit transition increments for next pass.
            reviewer_pass: pass,
            review_issues_found: false,
            review_context_prepared_pass: None,
            review_prompt_prepared_pass: None,
            review_required_files_cleaned_pass: None,
            review_agent_invoked_pass: None,
            review_issues_xml_extracted_pass: None,
            review_validated_outcome: None,
            review_issues_markdown_written_pass: None,
            review_issue_snippets_extracted_pass: None,
            review_issues_xml_archived_pass: None,
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
                xsd_retry_count: 0,
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                ..state.continuation
            },
            fix_required_files_cleaned_pass: None,
            metrics: state.metrics.increment_review_passes_completed(),
            ..state
        }
    } else {
        PipelineState {
            phase: next_phase,
            agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
            reviewer_pass: next_pass,
            review_issues_found: false,
            review_context_prepared_pass: None,
            review_prompt_prepared_pass: None,
            review_required_files_cleaned_pass: None,
            review_agent_invoked_pass: None,
            review_issues_xml_extracted_pass: None,
            review_validated_outcome: None,
            review_issues_markdown_written_pass: None,
            review_issue_snippets_extracted_pass: None,
            review_issues_xml_archived_pass: None,
            continuation: ContinuationState {
                invalid_output_attempts: 0,
                xsd_retry_count: 0,
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                ..state.continuation
            },
            fix_required_files_cleaned_pass: None,
            metrics: state.metrics.increment_review_passes_completed(),
            ..state
        }
    }
}
