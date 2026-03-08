use crate::reducer::event::PromptInputEvent;
use crate::reducer::state::{
    MaterializedCommitInputs, MaterializedDevelopmentInputs, MaterializedPlanningInputs,
    MaterializedReviewInputs, MaterializedXsdRetryLastOutput, PipelineState,
};

pub fn reduce_prompt_input_event(state: PipelineState, event: PromptInputEvent) -> PipelineState {
    match event {
        PromptInputEvent::OversizeDetected { .. } => state,
        PromptInputEvent::PlanningInputsMaterialized { iteration, prompt } => PipelineState {
            prompt_inputs: crate::reducer::state::PromptInputsState {
                planning: Some(MaterializedPlanningInputs { iteration, prompt }),
                ..state.prompt_inputs
            },
            ..state
        },
        PromptInputEvent::DevelopmentInputsMaterialized {
            iteration,
            prompt,
            plan,
        } => PipelineState {
            prompt_inputs: crate::reducer::state::PromptInputsState {
                development: Some(MaterializedDevelopmentInputs {
                    iteration,
                    prompt,
                    plan,
                }),
                ..state.prompt_inputs
            },
            ..state
        },
        PromptInputEvent::ReviewInputsMaterialized { pass, plan, diff } => PipelineState {
            prompt_inputs: crate::reducer::state::PromptInputsState {
                review: Some(MaterializedReviewInputs { pass, plan, diff }),
                ..state.prompt_inputs
            },
            ..state
        },
        PromptInputEvent::CommitInputsMaterialized { attempt, diff } => PipelineState {
            prompt_inputs: crate::reducer::state::PromptInputsState {
                commit: Some(MaterializedCommitInputs { attempt, diff }),
                ..state.prompt_inputs
            },
            ..state
        },
        PromptInputEvent::XsdRetryLastOutputMaterialized {
            phase,
            scope_id,
            last_output,
        } => PipelineState {
            prompt_inputs: crate::reducer::state::PromptInputsState {
                xsd_retry_last_output: Some(MaterializedXsdRetryLastOutput {
                    phase,
                    scope_id,
                    last_output,
                }),
                ..state.prompt_inputs
            },
            ..state
        },
        PromptInputEvent::HandlerError { error, .. } => super::error::reduce_error(&state, &error),

        PromptInputEvent::PromptPermissionsLocked { warning } => PipelineState {
            prompt_permissions: crate::reducer::state::PromptPermissionsState {
                locked: true,
                restore_needed: true,
                restored: false,
                last_warning: warning,
            },
            ..state
        },
        PromptInputEvent::PromptPermissionsRestoreWarning { warning } => PipelineState {
            prompt_permissions: crate::reducer::state::PromptPermissionsState {
                last_warning: Some(warning),
                ..state.prompt_permissions
            },
            ..state
        },
        PromptInputEvent::TemplateRendered {
            phase: _,
            template_name: _,
            log,
        } => {
            // Store the substitution log for validation and observability
            let validation_failed = !log.is_complete();
            let unsubstituted = if validation_failed {
                log.unsubstituted.clone()
            } else {
                Vec::new()
            };
            PipelineState {
                last_substitution_log: Some(log),
                template_validation_failed: validation_failed,
                template_validation_unsubstituted: unsubstituted,
                ..state
            }
        }
        PromptInputEvent::PromptCaptured {
            key,
            content,
            content_id,
        } => {
            // Insert the captured prompt into the reducer-owned prompt history.
            //
            // This event is emitted by prompt preparation handlers when a fresh prompt
            // is generated (not replayed). The reducer inserts it here so subsequent
            // effects and resumed runs can replay the same prompt deterministically.
            //
            // Idempotency: if the key already exists (e.g., handler ran twice due to
            // retry without a new scope), the existing entry is preserved (do not overwrite).
            let entry = crate::prompts::PromptHistoryEntry { content, content_id };
            let mut new_history = state.prompt_history.clone();
            new_history.entry(key).or_insert(entry);
            PipelineState {
                prompt_history: new_history,
                ..state
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reducer::event::PipelineEvent;
    use crate::reducer::state::PipelineState;

    fn reduce(state: PipelineState, event: PipelineEvent) -> PipelineState {
        match event {
            PipelineEvent::PromptInput(e) => reduce_prompt_input_event(state, e),
            _ => panic!("unexpected event in test"),
        }
    }

    #[test]
    fn test_prompt_captured_adds_to_state_prompt_history() {
        let state = PipelineState::initial(1, 0);
        assert!(
            state.prompt_history.is_empty(),
            "initial state has no prompt history"
        );

        let new_state = reduce(
            state,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key: "planning_1".to_string(),
                content: "test planning prompt".to_string(),
                content_id: Some("sha256-abc".to_string()),
            }),
        );

        let entry = new_state
            .prompt_history
            .get("planning_1")
            .expect("entry must be present after PromptCaptured");

        assert_eq!(entry.content, "test planning prompt");
        assert_eq!(entry.content_id, Some("sha256-abc".to_string()));
    }

    #[test]
    fn test_prompt_captured_does_not_overwrite_existing_if_was_replayed() {
        // Simulate state where a prompt was already captured (replayed scenario).
        let mut state = PipelineState::initial(1, 0);
        state.prompt_history.insert(
            "planning_1".to_string(),
            crate::prompts::PromptHistoryEntry {
                content: "original prompt".to_string(),
                content_id: Some("sha256-original".to_string()),
            },
        );

        // Attempt to capture a different prompt with the same key (idempotency check).
        let new_state = reduce(
            state,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key: "planning_1".to_string(),
                content: "replacement prompt".to_string(),
                content_id: Some("sha256-new".to_string()),
            }),
        );

        let entry = new_state
            .prompt_history
            .get("planning_1")
            .expect("entry must still be present");

        // Must preserve the original — PromptCaptured is idempotent (or_insert semantics).
        assert_eq!(
            entry.content, "original prompt",
            "existing entry must not be overwritten by PromptCaptured"
        );
        assert_eq!(entry.content_id, Some("sha256-original".to_string()));
    }

    #[test]
    fn test_prompt_captured_with_no_content_id_stores_none() {
        let state = PipelineState::initial(1, 0);

        let new_state = reduce(
            state,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key: "development_1".to_string(),
                content: "dev prompt without id".to_string(),
                content_id: None,
            }),
        );

        let entry = new_state
            .prompt_history
            .get("development_1")
            .expect("entry must be present");

        assert_eq!(entry.content, "dev prompt without id");
        assert!(
            entry.content_id.is_none(),
            "content_id must be None when not provided"
        );
    }
}
