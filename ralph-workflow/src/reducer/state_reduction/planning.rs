// NOTE: split from reducer/state_reduction.rs.

use crate::agents::DrainMode;
use crate::reducer::event::PlanningEvent;
use crate::reducer::state::{
    ContinuationState, PipelineState, PlanningValidatedOutcome, PromptInputsState,
};

pub(super) fn reduce_planning_event(state: PipelineState, event: PlanningEvent) -> PipelineState {
    match event {
        PlanningEvent::PhaseStarted => PipelineState {
            phase: crate::reducer::event::PipelinePhase::Planning,
            agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
            planning_prompt_prepared_iteration: None,
            planning_required_files_cleaned_iteration: None,
            planning_agent_invoked_iteration: None,
            planning_xml_extracted_iteration: None,
            planning_validated_outcome: None,
            planning_markdown_written_iteration: None,
            planning_xml_archived_iteration: None,
            continuation: ContinuationState {
                invalid_output_attempts: 0,
                ..state.continuation
            },
            ..state
        },
        PlanningEvent::PhaseCompleted => PipelineState {
            phase: crate::reducer::event::PipelinePhase::Development,
            agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
            planning_prompt_prepared_iteration: None,
            planning_required_files_cleaned_iteration: None,
            planning_agent_invoked_iteration: None,
            planning_xml_extracted_iteration: None,
            planning_validated_outcome: None,
            planning_markdown_written_iteration: None,
            planning_xml_archived_iteration: None,
            continuation: ContinuationState {
                invalid_output_attempts: 0,
                ..state.continuation
            },
            ..state
        },
        PlanningEvent::PromptPrepared { iteration } => PipelineState {
            planning_prompt_prepared_iteration: Some(iteration),
            continuation: ContinuationState {
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                ..state.continuation
            },
            ..state
        },
        PlanningEvent::PlanXmlCleaned { iteration } => PipelineState {
            planning_required_files_cleaned_iteration: Some(iteration),
            ..state
        },
        PlanningEvent::AgentInvoked { iteration } => PipelineState {
            planning_agent_invoked_iteration: Some(iteration),
            continuation: ContinuationState {
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                ..state.continuation
            },
            ..state
        },
        PlanningEvent::PlanXmlExtracted { iteration } => PipelineState {
            planning_xml_extracted_iteration: Some(iteration),
            ..state
        },
        PlanningEvent::PlanXmlValidated {
            iteration,
            valid,
            markdown,
        } => PipelineState {
            planning_validated_outcome: Some(PlanningValidatedOutcome {
                iteration,
                valid,
                markdown,
            }),
            ..state
        },
        PlanningEvent::PlanMarkdownWritten { iteration } => PipelineState {
            planning_markdown_written_iteration: Some(iteration),
            // Writing PLAN.md updates the canonical plan content. Invalidate any
            // downstream materialized inputs that might have captured an older plan.
            prompt_inputs: PromptInputsState {
                development: None,
                review: None,
                ..state.prompt_inputs.clone()
            },
            ..state
        },
        PlanningEvent::PlanXmlArchived { iteration } => PipelineState {
            planning_xml_archived_iteration: Some(iteration),
            ..state
        },
        PlanningEvent::GenerationCompleted { valid, .. } => {
            if valid {
                PipelineState {
                    phase: crate::reducer::event::PipelinePhase::Development,
                    agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
                    planning_prompt_prepared_iteration: None,
                    planning_required_files_cleaned_iteration: None,
                    planning_agent_invoked_iteration: None,
                    planning_xml_extracted_iteration: None,
                    planning_validated_outcome: None,
                    planning_markdown_written_iteration: None,
                    planning_xml_archived_iteration: None,
                    continuation: ContinuationState {
                        invalid_output_attempts: 0,
                        ..state.continuation
                    },
                    ..state
                }
            } else {
                // Do not proceed to Development without a valid plan.
                PipelineState {
                    phase: crate::reducer::event::PipelinePhase::Planning,
                    agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
                    planning_prompt_prepared_iteration: None,
                    planning_required_files_cleaned_iteration: None,
                    planning_agent_invoked_iteration: None,
                    planning_xml_extracted_iteration: None,
                    planning_validated_outcome: None,
                    planning_markdown_written_iteration: None,
                    planning_xml_archived_iteration: None,
                    ..state
                }
            }
        }

        PlanningEvent::OutputValidationFailed { iteration, attempt }
        | PlanningEvent::PlanXmlMissing { iteration, attempt } => {
            // When a same-agent retry was pending (timeout/internal-error recovery) but the
            // agent still produced invalid output, switch to the next agent and reset the
            // invalid-output counter.  Otherwise stay on the current agent and increment the
            // counter so we can accumulate several failures before switching.
            if state.continuation.same_agent_retry_pending {
                let new_agent_chain = state.agent_chain.switch_to_next_agent().clear_session_id();
                PipelineState {
                    phase: crate::reducer::event::PipelinePhase::Planning,
                    iteration,
                    agent_chain: new_agent_chain.with_mode(DrainMode::Normal),
                    planning_prompt_prepared_iteration: None,
                    planning_required_files_cleaned_iteration: None,
                    planning_agent_invoked_iteration: None,
                    planning_xml_extracted_iteration: None,
                    planning_validated_outcome: None,
                    planning_markdown_written_iteration: None,
                    planning_xml_archived_iteration: None,
                    continuation: ContinuationState {
                        invalid_output_attempts: 0,
                        same_agent_retry_count: 0,
                        same_agent_retry_pending: false,
                        same_agent_retry_reason: None,
                        ..state.continuation
                    },
                    ..state
                }
            } else {
                PipelineState {
                    phase: crate::reducer::event::PipelinePhase::Planning,
                    iteration,
                    agent_chain: state.agent_chain.with_mode(DrainMode::Normal),
                    planning_prompt_prepared_iteration: None,
                    planning_required_files_cleaned_iteration: None,
                    planning_agent_invoked_iteration: None,
                    planning_xml_extracted_iteration: None,
                    planning_validated_outcome: None,
                    planning_markdown_written_iteration: None,
                    planning_xml_archived_iteration: None,
                    continuation: ContinuationState {
                        invalid_output_attempts: attempt.saturating_add(1),
                        ..state.continuation
                    },
                    ..state
                }
            }
        }
    }
}
