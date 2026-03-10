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

use crate::files::llm_output_extraction::try_extract_xml_commit_with_trace;
use crate::prompts::prompt_scope_key::{PromptScopeKey, RetryMode};
use crate::reducer::effect::Effect;
use crate::reducer::event::{PipelineEvent, PipelinePhase};
use crate::reducer::state::{CommitState, PromptMode};
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

            Effect::PrepareCommitPrompt { prompt_mode } => {
                let attempt = match self.state.commit {
                    CommitState::Generating { attempt, .. } => attempt,
                    _ => 1,
                };
                // Compute the prompt key the same way the real handler does,
                // using Normal retry mode for Normal/SameAgentRetry modes.
                let retry_mode = match prompt_mode {
                    PromptMode::XsdRetry => RetryMode::Xsd { count: 1 },
                    _ => RetryMode::Normal,
                };
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
                let xml = self.simulate_commit_message_xml.clone().unwrap_or_else(|| {
                    r"<ralph-commit>
<ralph-subject>feat: mock commit message for testing</ralph-subject>
<ralph-body>This is a mock commit body generated for testing purposes.

- Changed some files
- Added new features</ralph-body>
</ralph-commit>"
                        .to_string()
                });

                let (message, skip_reason, files, _excluded_files, detail) =
                    try_extract_xml_commit_with_trace(&xml);

                let event = skip_reason.map_or_else(
                    || {
                        message.map_or_else(
                            || PipelineEvent::commit_xml_validation_failed(detail, attempt),
                            |message| {
                                PipelineEvent::commit_xml_validated(message, files, vec![], attempt)
                            },
                        )
                    },
                    PipelineEvent::commit_skipped,
                );

                let ui = vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::CommitMessage,
                    content: xml,
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

#[cfg(test)]
mod tests {
    use super::*;

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
}
