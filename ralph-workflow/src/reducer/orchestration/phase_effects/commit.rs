//! Commit phase orchestration.
//!
//! Pure orchestration: State → Effect, no I/O.
//!
//! Commit phase workflow:
//! 1. Initialize agent chain (Commit role)
//! 2. Check commit diff (detect empty diff)
//! 3. If diff is empty: Skip commit
//! 4. Otherwise:
//!    a. Materialize commit inputs (diff)
//!    b. Prepare commit prompt
//!    c. Cleanup commit XML (on attempt 1 only)
//!    d. Invoke commit agent
//!    e. Extract commit XML
//!    f. Validate commit XML
//!    g. Archive commit XML
//!    h. Create commit
//! 5. Save checkpoint (transition to `FinalValidation`)
//!
//! Diff content ID:
//! - `commit_diff_content_id_sha256` tracks the diff content hash
//! - Re-run `CheckCommitDiff` if `content_id` is missing (backward compatibility)
//! - Invalidate materialized inputs if `content_id` changes

use crate::agents::AgentDrain;
use crate::reducer::effect::Effect;
use crate::reducer::event::CheckpointTrigger;
use crate::reducer::state::{CommitState, PipelineState, PromptMode};

/// Files that the commit agent writes.
///
/// These files are cleaned up before each commit agent invocation to ensure
/// fresh output. The commit agent writes to `.agent/tmp/commit_message.xml`.
pub(super) const REQUIRED_FILES: &[&str] = &[
    ".agent/tmp/commit_message.xml",
    ".agent/tmp/commit_message.json",
    ".agent/tmp/commit_message.partial.json",
];

pub(super) fn determine_commit_effect(state: &PipelineState) -> Effect {
    // Commit phase requires explicit agent chain initialization like other phases
    if state.agent_chain.agents.is_empty() || state.agent_chain.current_drain != AgentDrain::Commit
    {
        return Effect::InitializeAgentChain {
            drain: AgentDrain::Commit,
        };
    }
    match state.commit {
        CommitState::NotStarted | CommitState::Generating { .. } => {
            let current_attempt = match state.commit {
                CommitState::Generating { attempt, .. } => attempt,
                _ => 1,
            };
            if let Some(outcome) = state.commit_validated_outcome.as_ref() {
                if outcome.attempt == current_attempt && state.commit_xml_extracted {
                    return Effect::ApplyCommitMessageOutcome;
                }
            }

            // Once the prompt is prepared, retry flows should not require rematerializing
            // inputs (or re-checking the diff) before re-cleaning XML and reinvoking.
            // The prompt file on disk is the source of truth for invocation.
            if state.commit_prompt_prepared {
                if current_attempt == 1 && !state.commit_required_files_cleaned {
                    return Effect::CleanupRequiredFiles {
                        files: REQUIRED_FILES.iter().map(ToString::to_string).collect(),
                    };
                }

                let last_effect_was_commit_agent = state
                    .continuation
                    .last_effect_kind
                    .as_deref()
                    .is_some_and(|k| k.contains("InvokeCommitAgent"));

                let effective_commit_agent_invoked = state.commit_agent_invoked
                    || (last_effect_was_commit_agent
                        && state.commit_prompt_prepared
                        && (current_attempt != 1 || state.commit_required_files_cleaned)
                        && state.agent_chain.current_drain == AgentDrain::Commit);

                if !effective_commit_agent_invoked {
                    return Effect::InvokeCommitAgent;
                }
                if !state.commit_xml_extracted {
                    return Effect::ExtractCommitXml;
                }
                return Effect::ValidateCommitXml;
            }

            if !state.commit_diff_prepared {
                return Effect::CheckCommitDiff;
            }
            if state.commit_diff_empty {
                return Effect::SkipCommit {
                    reason: "No changes to commit (empty diff)".to_string(),
                };
            }
            // Backward compatibility / recoverability: older checkpoints may have
            // `commit_diff_prepared = true` but no recorded content id. Re-run diff
            // preparation once to establish `commit_diff_content_id_sha256`, which is
            // required to safely guard against stale materialized prompt inputs.
            if state.commit_diff_content_id_sha256.is_none() {
                return Effect::CheckCommitDiff;
            }
            let current_attempt = match state.commit {
                CommitState::Generating { attempt, .. } => attempt,
                _ => 1,
            };
            let consumer_signature_sha256 = state.agent_chain.consumer_signature_sha256();
            let diff_content_id_sha256 = state.commit_diff_content_id_sha256.as_deref();
            if !state.commit_prompt_prepared {
                let commit_inputs_materialized_for_attempt =
                    state.prompt_inputs.commit.as_ref().is_some_and(|c| {
                        c.attempt == current_attempt
                            && c.diff.consumer_signature_sha256 == consumer_signature_sha256
                            && diff_content_id_sha256
                                .is_some_and(|id| id == c.diff.content_id_sha256)
                    });
                if !commit_inputs_materialized_for_attempt {
                    return Effect::MaterializeCommitInputs {
                        attempt: current_attempt,
                    };
                }
                // Derive prompt mode from reducer-owned retry state so commit retry prompts remain
                // reachable even when retry routing is bypassed.
                let prompt_mode = if state.continuation.same_agent_retry_pending
                    && !state.continuation.same_agent_retries_exhausted()
                {
                    PromptMode::SameAgentRetry
                } else {
                    PromptMode::Normal
                };
                return Effect::PrepareCommitPrompt { prompt_mode };
            }
            // Prompt-prepared flow is handled above.
            Effect::ValidateCommitXml
        }
        CommitState::Generated { ref message } => {
            if state.commit_xml_archived {
                Effect::CreateCommit {
                    message: message.clone(),
                    files: state.commit_selected_files.clone(),
                    excluded_files: state.commit_excluded_files.clone(),
                }
            } else {
                Effect::ArchiveCommitXml
            }
        }
        CommitState::Skipped => Effect::SaveCheckpoint {
            trigger: CheckpointTrigger::PhaseTransition,
        },
        CommitState::Committed { .. } => {
            // After a selective commit (non-empty commit_selected_files) or a residual retry
            // pass, check whether any files remain uncommitted before proceeding.
            let is_selective = !state.commit_selected_files.is_empty();
            let retry_pass = state.commit_residual_retry_pass;
            if is_selective || retry_pass > 0 {
                let pass = if retry_pass > 0 { retry_pass } else { 1 };
                Effect::CheckResidualFiles { pass }
            } else {
                Effect::SaveCheckpoint {
                    trigger: CheckpointTrigger::PhaseTransition,
                }
            }
        }
    }
}
