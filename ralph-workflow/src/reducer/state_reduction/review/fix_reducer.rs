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
        fix_analysis_agent_invoked_pass: None,
        fix_result_xml_extracted_pass: None,
        fix_validated_outcome: None,
        fix_result_xml_archived_pass: None,
        ..state
    }
}

fn transition_to_commit_after_fix(
    state: PipelineState,
    pass: u32,
    increment_review_passes_completed: bool,
) -> PipelineState {
    let state = clear_fix_drain_progress(state);

    PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::Review),
        reviewer_pass: pass,
        agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
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
        commit_selected_files: Vec::new(),
        commit_excluded_files: Vec::new(),
        commit_residual_retry_pass: 0,
        continuation: state.continuation.reset(),
        metrics: if increment_review_passes_completed {
            state.metrics.increment_review_passes_completed()
        } else {
            state.metrics
        },
        ..state
    }
}

/// Handles `ReviewEvent::FixAttemptStarted`.
///
/// Starts a new fix attempt by resetting the agent chain for the Fix drain
/// and clearing pending flags to prevent infinite loops.
///
/// Fix attempts use the Fix agent chain (AgentRole::Fix). The Fix role is distinct
/// from Reviewer: fix agents are write-capable (they apply changes), while reviewer
/// agents produce findings. In legacy configs without a dedicated fix chain, the
/// Fix role falls back to the reviewer chain via `get_effective_fix_fallbacks()`.
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
        continuation: ContinuationState {
            invalid_output_attempts: 0,
            fix_continue_pending: false,
            same_agent_retry_pending: false,
            same_agent_retry_reason: None,
            ..state.continuation
        },
        fix_prompt_prepared_pass: None,
        fix_required_files_cleaned_pass: None,
        fix_agent_invoked_pass: None,
        fix_analysis_agent_invoked_pass: None,
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
            same_agent_retry_pending: false,
            same_agent_retry_reason: None,
            ..state.continuation
        },
        metrics: state.metrics.increment_fix_runs_total(),
        ..state
    }
}

/// Handles `ReviewEvent::FixAnalysisAgentInvoked`.
///
/// Marks fix analysis agent as invoked for this pass and increments metrics.
/// This is the fix verification step that mirrors development analysis.
pub(super) fn reduce_fix_analysis_agent_invoked(state: PipelineState, pass: u32) -> PipelineState {
    PipelineState {
        agent_chain: state.agent_chain.with_drain(AgentDrain::Analysis),
        fix_analysis_agent_invoked_pass: Some(pass),
        continuation: ContinuationState {
            same_agent_retry_pending: false,
            same_agent_retry_reason: None,
            ..state.continuation
        },
        metrics: state.metrics.increment_fix_analysis_runs_total(),
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
    analysis_decision: Option<crate::reducer::state::AnalysisDecision>,
) -> PipelineState {
    PipelineState {
        fix_validated_outcome: Some(FixValidatedOutcome {
            pass,
            status,
            summary,
            analysis_decision,
        }),
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
/// Applies the fix outcome using explicit `AnalysisDecision` when present; falls
/// back to status-based continuation logic when `analysis_decision` is `None`.
///
/// Phase 2 routing table:
/// - `ReadyToCommit` → CommitMessage (same as current `AllIssuesAddressed` path)
/// - `NeedsAnotherReview` → Review (fresh review pass with same reviewer_pass index)
/// - `NeedsReplanning` → Planning (start a new development cycle)
/// - `NeedsMoreWork` | `None` → status-based continuation logic (unchanged)
/// - `ReadyForReview` → treated as `NeedsAnotherReview` in post-fix context
pub(super) fn reduce_fix_outcome_applied(state: PipelineState, pass: u32) -> PipelineState {
    let Some(outcome) = state
        .fix_validated_outcome
        .as_ref()
        .filter(|o| o.pass == pass)
    else {
        return state;
    };

    // Phase 2: check explicit analysis decision first
    use crate::reducer::state::AnalysisDecision;
    match outcome.analysis_decision {
        Some(AnalysisDecision::NeedsAnotherReview) | Some(AnalysisDecision::ReadyForReview) => {
            return transition_to_review_after_fix(state, pass);
        }
        Some(AnalysisDecision::NeedsReplanning) => {
            return transition_to_planning_after_fix(state, pass);
        }
        Some(AnalysisDecision::ReadyToCommit) => {
            let next_event = ReviewEvent::FixAttemptCompleted {
                pass,
                changes_made: true,
            };
            return super::reduce_review_event(state, next_event);
        }
        // NeedsMoreWork or None: fall through to status-based logic
        Some(AnalysisDecision::NeedsMoreWork) | None => {}
    }

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

/// Route back to Review phase after fix (Phase 2: NeedsAnotherReview decision).
///
/// Clears fix drain progress and resets the chain to Review drain for a fresh
/// review pass. The reviewer_pass index is unchanged — the review orchestrator
/// re-invokes the reviewer agent for the same pass number.
fn transition_to_review_after_fix(state: PipelineState, pass: u32) -> PipelineState {
    let state = clear_fix_drain_progress(state);
    PipelineState {
        phase: PipelinePhase::Review,
        previous_phase: Some(PipelinePhase::Review),
        reviewer_pass: pass,
        agent_chain: AgentChainState::initial()
            .with_max_cycles(state.agent_chain.max_cycles)
            .with_backoff_policy(
                state.agent_chain.retry_delay_ms,
                state.agent_chain.backoff_multiplier,
                state.agent_chain.max_backoff_ms,
            )
            .reset_for_drain(AgentDrain::Review)
            .with_mode(DrainMode::Normal),
        continuation: state.continuation.reset(),
        ..state
    }
}

/// Route to Planning phase after fix (Phase 2: NeedsReplanning decision).
///
/// Clears fix and review drain progress and resets for a new development cycle.
/// The iteration is incremented because a commit checkpoint is expected before
/// replanning (the plan says "commit first, then continue to planning").
fn transition_to_planning_after_fix(state: PipelineState, pass: u32) -> PipelineState {
    let state = clear_fix_drain_progress(state);
    // Clear review progress as well since we're leaving the review cycle entirely
    let state = clear_review_drain_progress(state);
    PipelineState {
        phase: PipelinePhase::Planning,
        previous_phase: Some(PipelinePhase::Review),
        reviewer_pass: pass,
        agent_chain: AgentChainState::initial()
            .with_max_cycles(state.agent_chain.max_cycles)
            .with_backoff_policy(
                state.agent_chain.retry_delay_ms,
                state.agent_chain.backoff_multiplier,
                state.agent_chain.max_backoff_ms,
            )
            .reset_for_drain(AgentDrain::Planning)
            .with_mode(DrainMode::Normal),
        continuation: state.continuation.reset(),
        ..state
    }
}

fn clear_review_drain_progress(state: PipelineState) -> PipelineState {
    PipelineState {
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
        ..state
    }
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
    transition_to_commit_after_fix(state, pass, true)
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
    let agent_chain = if state.agent_chain.current_drain == AgentDrain::Analysis {
        AgentChainState::initial()
            .with_max_cycles(state.agent_chain.max_cycles)
            .with_backoff_policy(
                state.agent_chain.retry_delay_ms,
                state.agent_chain.backoff_multiplier,
                state.agent_chain.max_backoff_ms,
            )
            .reset_for_drain(AgentDrain::Review)
            .with_mode(DrainMode::Continuation)
    } else {
        state
            .agent_chain
            .with_drain(AgentDrain::Fix)
            .with_mode(DrainMode::Continuation)
    };

    // Fix output is valid but indicates work is incomplete (issues_remain)
    PipelineState {
        agent_chain,
        reviewer_pass: pass,
        fix_prompt_prepared_pass: None,
        fix_required_files_cleaned_pass: None,
        fix_agent_invoked_pass: None,
        fix_analysis_agent_invoked_pass: None,
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
    transition_to_commit_after_fix(state, pass, true)
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
    // Fix continuation budget exhausted - proceed to commit with current state.
    // Policy: We accept partial fixes rather than blocking the pipeline.
    transition_to_commit_after_fix(state, pass, false)
}

/// Handles `ReviewEvent::FixOutputValidationFailed` and `ReviewEvent::FixResultXmlMissing`.
///
/// Switches to next agent in chain when fix output validation fails.
/// XSD retry mode has been removed; validation failure always advances to the next agent.
pub(super) fn reduce_fix_output_validation_failed(
    state: PipelineState,
    pass: u32,
    attempt: u32,
    _error_detail: Option<String>,
) -> PipelineState {
    // XSD retry mode removed: validation failure always switches to next agent.
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
            invalid_output_attempts: attempt + 1,
            ..state.continuation
        },
        // Reset orchestration flags to ensure:
        // 1. Prompt is prepared for new agent
        // 2. New agent is invoked
        // 3. Cleanup runs before invocation
        fix_prompt_prepared_pass: None,
        fix_agent_invoked_pass: None,
        fix_analysis_agent_invoked_pass: None,
        fix_required_files_cleaned_pass: None,
        ..state
    }
}
