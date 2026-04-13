//! Commit phase effect-to-event mapping.
//!
//! This module handles effect execution for the Commit phase of the pipeline.
//! Commit involves generating a commit message and creating the git commit.
//!
//! ## Commit Phase Flow
//!
//! 1. **`CheckCommitDiff`** - Verify there are changes to commit
//! 2. **`MaterializeCommitInputs`** - Prepare diff input for commit agent
//! 3. **`PrepareCommitPrompt`** - Generate commit prompt
//! 4. **`InvokeCommitAgent`** - Execute commit agent
//! 5. **`CleanupRequiredFiles`** - Clean any existing XML (handled in `lifecycle_effects`)
//! 6. **`ExtractCommitXml`** - Extract XML from agent output
//! 7. **`ValidateCommitXml`** - Validate and parse commit message
//! 8. **`ApplyCommitMessageOutcome`** - Apply commit message to state
//! 9. **`ArchiveCommitXml`** - Archive XML
//! 10. **`CreateCommit`** or **`SkipCommit`** - Create git commit or skip if no changes
//!
//! ## Rebase Support
//!
//! Before commit, the pipeline may rebase onto a target branch:
//! - **`RunRebase`** - Rebase onto target branch
//! - **`ResolveRebaseConflicts`** - Resolve conflicts if rebase fails
//!
//! ## Mock Behavior
//!
//! - Mock always returns a valid commit message
//! - **`CheckCommitDiff`** can be configured to simulate empty diff (for testing skip logic)
//! - **`CreateCommit`** returns a fake commit hash
//! - **`RunRebase`** always succeeds with a fake head OID

use crate::prompts::prompt_scope_key::{PromptScopeKey, RetryMode};
use crate::reducer::effect::Effect;
use crate::reducer::event::{PipelineEvent, PipelinePhase};
use crate::reducer::state::CommitState;
use crate::reducer::ui_event::{UIEvent, XmlOutputType};

use super::super::MockEffectHandler;

impl MockEffectHandler {
    /// Handle commit phase effects.
    ///
    /// Returns appropriate mock events for each commit effect without
    /// performing real agent execution, XML validation, or git operations.
    pub(super) fn handle_commit_effect(
        &self,
        effect: Effect,
    ) -> Option<(PipelineEvent, Vec<UIEvent>)> {
        // NOTE: `Effect::CheckCommitDiff` is handled in the workspace-backed mock
        // handler implementation (`mock_effect_handler/handler.rs`) so it can write
        // the diff to `.agent/tmp/commit_diff.txt` and compute a real sha256 content id.
        // Do not map it here.
        match effect {
            Effect::RunRebase {
                phase,
                target_branch: _,
            } => Some((
                PipelineEvent::rebase_succeeded(phase, "mock_head_abc123".to_string()),
                vec![],
            )),

            Effect::ResolveRebaseConflicts { strategy: _ } => {
                Some((PipelineEvent::rebase_conflict_resolved(vec![]), vec![]))
            }

            Effect::PrepareCommitPrompt { prompt_mode: _ } => {
                let attempt = match self.state.commit {
                    CommitState::Generating { attempt, .. } => attempt,
                    _ => 1,
                };
                // Compute the prompt key the same way the real handler does.
                let retry_mode = RetryMode::Normal;
                let scope_key = PromptScopeKey::for_commit(
                    self.state.iteration,
                    attempt,
                    retry_mode,
                    self.state.recovery_epoch,
                );
                let key = scope_key.to_string();
                let was_replayed = self
                    .replay_prompt_keys
                    .as_ref()
                    .is_some_and(|keys| keys.contains(&key));
                let ui = vec![
                    UIEvent::PhaseTransition {
                        from: Some(self.state.phase),
                        to: PipelinePhase::CommitMessage,
                    },
                    UIEvent::PromptReplayHit { key, was_replayed },
                ];
                Some((PipelineEvent::commit_prompt_prepared(attempt), ui))
            }

            Effect::InvokeCommitAgent => {
                let attempt = match self.state.commit {
                    CommitState::Generating { attempt, .. } => attempt,
                    _ => 1,
                };
                Some((PipelineEvent::commit_agent_invoked(attempt), vec![]))
            }

            Effect::ExtractCommitXml => {
                let attempt = match self.state.commit {
                    CommitState::Generating { attempt, .. } => attempt,
                    _ => 1,
                };
                Some((PipelineEvent::commit_xml_extracted(attempt), vec![]))
            }

            Effect::ValidateCommitXml => {
                let attempt = match self.state.commit {
                    CommitState::Generating { attempt, .. } => attempt,
                    _ => 1,
                };

                let event = if let Some(json) = &self.simulate_commit_json {
                    parse_mock_commit_json_event(json, attempt)
                } else {
                    let message =
                        "feat: mock commit message for testing\n\nThis is a mock commit body \
                         generated for testing purposes.\n\n- Changed some files\n- Added new features"
                            .to_string();
                    PipelineEvent::commit_xml_validated(message, vec![], vec![], attempt)
                };

                let ui = vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::CommitMessage,
                    content: "(mock JSON artifact)".to_string(),
                    context: None,
                }];

                Some((event, ui))
            }

            Effect::ApplyCommitMessageOutcome => {
                let event = self.state.commit_validated_outcome.as_ref().map_or_else(
                    || {
                        PipelineEvent::commit_generation_failed(
                            "Mock commit outcome missing".to_string(),
                        )
                    },
                    |outcome| {
                        outcome.message.as_ref().map_or_else(
                            || {
                                outcome.reason.as_ref().map_or_else(
                                    || {
                                        PipelineEvent::commit_generation_failed(
                                            "Mock commit outcome missing message and reason"
                                                .to_string(),
                                        )
                                    },
                                    |reason| {
                                        PipelineEvent::commit_message_validation_failed(
                                            reason.clone(),
                                            outcome.attempt,
                                        )
                                    },
                                )
                            },
                            |message| {
                                PipelineEvent::commit_message_generated(
                                    message.clone(),
                                    outcome.attempt,
                                )
                            },
                        )
                    },
                );
                Some((event, vec![]))
            }

            Effect::ArchiveCommitXml => {
                let attempt = match self.state.commit {
                    CommitState::Generating { attempt, .. } => attempt,
                    _ => 1,
                };
                Some((PipelineEvent::commit_xml_archived(attempt), vec![]))
            }

            Effect::CreateCommit {
                message,
                files: _,
                excluded_files: _,
            } => Some((
                PipelineEvent::commit_created("mock_commit_hash_abc123".to_string(), message),
                vec![],
            )),

            Effect::SkipCommit { reason } => Some((PipelineEvent::commit_skipped(reason), vec![])),

            _ => None,
        }
    }
}

/// Parse a mock commit JSON value into a `PipelineEvent` for `ValidateCommitXml`.
///
/// Supports the same JSON schema as the real artifact:
/// - `{ "type": "commit", "subject": "...", "excluded_files": [...] }`
/// - `{ "type": "skip", "reason": "..." }`
fn parse_mock_commit_json_event(json: &serde_json::Value, attempt: u32) -> PipelineEvent {
    // Skip variant
    if json.get("type").and_then(|t| t.as_str()) == Some("skip") {
        let reason = json
            .get("reason")
            .and_then(|r| r.as_str())
            .unwrap_or("no reason provided")
            .to_string();
        return PipelineEvent::commit_skipped(reason);
    }

    let Some(subject) = json.get("subject").and_then(|s| s.as_str()) else {
        return PipelineEvent::commit_xml_validation_failed(
            "Mock JSON artifact missing required 'subject' field".to_string(),
            attempt,
        );
    };

    let subject = subject.trim();
    if subject.is_empty() {
        return PipelineEvent::commit_xml_validation_failed(
            "Mock JSON artifact has empty 'subject' field".to_string(),
            attempt,
        );
    }

    let excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile> = json
        .get("excluded_files")
        .and_then(|f| f.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|item| {
                    let path = item.get("path")?.as_str()?.to_string();
                    let reason_str = item.get("reason")?.as_str()?;
                    let reason = match reason_str {
                        "internal_ignore" => {
                            crate::reducer::state::pipeline::ExcludedFileReason::InternalIgnore
                        }
                        "not_task_related" => {
                            crate::reducer::state::pipeline::ExcludedFileReason::NotTaskRelated
                        }
                        "sensitive" => crate::reducer::state::pipeline::ExcludedFileReason::Sensitive,
                        "deferred" => crate::reducer::state::pipeline::ExcludedFileReason::Deferred,
                        _ => crate::reducer::state::pipeline::ExcludedFileReason::NotTaskRelated,
                    };
                    Some(crate::reducer::state::pipeline::ExcludedFile { path, reason })
                })
                .collect()
        })
        .unwrap_or_default();

    PipelineEvent::commit_xml_validated(subject.to_string(), vec![], excluded_files, attempt)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reducer::event::CommitEvent;

    #[test]
    fn test_effect_mapping_does_not_handle_check_commit_diff_to_avoid_inconsistent_content_id() {
        // `Effect::CheckCommitDiff` is workspace-backed in MockEffectHandler::execute so the
        // content id is computed from the actual diff bytes written to workspace.
        // The pure effect-mapping layer must not synthesize a fake content id.
        let handler = MockEffectHandler::new(crate::reducer::state::PipelineState::initial(1, 0));
        let mapped = handler.handle_commit_effect(Effect::CheckCommitDiff);
        assert!(
            mapped.is_none(),
            "CheckCommitDiff should be handled by the workspace-backed execute path"
        );
    }

    #[test]
    fn test_validate_commit_xml_propagates_excluded_files_metadata() {
        let state = crate::reducer::state::PipelineState::initial(1, 0);
        let handler = MockEffectHandler::new(state).with_commit_json(serde_json::json!({
            "type": "commit",
            "subject": "feat: mock",
            "excluded_files": [
                { "path": "src/leftover.rs", "reason": "deferred" }
            ]
        }));

        let (event, _ui) = handler
            .handle_commit_effect(Effect::ValidateCommitXml)
            .expect("ValidateCommitXml should be handled");

        match event {
            PipelineEvent::Commit(CommitEvent::CommitXmlValidated { excluded_files, .. }) => {
                assert_eq!(excluded_files.len(), 1);
                assert_eq!(excluded_files[0].path, "src/leftover.rs");
            }
            other => panic!("expected CommitXmlValidated event, got {other:?}"),
        }
    }

}
