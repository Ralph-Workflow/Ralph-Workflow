//! State transitions for review phase lifecycle.
//!
//! This module contains pure reducer functions that handle transitions between
//! review passes and phase state changes. All functions are deterministic state
//! transformations with no side effects.

use crate::agents::{AgentDrain, DrainMode};
use crate::reducer::event::PipelinePhase;
use crate::reducer::state::{
    AgentChainState, ContinuationState, PipelineState, PromptInputsState, ReviewValidatedOutcome,
};

/// Handles `ReviewEvent::PhaseStarted`.
///
/// Transitions to Review phase and initializes review state.
/// Clears any populated developer chain and resets continuation state.
pub(in crate::reducer::state_reduction::review) fn reduce_phase_started(
    state: PipelineState,
) -> PipelineState {
    PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        review_issues_found: false,
        // IMPORTANT: entering Review must not reuse a populated developer chain.
        // Clearing the chain ensures orchestration deterministically emits
        // InitializeAgentChain for AgentRole::Reviewer.
        agent_chain: {
            // Entering Review must clear any populated developer chain, but must preserve
            // the configured retry/backoff policy so behavior stays consistent across phases.
            AgentChainState::initial()
                .with_max_cycles(state.agent_chain.max_cycles)
                .with_backoff_policy(
                    state.agent_chain.retry_delay_ms,
                    state.agent_chain.backoff_multiplier,
                    state.agent_chain.max_backoff_ms,
                )
                .reset_for_drain(AgentDrain::Review)
        },
        // Entering Review must reset continuation state to avoid leaking
        // development continuation context into review/fix/rebase logic.
        // Preserve configured limits to keep budgets stable across phases.
        continuation: state.continuation.reset(),
        review_required_files_cleaned_pass: None,
        review_issue_snippets_extracted_pass: None,
        fix_required_files_cleaned_pass: None,
        ..state
    }
}

/// Handles `ReviewEvent::PassStarted`.
///
/// Starts a new review pass or retries the current pass.
/// Increments metrics for truly new passes, resets per-pass state.
pub(in crate::reducer::state_reduction::review) fn reduce_pass_started(
    state: PipelineState,
    pass: u32,
) -> PipelineState {
    // Increment for the first PassStarted (pass 0) and for any truly new pass.
    // A PassStarted re-emitted for the same pass (retry) must not increment.
    let is_first_pass = state.metrics.review_passes_started == 0;
    let is_new_pass = state.reviewer_pass != pass;

    let mut metrics = state.metrics;
    if is_first_pass || is_new_pass {
        metrics = metrics.increment_review_passes_started();
    }
    // Update current pass tracker
    metrics = metrics.set_current_review_pass(pass);
    // Reset per-pass fix continuation attempt counter when starting a new pass.
    // If orchestration re-emits PassStarted for the same pass (retry), preserve the
    // current per-pass attempt counter so retries don't erase history.
    if is_new_pass {
        metrics = metrics.reset_fix_continuation_attempt();
    }

    PipelineState {
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
        agent_chain: {
            let should_reset = pass != state.reviewer_pass;
            if should_reset {
                state.agent_chain.reset()
            } else {
                // If orchestration re-emits PassStarted for the same pass (e.g., retry after
                // OutputValidationFailed), preserve the agent selection so fallback is effective.
                state.agent_chain
            }
        },
        continuation: if pass == state.reviewer_pass {
            // If orchestration re-emits PassStarted for the same pass (e.g., retry after
            // OutputValidationFailed), clear xsd_retry_pending to prevent infinite loops.
            // The reducer owns retry accounting for determinism.
            ContinuationState {
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                ..state.continuation
            }
        } else {
            // New pass: reset retry state but preserve configured limits
            ContinuationState {
                invalid_output_attempts: 0,
                xsd_retry_count: 0,
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                same_agent_retry_count: 0,
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                // Clear review error when starting a new pass
                last_review_xsd_error: None,
                ..state.continuation
            }
        },
        metrics,
        ..state
    }
}

/// Handles `ReviewEvent::ContextPrepared`.
///
/// Marks review context as prepared for this pass.
/// Invalidates cached prompt inputs to force re-preparation with fresh context.
pub(in crate::reducer::state_reduction::review) fn reduce_context_prepared(
    state: PipelineState,
    pass: u32,
) -> PipelineState {
    PipelineState {
        review_context_prepared_pass: Some(pass),
        // Preparing review context rewrites the diff backup and baseline.
        // Invalidate any materialized inputs for this pass so we don't reuse
        // stale PLAN/DIFF materializations.
        prompt_inputs: PromptInputsState {
            review: None,
            ..state.prompt_inputs.clone()
        },
        // Also force prompt re-preparation for this pass if it had already been prepared.
        review_prompt_prepared_pass: None,
        agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
        ..state
    }
}

/// Handles `ReviewEvent::PromptPrepared`.
///
/// Marks review prompt as prepared for this pass.
/// Clears retry flags since fresh prompt preparation indicates new attempt.
pub(in crate::reducer::state_reduction::review) fn reduce_prompt_prepared(
    state: PipelineState,
    pass: u32,
) -> PipelineState {
    PipelineState {
        review_prompt_prepared_pass: Some(pass),
        agent_chain: state.agent_chain,
        continuation: ContinuationState {
            xsd_retry_pending: false,
            xsd_retry_session_reuse_pending: state.continuation.xsd_retry_session_reuse_pending,
            same_agent_retry_pending: false,
            same_agent_retry_reason: None,
            ..state.continuation
        },
        ..state
    }
}

/// Handles `ReviewEvent::IssuesXmlCleaned`.
///
/// Marks issues XML as cleaned for this pass (pre-invocation cleanup).
pub(in crate::reducer::state_reduction::review) fn reduce_issues_xml_cleaned(
    state: PipelineState,
    pass: u32,
) -> PipelineState {
    PipelineState {
        review_required_files_cleaned_pass: Some(pass),
        ..state
    }
}

/// Handles `ReviewEvent::AgentInvoked`.
///
/// Marks agent as invoked for this pass and increments metrics.
/// Clears retry flags since agent invocation is a fresh attempt.
pub(in crate::reducer::state_reduction::review) fn reduce_agent_invoked(
    state: PipelineState,
    pass: u32,
) -> PipelineState {
    PipelineState {
        review_agent_invoked_pass: Some(pass),
        continuation: ContinuationState {
            xsd_retry_pending: false,
            xsd_retry_session_reuse_pending: false,
            same_agent_retry_pending: false,
            same_agent_retry_reason: None,
            ..state.continuation
        },
        metrics: state.metrics.increment_review_runs_total(),
        ..state
    }
}

/// Handles `ReviewEvent::IssuesXmlExtracted`.
///
/// Marks issues XML as extracted for this pass.
pub(in crate::reducer::state_reduction::review) fn reduce_issues_xml_extracted(
    state: PipelineState,
    pass: u32,
) -> PipelineState {
    PipelineState {
        review_issues_xml_extracted_pass: Some(pass),
        ..state
    }
}

/// Handles `ReviewEvent::IssuesXmlValidated`.
///
/// Stores validation outcome and clears XSD error (validation succeeded).
pub(in crate::reducer::state_reduction::review) fn reduce_issues_xml_validated(
    state: PipelineState,
    pass: u32,
    issues_found: bool,
    clean_no_issues: bool,
    issues: Vec<String>,
    no_issues_found: Option<String>,
) -> PipelineState {
    PipelineState {
        review_validated_outcome: Some(ReviewValidatedOutcome {
            pass,
            issues_found,
            clean_no_issues,
            issues: issues.into_boxed_slice(),
            no_issues_found,
        }),
        continuation: ContinuationState {
            // Clear error when validation succeeds
            last_review_xsd_error: None,
            ..state.continuation
        },
        ..state
    }
}

/// Handles `ReviewEvent::IssuesMarkdownWritten`.
///
/// Marks issues markdown as written for this pass.
pub(in crate::reducer::state_reduction::review) fn reduce_issues_markdown_written(
    state: PipelineState,
    pass: u32,
) -> PipelineState {
    PipelineState {
        review_issues_markdown_written_pass: Some(pass),
        ..state
    }
}

/// Handles `ReviewEvent::IssueSnippetsExtracted`.
///
/// Marks issue snippets as extracted for this pass.
pub(in crate::reducer::state_reduction::review) fn reduce_issue_snippets_extracted(
    state: PipelineState,
    pass: u32,
) -> PipelineState {
    PipelineState {
        review_issue_snippets_extracted_pass: Some(pass),
        ..state
    }
}

/// Handles `ReviewEvent::IssuesXmlArchived`.
///
/// Marks issues XML as archived for this pass.
pub(in crate::reducer::state_reduction::review) fn reduce_issues_xml_archived(
    state: PipelineState,
    pass: u32,
) -> PipelineState {
    PipelineState {
        review_issues_xml_archived_pass: Some(pass),
        ..state
    }
}
