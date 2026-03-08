//! Error event reduction.
//!
//! Handles error events returned through `Err()` from effect handlers.
//! Each error type has a specific recovery strategy decided by the reducer.

use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelinePhase;
use crate::reducer::state::PipelineState;

fn compute_failed_phase_for_recovery(
    state: &PipelineState,
    explicit_failed_phase: Option<PipelinePhase>,
) -> PipelinePhase {
    if let Some(phase) = explicit_failed_phase {
        return phase;
    }

    if state.phase == PipelinePhase::AwaitingDevFix {
        // Errors can occur while executing AwaitingDevFix effects (marker emission,
        // dev-fix invocation, etc.). Never clobber the recovery target to AwaitingDevFix.
        return state
            .failed_phase_for_recovery
            .or(state.previous_phase)
            .unwrap_or(PipelinePhase::Development);
    }

    state.phase
}

fn route_to_awaiting_dev_fix(
    state: &PipelineState,
    explicit_failed_phase: Option<PipelinePhase>,
) -> PipelineState {
    let failed_phase_for_recovery = compute_failed_phase_for_recovery(state, explicit_failed_phase);
    let in_recovery_loop = state.phase == PipelinePhase::AwaitingDevFix
        || state.previous_phase == Some(PipelinePhase::AwaitingDevFix);

    let mut new_state = state.clone();
    new_state.previous_phase = Some(state.phase);
    new_state.phase = PipelinePhase::AwaitingDevFix;
    new_state.dev_fix_triggered = false;
    new_state.failed_phase_for_recovery = Some(failed_phase_for_recovery);

    if !in_recovery_loop {
        new_state.dev_fix_attempt_count = 0;
        new_state.recovery_escalation_level = 0;
    }

    new_state
}

/// Reduce error events.
///
/// Error events are processed through the reducer identically to success events.
/// The reducer decides the recovery strategy based on the error type.
///
/// # Recovery Strategies
///
/// - **Continuation not supported errors**: Invariant violations indicating continuation
///   mode was incorrectly passed to a phase that doesn't support it. The reducer routes
///   through `PipelinePhase::AwaitingDevFix` so unattended execution can dispatch dev-fix
///   and keep the pipeline non-terminating.
///
/// - **Missing inputs errors**: Effect sequencing bugs where a handler was called without
///   required preconditions being met. These are routed through `PipelinePhase::AwaitingDevFix`
///   so unattended remediation can proceed instead of terminating early.
pub(super) fn reduce_error(state: &PipelineState, error: &ErrorEvent) -> PipelineState {
    match error {
        ErrorEvent::UserInterruptRequested => {
            // External termination request: transition to Interrupted so orchestration
            // can restore PROMPT.md permissions and persist a checkpoint deterministically.
            let mut new_state = state.clone();
            new_state.previous_phase = Some(state.phase);
            new_state.phase = PipelinePhase::Interrupted;
            new_state.interrupted_by_user = true;

            // If we were in a termination sub-flow, clear marker intent. Ctrl+C is not
            // a programmatic completion marker termination path.
            new_state.completion_marker_pending = false;
            new_state.completion_marker_is_failure = false;
            new_state.completion_marker_reason = None;
            new_state
        }

        // Continuation not supported errors are invariant violations
        ErrorEvent::PlanningContinuationNotSupported
        | ErrorEvent::ReviewContinuationNotSupported
        | ErrorEvent::FixContinuationNotSupported
        | ErrorEvent::CommitContinuationNotSupported => {
            // Invariant violations: route through AwaitingDevFix so unattended orchestration
            // always emits a completion marker and dispatches dev-fix, rather than terminating.
            route_to_awaiting_dev_fix(state, None)
        }

        // Missing inputs are handler bugs - route through AwaitingDevFix for remediation.
        ErrorEvent::ReviewInputsNotMaterialized { .. }
        | ErrorEvent::PlanningInputsNotMaterialized { .. }
        | ErrorEvent::DevelopmentInputsNotMaterialized { .. }
        | ErrorEvent::CommitInputsNotMaterialized { .. }
        | ErrorEvent::CommitAgentNotInitialized { .. }
        | ErrorEvent::ValidatedPlanningMarkdownMissing { .. }
        | ErrorEvent::ValidatedDevelopmentOutcomeMissing { .. }
        | ErrorEvent::ValidatedReviewOutcomeMissing { .. }
        | ErrorEvent::ValidatedFixOutcomeMissing { .. }
        | ErrorEvent::ValidatedCommitOutcomeMissing { .. } => {
            // Invariant violations: route through AwaitingDevFix so the pipeline never
            // exits early and a completion marker is reliably written.
            route_to_awaiting_dev_fix(state, None)
        }

        // Fix prompt file missing is recoverable - tmp artifacts can be cleaned between checkpoints.
        // Clear the "prepared" flag so orchestration re-runs PrepareFixPrompt.
        ErrorEvent::FixPromptMissing => {
            let mut new_state = state.clone();
            new_state.fix_prompt_prepared_pass = None;
            new_state
        }

        // Unknown agent lookup is recoverable - advance the agent chain to preserve
        // unattended-mode fallback behavior.
        ErrorEvent::AgentNotFound { .. } => {
            let mut new_state = state.clone();
            new_state.agent_chain = state.agent_chain.switch_to_next_agent().clear_session_id();
            new_state.continuation = crate::reducer::state::ContinuationState {
                xsd_retry_count: 0,
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                same_agent_retry_count: 0,
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                ..state.continuation.clone()
            };
            new_state
        }

        // Missing prompt files are recoverable - tmp artifacts can be cleaned between checkpoints.
        // Clear the corresponding "prepared" flag so the event loop will regenerate the prompt.
        ErrorEvent::PlanningPromptMissing { .. } => {
            let mut new_state = state.clone();
            new_state.planning_prompt_prepared_iteration = None;
            new_state
        }
        ErrorEvent::DevelopmentPromptMissing { .. } => {
            let mut new_state = state.clone();
            new_state.development_prompt_prepared_iteration = None;
            new_state
        }
        ErrorEvent::ReviewPromptMissing { .. } => {
            let mut new_state = state.clone();
            new_state.review_prompt_prepared_pass = None;
            new_state
        }
        ErrorEvent::CommitPromptMissing { .. } => {
            let mut new_state = state.clone();
            new_state.commit_prompt_prepared = false;
            new_state
        }

        // Workspace/Git operation failures must not cause early pipeline termination.
        // Route these through AwaitingDevFix so TriggerDevFixFlow writes the completion marker
        // and unattended orchestration can reliably detect completion.
        ErrorEvent::WorkspaceReadFailed { .. }
        | ErrorEvent::WorkspaceWriteFailed { .. }
        | ErrorEvent::WorkspaceCreateDirAllFailed { .. }
        | ErrorEvent::WorkspaceRemoveFailed { .. }
        | ErrorEvent::GitAddAllFailed { .. }
        | ErrorEvent::GitStatusFailed { .. } => route_to_awaiting_dev_fix(state, None),

        // Agent chain exhausted - transition to AwaitingDevFix for remediation attempt
        // instead of immediately terminating
        ErrorEvent::AgentChainExhausted { phase, .. } => {
            // Transition to AwaitingDevFix phase
            // This signals orchestration to invoke the development agent to diagnose
            // and fix the pipeline failure before deciding whether to proceed or terminate
            route_to_awaiting_dev_fix(state, Some(*phase))
        }
    }
}

#[cfg(test)]
#[path = "error/tests.rs"]
mod tests;
