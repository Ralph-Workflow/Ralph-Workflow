//! Review phase orchestration.
//!
//! Pure orchestration: State → Effect, no I/O.
//!
//! Review phase has two drain-owned modes:
//!
//! 1. Fix drain (`current_drain == Fix`):
//!    a. Initialize the fix drain chain
//!    b. Prepare fix prompt
//!    c. Cleanup fix result XML
//!    d. Invoke fix agent
//!    e. Initialize the analysis drain chain
//!    f. Invoke fix analysis agent (verifies fix changes vs review issues)
//!    g. Extract fix result XML
//!    h. Validate fix result XML
//!    i. Archive fix result XML
//!    j. Apply fix outcome
//!
//! 2. Review drain (`current_drain == Review`):
//!    For each review pass (up to `total_reviewer_passes)`:
//!    a. Initialize the review drain chain
//!    b. Prepare review context
//!    c. Materialize review inputs (plan + diff)
//!    d. Prepare review prompt
//!    e. Cleanup review issues XML
//!    f. Invoke review agent
//!    g. Extract review issues XML
//!    h. Validate review issues XML
//!    i. Write issues markdown
//!    j. Extract review issue snippets
//!    k. Archive review issues XML
//!    l. Apply review outcome
//!
//! Review pass boundary handling:
//! - At `reviewer_pass` == `total_reviewer_passes`, still process the current pass
//! - On resume, progress flags are reset (pipeline.rs:453-532)
//! - Only skip to `SaveCheckpoint` when:
//!   - `reviewer_pass` > `total_reviewer_passes` (should not happen in normal flow)
//!   - `total_reviewer_passes` == 0 (no review passes configured)

use crate::agents::{AgentDrain, DrainMode};
use crate::reducer::effect::Effect;
use crate::reducer::event::CheckpointTrigger;
use crate::reducer::state::{PipelineState, PromptMode};

/// Files that the review agent writes.
///
/// These files are cleaned up before each review agent invocation to ensure
/// fresh output. The review agent writes to `.agent/tmp/issues.xml`.
pub const REQUIRED_FILES_ISSUES: &[&str] = &[".agent/tmp/issues.xml"];

/// Files that the fix agent writes.
///
/// These files are cleaned up before each fix agent invocation to ensure
/// fresh output. The fix agent writes to `.agent/tmp/fix_result.xml`.
/// When fix analysis is enabled, the analysis agent writes to `.agent/tmp/development_result.xml`.
pub const REQUIRED_FILES_FIX: &[&str] = &[
    ".agent/tmp/fix_result.xml",
    ".agent/tmp/development_result.xml",
];

pub(super) fn determine_review_effect(state: &PipelineState) -> Effect {
    let runtime_drain = state.runtime_drain();

    // Enter fix chain if:
    // 1. runtime_drain is Fix (normal fix flow), OR
    // 2. fix_analysis_agent_invoked_pass is set AND current_drain is Analysis (post-fix-analysis flow), OR
    // 3. fix_agent_invoked_pass is set AND current_drain is Analysis AND fix_analysis_agent_invoked_pass is None
    //    (transitioning from fix to analysis - after InvokeFixAgent but before FixAnalysisAgentInvoked)
    let in_fix_to_analysis_transition = state.fix_agent_invoked_pass == Some(state.reviewer_pass)
        && state.agent_chain.current_drain == AgentDrain::Analysis
        && state.fix_analysis_agent_invoked_pass.is_none();

    let in_fix_flow = runtime_drain == AgentDrain::Fix
        || (state.fix_analysis_agent_invoked_pass.is_some()
            && state.agent_chain.current_drain == AgentDrain::Analysis)
        || in_fix_to_analysis_transition;

    if in_fix_flow {
        // Check if we need to initialize the fix chain.
        // Skip if:
        // 1. We're in the fix-to-analysis transition (current_drain is Analysis but
        //    fix_analysis_agent_invoked_pass is not yet set), OR
        // 2. Legacy checkpoint with fix progress (current_drain defaulted to Planning but fix was in progress)
        let in_transition_to_analysis = state.agent_chain.current_drain == AgentDrain::Analysis
            && state.fix_analysis_agent_invoked_pass.is_none();

        let last_effect_was_fix_agent = state
            .continuation
            .last_effect_kind
            .as_deref()
            .is_some_and(|k| k.contains("InvokeFixAgent"));

        // Check if this is a legacy checkpoint that should continue with fix flow
        let current_drain_is_default = state.agent_chain.current_drain == AgentDrain::Planning;
        let has_fix_progress = state.fix_prompt_prepared_pass.is_some()
            || state.fix_agent_invoked_pass.is_some()
            || state.fix_required_files_cleaned_pass.is_some();
        let is_legacy_continue = current_drain_is_default && has_fix_progress;

        // Initialize if:
        // 1. Not in fix-to-analysis transition, AND
        // 2. Not a legacy continue case, AND
        // 3. Fix agent has NOT been invoked yet (we're starting the fix flow), AND
        //    This checks both the explicit flag AND the last_effect_kind (to handle the bug scenario
        //    where the flag isn't set yet but the fix was invoked)
        // 4. Either agents are empty OR chain doesn't match runtime drain
        let chain_matches = state.agent_chain.matches_runtime_drain(AgentDrain::Fix);
        let fix_not_started = state.fix_agent_invoked_pass.is_none()
            && !(last_effect_was_fix_agent
                && state.fix_prompt_prepared_pass == Some(state.reviewer_pass)
                && state.fix_required_files_cleaned_pass == Some(state.reviewer_pass)
                && state.runtime_drain() == AgentDrain::Fix);

        let should_initialize_fix_chain = !in_transition_to_analysis
            && !is_legacy_continue
            && fix_not_started
            && (state.agent_chain.agents.is_empty() || !chain_matches);

        if should_initialize_fix_chain {
            return Effect::InitializeAgentChain {
                drain: AgentDrain::Fix,
            };
        }

        // If we're in Analysis drain during fix flow, we're in the fix analysis phase.
        // Don't re-enter fix preparation flow - continue with analysis steps.
        if state.agent_chain.current_drain == AgentDrain::Analysis {
            // Fall through to the fix analysis handling below
        } else if state.fix_prompt_prepared_pass != Some(state.reviewer_pass) {
            let prompt_mode = if state.continuation.fix_continue_pending
                || state.agent_chain.current_mode == crate::agents::DrainMode::Continuation
            {
                PromptMode::Continuation
            } else {
                PromptMode::Normal
            };
            return Effect::PrepareFixPrompt {
                pass: state.reviewer_pass,
                prompt_mode,
            };
        }

        if state.fix_required_files_cleaned_pass != Some(state.reviewer_pass) {
            return Effect::CleanupRequiredFiles {
                files: REQUIRED_FILES_FIX.iter().map(ToString::to_string).collect(),
            };
        }

        // BUG FIX: Handle the case where InvocationSucceeded was processed but
        // fix_agent_invoked_pass wasn't set yet. This mirrors the development
        // phase fix in development.rs:82-95.
        let last_effect_was_fix_agent = state
            .continuation
            .last_effect_kind
            .as_deref()
            .is_some_and(|k| k.contains("InvokeFixAgent"));

        // Use runtime_drain() because Fix flow uses AgentRole::Reviewer which sets
        // current_drain to Review, not Fix. The runtime_drain() correctly returns
        // Fix when fix_drain_active() is true.
        let effective_fix_agent_invoked = state.fix_agent_invoked_pass == Some(state.reviewer_pass)
            || (last_effect_was_fix_agent
                && state.fix_prompt_prepared_pass == Some(state.reviewer_pass)
                && state.fix_required_files_cleaned_pass == Some(state.reviewer_pass)
                && state.runtime_drain() == AgentDrain::Fix
                && state.agent_chain.current_mode == DrainMode::Normal);

        if !effective_fix_agent_invoked {
            return Effect::InvokeFixAgent {
                pass: state.reviewer_pass,
            };
        }

        // Fix analysis: after fix agent completes, invoke analysis agent to verify the fix
        // This mirrors the development analysis step
        if effective_fix_agent_invoked
            && state.fix_analysis_agent_invoked_pass != Some(state.reviewer_pass)
        {
            // First, initialize the analysis drain
            if state.agent_chain.current_drain != AgentDrain::Analysis {
                return Effect::InitializeAgentChain {
                    drain: AgentDrain::Analysis,
                };
            }
            // Then invoke the fix analysis agent
            return Effect::InvokeFixAnalysisAgent {
                pass: state.reviewer_pass,
            };
        }

        if state.fix_result_xml_extracted_pass != Some(state.reviewer_pass) {
            return Effect::ExtractFixResultXml {
                pass: state.reviewer_pass,
            };
        }

        let fix_validated_is_for_pass = state
            .fix_validated_outcome
            .as_ref()
            .is_some_and(|o| o.pass == state.reviewer_pass);
        if !fix_validated_is_for_pass {
            return Effect::ValidateFixResultXml {
                pass: state.reviewer_pass,
            };
        }

        if state.fix_result_xml_archived_pass != Some(state.reviewer_pass) {
            return Effect::ArchiveFixResultXml {
                pass: state.reviewer_pass,
            };
        }

        // Check if recovery state is active and fix completed successfully
        if crate::reducer::orchestration::is_recovery_state_active(state)
            && state.fix_result_xml_archived_pass == Some(state.reviewer_pass)
        {
            // Recovery succeeded - emit RecoverySucceeded before applying outcome
            return Effect::EmitRecoverySuccess {
                level: state.recovery_escalation_level,
                total_attempts: state.dev_fix_attempt_count,
            };
        }

        return Effect::ApplyFixOutcome {
            pass: state.reviewer_pass,
        };

        // Legacy super-effect placeholder. Removed once the fix chain is complete.
    }

    if state.agent_chain.agents.is_empty()
        || !state.agent_chain.matches_runtime_drain(AgentDrain::Review)
    {
        return Effect::InitializeAgentChain {
            drain: AgentDrain::Review,
        };
    }

    let consumer_signature_sha256 = state.agent_chain.consumer_signature_sha256();

    // Otherwise, run next review pass or complete phase.
    // Review pass boundary check: At reviewer_pass == total_reviewer_passes, still need to
    // process the current pass (either run it if not started, or apply its outcome if complete).
    // On resume, progress flags are reset to None (pipeline.rs:453-532), so orchestration
    // will derive the appropriate step. Only skip to SaveCheckpoint when:
    // - reviewer_pass > total_reviewer_passes (should not happen in normal flow), or
    // - total_reviewer_passes == 0 (no review passes configured, transition immediately)
    let review_pass_needs_work = state.reviewer_pass < state.total_reviewer_passes
        || (state.reviewer_pass == state.total_reviewer_passes && state.total_reviewer_passes > 0);

    if review_pass_needs_work {
        if state.review_context_prepared_pass != Some(state.reviewer_pass) {
            return Effect::PrepareReviewContext {
                pass: state.reviewer_pass,
            };
        }

        if state.review_prompt_prepared_pass != Some(state.reviewer_pass) {
            let review_inputs_materialized_for_pass =
                state.prompt_inputs.review.as_ref().is_some_and(|p| {
                    p.pass == state.reviewer_pass
                        && p.plan.consumer_signature_sha256 == consumer_signature_sha256
                        && p.diff.consumer_signature_sha256 == consumer_signature_sha256
                });
            if !review_inputs_materialized_for_pass {
                return Effect::MaterializeReviewInputs {
                    pass: state.reviewer_pass,
                };
            }
            return Effect::PrepareReviewPrompt {
                pass: state.reviewer_pass,
                prompt_mode: PromptMode::Normal,
            };
        }

        if state.review_required_files_cleaned_pass != Some(state.reviewer_pass) {
            return Effect::CleanupRequiredFiles {
                files: REQUIRED_FILES_ISSUES
                    .iter()
                    .map(ToString::to_string)
                    .collect(),
            };
        }

        if state.review_agent_invoked_pass != Some(state.reviewer_pass) {
            return Effect::InvokeReviewAgent {
                pass: state.reviewer_pass,
            };
        }

        if state.review_issues_xml_extracted_pass != Some(state.reviewer_pass) {
            return Effect::ExtractReviewIssuesXml {
                pass: state.reviewer_pass,
            };
        }

        let review_validated_is_for_pass = state
            .review_validated_outcome
            .as_ref()
            .is_some_and(|o| o.pass == state.reviewer_pass);
        if !review_validated_is_for_pass {
            return Effect::ValidateReviewIssuesXml {
                pass: state.reviewer_pass,
            };
        }

        if state.review_issues_markdown_written_pass != Some(state.reviewer_pass) {
            return Effect::WriteIssuesMarkdown {
                pass: state.reviewer_pass,
            };
        }

        if state.review_issue_snippets_extracted_pass != Some(state.reviewer_pass) {
            return Effect::ExtractReviewIssueSnippets {
                pass: state.reviewer_pass,
            };
        }

        if state.review_issues_xml_archived_pass != Some(state.reviewer_pass) {
            return Effect::ArchiveReviewIssuesXml {
                pass: state.reviewer_pass,
            };
        }

        // Check if recovery state is active and review completed successfully
        if crate::reducer::orchestration::is_recovery_state_active(state)
            && state.review_issues_xml_archived_pass == Some(state.reviewer_pass)
        {
            // Recovery succeeded - emit RecoverySucceeded before applying outcome
            return Effect::EmitRecoverySuccess {
                level: state.recovery_escalation_level,
                total_attempts: state.dev_fix_attempt_count,
            };
        }

        let outcome = state.review_validated_outcome.as_ref();
        match outcome {
            Some(outcome) => Effect::ApplyReviewOutcome {
                pass: outcome.pass,
                issues_found: outcome.issues_found,
                clean_no_issues: outcome.clean_no_issues,
            },
            None => Effect::SaveCheckpoint {
                trigger: CheckpointTrigger::PhaseTransition,
            },
        }
    } else {
        Effect::SaveCheckpoint {
            trigger: CheckpointTrigger::PhaseTransition,
        }
    }
}
