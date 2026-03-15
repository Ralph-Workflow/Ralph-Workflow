//! Review phase reducer.
//!
//! Pure state reduction for review and fix phases of the pipeline.
//! Handles events from issue detection, review validation, and fix application.
//!
//! ## Reducer Contract
//!
//! All functions in this module are PURE:
//! - No I/O (filesystem, network, process execution)
//! - No side effects (logging, environment variables, time)
//! - Deterministic: same state + event = same result
//!
//! ## Architecture
//!
//! The review phase uses a two-pass system:
//! 1. Review pass: Detect issues and validate XML output
//! 2. Fix pass: Apply fixes based on review feedback
//!
//! State transitions are driven entirely by events from handlers.
//!
//! ## Module Organization
//!
//! - `review_reducer`: Handles review pass events (issue detection, validation, pass completion)
//! - `fix_reducer`: Handles fix attempt events (fix application, continuation, budget exhaustion)
//! - `helpers`: Pure helper functions for state transitions
//!
//! See `docs/architecture/event-loop-and-reducers.md` for details on the reducer architecture.

mod fix_reducer;
mod review_reducer;

use crate::reducer::event::ReviewEvent;
use crate::reducer::state::PipelineState;

pub(super) fn reduce_review_event(state: PipelineState, event: ReviewEvent) -> PipelineState {
    use ReviewEvent::{
        AgentInvoked, Completed, ContextPrepared, FixAgentInvoked, FixAnalysisAgentInvoked,
        FixAttemptCompleted, FixAttemptStarted, FixContinuationBudgetExhausted,
        FixContinuationSucceeded, FixContinuationTriggered, FixOutcomeApplied,
        FixOutputValidationFailed, FixPromptPrepared, FixResultXmlArchived, FixResultXmlCleaned,
        FixResultXmlExtracted, FixResultXmlMissing, FixResultXmlValidated, IssueSnippetsExtracted,
        IssuesMarkdownWritten, IssuesXmlArchived, IssuesXmlCleaned, IssuesXmlExtracted,
        IssuesXmlMissing, IssuesXmlValidated, OutputValidationFailed, PassCompletedClean,
        PassStarted, PhaseCompleted, PhaseStarted, PromptPrepared,
    };

    match event {
        // Review pass events
        PhaseStarted => review_reducer::reduce_phase_started(state),
        PassStarted { pass } => review_reducer::reduce_pass_started(state, pass),
        ContextPrepared { pass } => review_reducer::reduce_context_prepared(state, pass),
        PromptPrepared { pass } => review_reducer::reduce_prompt_prepared(state, pass),
        IssuesXmlCleaned { pass } => review_reducer::reduce_issues_xml_cleaned(state, pass),
        AgentInvoked { pass } => review_reducer::reduce_agent_invoked(state, pass),
        IssuesXmlExtracted { pass } => review_reducer::reduce_issues_xml_extracted(state, pass),
        IssuesXmlValidated {
            pass,
            issues_found,
            clean_no_issues,
            issues,
            no_issues_found,
        } => review_reducer::reduce_issues_xml_validated(
            state,
            pass,
            issues_found,
            clean_no_issues,
            issues,
            no_issues_found,
        ),
        IssuesMarkdownWritten { pass } => {
            review_reducer::reduce_issues_markdown_written(state, pass)
        }
        IssueSnippetsExtracted { pass } => {
            review_reducer::reduce_issue_snippets_extracted(state, pass)
        }
        IssuesXmlArchived { pass } => review_reducer::reduce_issues_xml_archived(state, pass),
        Completed { pass, issues_found } => {
            review_reducer::reduce_completed(state, pass, issues_found)
        }
        PhaseCompleted { .. } => review_reducer::reduce_phase_completed(state),
        PassCompletedClean { pass } => review_reducer::reduce_pass_completed_clean(state, pass),
        OutputValidationFailed {
            pass,
            attempt,
            error_detail,
        }
        | IssuesXmlMissing {
            pass,
            attempt,
            error_detail,
        } => review_reducer::reduce_output_validation_failed(state, pass, attempt, error_detail),

        // Fix attempt events
        FixAttemptStarted { .. } => fix_reducer::reduce_fix_attempt_started(state),
        FixPromptPrepared { pass } => fix_reducer::reduce_fix_prompt_prepared(state, pass),
        FixResultXmlCleaned { pass } => fix_reducer::reduce_fix_result_xml_cleaned(state, pass),
        FixAgentInvoked { pass } => fix_reducer::reduce_fix_agent_invoked(state, pass),
        FixAnalysisAgentInvoked { pass } => {
            fix_reducer::reduce_fix_analysis_agent_invoked(state, pass)
        }
        FixResultXmlExtracted { pass } => fix_reducer::reduce_fix_result_xml_extracted(state, pass),
        FixResultXmlValidated {
            pass,
            status,
            summary,
        } => fix_reducer::reduce_fix_result_xml_validated(state, pass, status, summary),
        FixResultXmlArchived { pass } => fix_reducer::reduce_fix_result_xml_archived(state, pass),
        FixOutcomeApplied { pass } => fix_reducer::reduce_fix_outcome_applied(state, pass),
        FixAttemptCompleted { pass, changes_made } => {
            fix_reducer::reduce_fix_attempt_completed(state, pass, changes_made)
        }
        FixContinuationTriggered {
            pass,
            status,
            summary,
        } => fix_reducer::reduce_fix_continuation_triggered(state, pass, status, summary),
        FixContinuationSucceeded {
            pass,
            total_attempts,
        } => fix_reducer::reduce_fix_continuation_succeeded(state, pass, total_attempts),
        FixContinuationBudgetExhausted {
            pass,
            total_attempts,
            last_status,
        } => fix_reducer::reduce_fix_continuation_budget_exhausted(
            state,
            pass,
            total_attempts,
            last_status,
        ),
        FixOutputValidationFailed {
            pass,
            attempt,
            error_detail,
        }
        | FixResultXmlMissing {
            pass,
            attempt,
            error_detail,
        } => fix_reducer::reduce_fix_output_validation_failed(state, pass, attempt, error_detail),
    }
}
