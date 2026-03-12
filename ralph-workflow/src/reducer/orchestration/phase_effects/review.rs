//! Review phase orchestration.
//!
//! Pure orchestration: State → Effect, no I/O.
//!
//! Review phase has two drain-owned modes:
//!
//! 1. Fix drain (`current_drain == Fix`):
//!    a. Initialize agent chain (Reviewer role)
//!    b. Prepare fix prompt
//!    c. Cleanup fix result XML
//!    d. Invoke fix agent
//!    e. Extract fix result XML
//!    f. Validate fix result XML
//!    g. Archive fix result XML
//!    h. Apply fix outcome
//!
//! 2. Review drain (`current_drain == Review`):
//!    For each review pass (up to `total_reviewer_passes)`:
//!    a. Initialize agent chain (Reviewer role)
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

use crate::agents::AgentDrain;
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
pub const REQUIRED_FILES_FIX: &[&str] = &[".agent/tmp/fix_result.xml"];

pub(super) fn determine_review_effect(state: &PipelineState) -> Effect {
    if state.agent_chain.current_drain == AgentDrain::Fix {
        if state.agent_chain.agents.is_empty() || state.agent_chain.current_drain != AgentDrain::Fix
        {
            return Effect::InitializeAgentChain {
                drain: AgentDrain::Fix,
            };
        }

        if state.fix_prompt_prepared_pass != Some(state.reviewer_pass) {
            return Effect::PrepareFixPrompt {
                pass: state.reviewer_pass,
                prompt_mode: PromptMode::Normal,
            };
        }

        if state.fix_required_files_cleaned_pass != Some(state.reviewer_pass) {
            return Effect::CleanupRequiredFiles {
                files: REQUIRED_FILES_FIX.iter().map(ToString::to_string).collect(),
            };
        }

        if state.fix_agent_invoked_pass != Some(state.reviewer_pass) {
            return Effect::InvokeFixAgent {
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

    if state.agent_chain.agents.is_empty() || state.agent_chain.current_drain != AgentDrain::Review
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

        let outcome = state
            .review_validated_outcome
            .as_ref()
            .expect("validated outcome should exist before applying review outcome");
        Effect::ApplyReviewOutcome {
            pass: outcome.pass,
            issues_found: outcome.issues_found,
            clean_no_issues: outcome.clean_no_issues,
        }
    } else {
        Effect::SaveCheckpoint {
            trigger: CheckpointTrigger::PhaseTransition,
        }
    }
}
