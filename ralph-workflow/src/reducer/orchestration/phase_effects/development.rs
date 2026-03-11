//! Development phase orchestration.
//!
//! Pure orchestration: State → Effect, no I/O.
//!
//! Development phase workflow:
//! 1. Write continuation context (if pending from previous attempt)
//! 2. Initialize agent chain (Developer role)
//! 3. For each iteration (up to `total_iterations)`:
//!    a. Prepare development context
//!    b. Materialize development inputs (prompt + plan)
//!    c. Prepare development prompt (Normal or Continuation mode)
//!    d. Cleanup development XML
//!    e. Invoke development agent
//!    f. Initialize Analysis agent chain
//!    g. Invoke analysis agent (verifies git diff vs PLAN.md)
//!    h. Extract development XML
//!    i. Validate development XML
//!    j. Archive development XML
//!    k. Apply development outcome
//! 4. Save checkpoint (transition to Review)
//!
//! Iteration boundary handling:
//! - At iteration == `total_iterations`, still process the current iteration
//! - On resume, progress flags are reset (pipeline.rs:453-532)
//! - Only skip to `SaveCheckpoint` when:
//!   - iteration > `total_iterations` (abnormal: exceeds configured iterations)
//!   - `total_iterations` == 0 (no iterations configured)

use crate::agents::AgentDrain;
use crate::reducer::effect::{ContinuationContextData, Effect};
use crate::reducer::event::CheckpointTrigger;
use crate::reducer::state::{DevelopmentStatus, PipelineState, PromptMode};

/// Files that the development/analysis agents write.
///
/// These files are cleaned up before each development iteration to ensure
/// fresh output. The analysis agent writes to `.agent/tmp/development_result.xml`.
pub const REQUIRED_FILES: &[&str] = &[".agent/tmp/development_result.xml"];

pub(super) fn determine_development_effect(state: &PipelineState) -> Effect {
    if state.continuation.context_write_pending {
        let status = state
            .continuation
            .previous_status
            .unwrap_or(DevelopmentStatus::Failed);
        let summary = state
            .continuation
            .previous_summary
            .clone()
            .unwrap_or_default();
        let files_changed = state.continuation.previous_files_changed.clone();
        let next_steps = state.continuation.previous_next_steps.clone();

        return Effect::WriteContinuationContext(ContinuationContextData {
            iteration: state.iteration,
            attempt: state.continuation.continuation_attempt,
            status,
            summary,
            files_changed,
            next_steps,
        });
    }

    if state.agent_chain.agents.is_empty() {
        return Effect::InitializeAgentChain {
            drain: AgentDrain::Development,
            role: AgentDrain::Development.role(),
        };
    }

    // Development phase runs two distinct roles (Developer then Analysis). Ensure
    // we are on the developer chain before preparing/invoking the developer agent.
    if state.development_agent_invoked_iteration != Some(state.iteration)
        && state.agent_chain.current_drain != AgentDrain::Development
    {
        return Effect::InitializeAgentChain {
            drain: AgentDrain::Development,
            role: AgentDrain::Development.role(),
        };
    }

    let consumer_signature_sha256 = state.agent_chain.consumer_signature_sha256();

    // Iteration boundary check: At iteration == total_iterations, still need to process
    // the current iteration (either run it if not started, or apply its outcome if complete).
    // On resume, progress flags are reset to None (pipeline.rs:453-532), so orchestration
    // will derive the appropriate step. Only skip to SaveCheckpoint when:
    // - iteration > total_iterations (abnormal: exceeds configured iterations), or
    // - total_iterations == 0 (no iterations configured, transition immediately)
    let iteration_needs_work = state.iteration < state.total_iterations
        || (state.iteration == state.total_iterations && state.total_iterations > 0);

    if iteration_needs_work {
        if state.development_context_prepared_iteration != Some(state.iteration) {
            return Effect::PrepareDevelopmentContext {
                iteration: state.iteration,
            };
        }

        if state.development_prompt_prepared_iteration != Some(state.iteration) {
            let development_inputs_materialized_for_iteration =
                state.prompt_inputs.development.as_ref().is_some_and(|p| {
                    p.iteration == state.iteration
                        && p.prompt.consumer_signature_sha256 == consumer_signature_sha256
                        && p.plan.consumer_signature_sha256 == consumer_signature_sha256
                });
            if !development_inputs_materialized_for_iteration {
                return Effect::MaterializeDevelopmentInputs {
                    iteration: state.iteration,
                };
            }

            let prompt_mode = if state.continuation.is_continuation() {
                PromptMode::Continuation
            } else {
                PromptMode::Normal
            };
            return Effect::PrepareDevelopmentPrompt {
                iteration: state.iteration,
                prompt_mode,
            };
        }

        if state.development_required_files_cleaned_iteration != Some(state.iteration) {
            return Effect::CleanupRequiredFiles {
                files: REQUIRED_FILES.iter().map(ToString::to_string).collect(),
            };
        }

        if state.development_agent_invoked_iteration != Some(state.iteration) {
            return Effect::InvokeDevelopmentAgent {
                iteration: state.iteration,
            };
        }

        // After EVERY development iteration, invoke analysis agent to verify results
        // Analysis agent produces development_result.xml by comparing git diff vs PLAN.md
        // This runs AFTER InvokeDevelopmentAgent completes (checked via development_agent_invoked_iteration)
        // and BEFORE ExtractDevelopmentXml (checked via analysis_agent_invoked_iteration)
        if state.development_agent_invoked_iteration == Some(state.iteration)
            && state.analysis_agent_invoked_iteration != Some(state.iteration)
        {
            if state.agent_chain.current_drain != AgentDrain::Analysis {
                return Effect::InitializeAgentChain {
                    drain: AgentDrain::Analysis,
                    role: AgentDrain::Analysis.role(),
                };
            }
            return Effect::InvokeAnalysisAgent {
                iteration: state.iteration,
            };
        }

        if state.development_xml_extracted_iteration != Some(state.iteration) {
            return Effect::ExtractDevelopmentXml {
                iteration: state.iteration,
            };
        }

        let dev_validated_is_for_iteration = state
            .development_validated_outcome
            .as_ref()
            .is_some_and(|o| o.iteration == state.iteration);
        if !dev_validated_is_for_iteration {
            return Effect::ValidateDevelopmentXml {
                iteration: state.iteration,
            };
        }

        if state.development_xml_archived_iteration != Some(state.iteration) {
            return Effect::ArchiveDevelopmentXml {
                iteration: state.iteration,
            };
        }

        // Check if recovery state is active and development completed successfully
        if crate::reducer::orchestration::is_recovery_state_active(state)
            && state.development_xml_archived_iteration == Some(state.iteration)
        {
            // Recovery succeeded - emit RecoverySucceeded before applying outcome
            return Effect::EmitRecoverySuccess {
                level: state.recovery_escalation_level,
                total_attempts: state.dev_fix_attempt_count,
            };
        }

        Effect::ApplyDevelopmentOutcome {
            iteration: state.iteration,
        }
    } else {
        Effect::SaveCheckpoint {
            trigger: CheckpointTrigger::PhaseTransition,
        }
    }
}
