//! Planning phase orchestration.
//!
//! Pure orchestration: State → Effect, no I/O.
//!
//! Planning phase workflow:
//! 1. Save checkpoint at iteration 0 (after rebase completes)
//! 2. Initialize the planning drain chain
//! 3. Ensure gitignore entries (.agent/, PROMPT*)
//! 4. Cleanup context (remove old PLAN.md from previous iteration)
//! 5. Materialize planning inputs (prompt template)
//! 6. Prepare planning prompt
//! 7. Cleanup planning XML
//! 8. Invoke planning agent
//! 9. Extract planning XML
//! 10. Validate planning XML
//! 11. Write planning markdown (PLAN.md)
//! 12. Archive planning XML
//! 13. Apply planning outcome (transition to Development)

use crate::agents::AgentDrain;
use crate::reducer::effect::Effect;
use crate::reducer::event::CheckpointTrigger;
use crate::reducer::state::{PipelineState, PromptMode, RebaseState};

/// Files that the planning agent writes.
///
/// These files are cleaned up before each planning agent invocation to ensure
/// fresh output. The planning agent writes to `.agent/tmp/plan.xml`.
pub(super) const REQUIRED_FILES: &[&str] = &[".agent/tmp/plan.xml"];

pub(super) fn determine_planning_effect(state: &PipelineState) -> Effect {
    if state.iteration == 0
        && state.checkpoint_saved_count == 0
        && matches!(
            state.rebase,
            RebaseState::Skipped | RebaseState::Completed { .. }
        )
    {
        return Effect::SaveCheckpoint {
            trigger: CheckpointTrigger::PhaseTransition,
        };
    }

    let planning_chain_ready = state
        .agent_chain
        .matches_runtime_drain(AgentDrain::Planning);

    if state.agent_chain.agents.is_empty() || !planning_chain_ready {
        return Effect::InitializeAgentChain {
            drain: AgentDrain::Planning,
        };
    }

    // Ensure gitignore entries BEFORE cleanup
    if !state.gitignore_entries_ensured {
        return Effect::EnsureGitignoreEntries;
    }

    let consumer_signature_sha256 = state.agent_chain.consumer_signature_sha256();

    // Clean up BEFORE planning to remove old PLAN.md from previous iteration
    if !state.context_cleaned {
        return Effect::CleanupContext;
    }

    if state.planning_prompt_prepared_iteration != Some(state.iteration) {
        let planning_inputs_materialized_for_iteration =
            state.prompt_inputs.planning.as_ref().is_some_and(|p| {
                p.iteration == state.iteration
                    && p.prompt.consumer_signature_sha256 == consumer_signature_sha256
            });
        if !planning_inputs_materialized_for_iteration {
            return Effect::MaterializePlanningInputs {
                iteration: state.iteration,
            };
        }
        return Effect::PreparePlanningPrompt {
            iteration: state.iteration,
            prompt_mode: PromptMode::Normal,
        };
    }

    if state.planning_required_files_cleaned_iteration != Some(state.iteration) {
        return Effect::CleanupRequiredFiles {
            files: REQUIRED_FILES.iter().map(ToString::to_string).collect(),
        };
    }

    if state.planning_agent_invoked_iteration != Some(state.iteration) {
        return Effect::InvokePlanningAgent {
            iteration: state.iteration,
        };
    }

    if state.planning_xml_extracted_iteration != Some(state.iteration) {
        return Effect::ExtractPlanningXml {
            iteration: state.iteration,
        };
    }

    let planning_validated_is_for_iteration = state
        .planning_validated_outcome
        .as_ref()
        .is_some_and(|o| o.iteration == state.iteration);
    if !planning_validated_is_for_iteration {
        return Effect::ValidatePlanningXml {
            iteration: state.iteration,
        };
    }

    // Phase 4: If parallel plan is present, evaluate or dispatch based on validation state
    if let Some(ref parallel_plan) = state.parallel_plan {
        if state.parallel_plan_validated {
            // Plan has been validated - dispatch workers
            return Effect::DispatchParallelWorkers {
                plan: parallel_plan.clone(),
            };
        } else {
            // Plan needs evaluation
            return Effect::EvaluateParallelPlan {
                plan: parallel_plan.clone(),
            };
        }
    }

    if state.planning_markdown_written_iteration != Some(state.iteration) {
        return Effect::WritePlanningMarkdown {
            iteration: state.iteration,
        };
    }

    if state.planning_xml_archived_iteration != Some(state.iteration) {
        return Effect::ArchivePlanningXml {
            iteration: state.iteration,
        };
    }

    // Check if recovery state is active and planning completed successfully
    if crate::reducer::orchestration::is_recovery_state_active(state)
        && state.planning_xml_archived_iteration == Some(state.iteration)
    {
        // Recovery succeeded - emit RecoverySucceeded before applying outcome
        return Effect::EmitRecoverySuccess {
            level: state.recovery_escalation_level,
            total_attempts: state.dev_fix_attempt_count,
        };
    }

    let outcome = state
        .planning_validated_outcome
        .as_ref()
        .expect("validated outcome should exist before applying planning outcome");
    Effect::ApplyPlanningOutcome {
        iteration: outcome.iteration,
        valid: outcome.valid,
    }
}
