use crate::reducer::event::PromptInputEvent;
use crate::reducer::state::{
    MaterializedCommitInputs, MaterializedDevelopmentInputs, MaterializedPlanningInputs,
    MaterializedReviewInputs, MaterializedXsdRetryLastOutput, PipelineState,
};

pub(super) fn reduce_prompt_input_event(
    state: PipelineState,
    event: PromptInputEvent,
) -> PipelineState {
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

        PromptInputEvent::PromptPermissionsLocked { warning } => {
            // If permission lock failed (warning present), do not claim the prompt is locked and
            // do not schedule a restore at exit.
            let locked = warning.is_none();
            PipelineState {
                prompt_permissions: crate::reducer::state::PromptPermissionsState {
                    locked,
                    restore_needed: locked,
                    restored: false,
                    last_warning: warning,
                },
                ..state
            }
        }
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
            // PromptCaptured is authoritative for the given key.
            //
            // This event is emitted by handlers when a prompt was freshly generated and
            // used for an effect attempt. Reducer-owned `prompt_history` must reflect the
            // prompt that was actually used so resume can replay deterministically.
            //
            // Many capture sites legitimately emit `content_id: None` (legacy / not yet
            // wired). In that case we still overwrite when content differs, otherwise a
            // checkpoint can retain stale prompt content under the same key.
            let entry = crate::prompts::PromptHistoryEntry {
                content,
                content_id,
            };

            // Functional style: build new HashMap from iterator, avoiding let mut
            // First, determine the final entry value
            let final_entry = match state.prompt_history.get(&key) {
                None => entry,
                Some(existing) => {
                    // Preserve richer metadata: do not downgrade an existing content-id to None
                    // when the prompt content itself is identical.
                    let is_same_content = existing.content == entry.content;
                    let would_downgrade_id =
                        existing.content_id.is_some() && entry.content_id.is_none();
                    let is_exact_same = existing.content == entry.content
                        && existing.content_id == entry.content_id;

                    if is_same_content && would_downgrade_id {
                        // Keep existing.
                        existing.clone()
                    } else if is_exact_same {
                        // Idempotent no-op.
                        existing.clone()
                    } else {
                        entry
                    }
                }
            };

            // Build new HashMap: iterate existing entries, then add/update the new entry
            let new_history = state
                .prompt_history
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .chain(std::iter::once((key, final_entry)))
                .collect();

            PipelineState {
                prompt_history: new_history,
                ..state
            }
        }
        PromptInputEvent::GitignoreEntriesEnsured { .. } => {
            // Set flag to prevent re-running effect
            PipelineState {
                gitignore_entries_ensured: true,
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

    pub(crate) fn reduce(state: PipelineState, event: PipelineEvent) -> PipelineState {
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
    fn test_prompt_captured_overwrites_existing_when_content_differs_even_if_content_id_same() {
        let state = PipelineState::initial(1, 0);
        let history = state
            .prompt_history
            .into_iter()
            .chain(std::iter::once((
                "planning_1".to_string(),
                crate::prompts::PromptHistoryEntry {
                    content: "original prompt".to_string(),
                    content_id: Some("sha256-same".to_string()),
                },
            )))
            .collect::<std::collections::HashMap<_, _>>();
        let state = PipelineState {
            prompt_history: history,
            ..state
        };

        let new_state = reduce(
            state,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key: "planning_1".to_string(),
                content: "replacement prompt".to_string(),
                content_id: Some("sha256-same".to_string()),
            }),
        );

        let entry = new_state
            .prompt_history
            .get("planning_1")
            .expect("entry must still be present");

        assert_eq!(entry.content, "replacement prompt");
        assert_eq!(entry.content_id, Some("sha256-same".to_string()));
    }

    #[test]
    fn test_prompt_captured_does_not_downgrade_existing_content_id_when_content_is_identical() {
        let state = PipelineState::initial(1, 0);
        let history = state
            .prompt_history
            .into_iter()
            .chain(std::iter::once((
                "planning_1".to_string(),
                crate::prompts::PromptHistoryEntry {
                    content: "same prompt".to_string(),
                    content_id: Some("sha256-keep".to_string()),
                },
            )))
            .collect::<std::collections::HashMap<_, _>>();
        let state = PipelineState {
            prompt_history: history,
            ..state
        };

        let new_state = reduce(
            state,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key: "planning_1".to_string(),
                content: "same prompt".to_string(),
                content_id: None,
            }),
        );

        let entry = new_state
            .prompt_history
            .get("planning_1")
            .expect("entry must be present");
        assert_eq!(entry.content, "same prompt");
        assert_eq!(entry.content_id.as_deref(), Some("sha256-keep"));
    }

    #[test]
    fn test_prompt_captured_overwrites_existing_when_content_id_differs() {
        let state = PipelineState::initial(1, 0);
        let history = state
            .prompt_history
            .into_iter()
            .chain(std::iter::once((
                "planning_1".to_string(),
                crate::prompts::PromptHistoryEntry {
                    content: "stale prompt".to_string(),
                    content_id: Some("sha256-old".to_string()),
                },
            )))
            .collect::<std::collections::HashMap<_, _>>();
        let state = PipelineState {
            prompt_history: history,
            ..state
        };

        let new_state = reduce(
            state,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key: "planning_1".to_string(),
                content: "fresh prompt".to_string(),
                content_id: Some("sha256-new".to_string()),
            }),
        );

        let entry = new_state
            .prompt_history
            .get("planning_1")
            .expect("entry must be present");

        assert_eq!(entry.content, "fresh prompt");
        assert_eq!(entry.content_id.as_deref(), Some("sha256-new"));
    }

    #[test]
    fn test_prompt_captured_overwrites_existing_when_incoming_has_no_content_id_and_content_differs(
    ) {
        // Regression: many handlers emit PromptCaptured with content_id=None.
        // If a prompt is regenerated under the same key with different content, the
        // reducer must treat the new capture as authoritative so resume replays the
        // prompt that was actually used.
        let state = PipelineState::initial(1, 0);
        let history = state
            .prompt_history
            .into_iter()
            .chain(std::iter::once((
                "planning_1".to_string(),
                crate::prompts::PromptHistoryEntry {
                    content: "stale prompt".to_string(),
                    content_id: None,
                },
            )))
            .collect::<std::collections::HashMap<_, _>>();
        let state = PipelineState {
            prompt_history: history,
            ..state
        };

        let new_state = reduce(
            state,
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key: "planning_1".to_string(),
                content: "fresh prompt".to_string(),
                content_id: None,
            }),
        );

        let entry = new_state
            .prompt_history
            .get("planning_1")
            .expect("entry must be present");
        assert_eq!(entry.content, "fresh prompt");
        assert!(entry.content_id.is_none());
    }
}
