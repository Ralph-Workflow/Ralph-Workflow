// NOTE: split from reducer/state_reduction.rs.
//
// IMPORTANT: The DiffFailed event is DEPRECATED as of the diff failure fallback fix.
// When git diff fails, the handler now uses fallback instructions instead of emitting
// DiffFailed. The event handler remains for backward compatibility with old checkpoints
// but is a no-op to prevent incorrect termination.
//
// See: ralph-workflow/src/reducer/handler/commit/inputs.rs:check_commit_diff_with_result
// for the new fallback behavior.

mod transition;
mod validation;

use crate::reducer::event::CommitEvent;
use crate::reducer::state::{CommitState, ContinuationState, PipelineState};
use transition::compute_post_commit_transition;
use validation::reduce_commit_validation_failed;

fn compute_post_commit_phase_data(
    state: &PipelineState,
) -> (
    crate::reducer::event::PipelinePhase,
    u32,
    u32,
    crate::reducer::state::AgentChainState,
    ContinuationState,
) {
    let (next_phase, next_iter, next_reviewer_pass) = compute_post_commit_transition(state);

    // When transitioning to Review phase, reset the agent chain for the Review drain
    // to ensure the reviewer fallback chain is used, not any other chain (Developer, Commit).
    // This handles both:
    // - Development → CommitMessage → Review (first review pass)
    // - Review → CommitMessage → Review (between review passes after fix)
    let agent_chain = if next_phase == crate::reducer::event::PipelinePhase::Review {
        crate::reducer::state::AgentChainState::initial()
            .with_max_cycles(state.agent_chain.max_cycles)
            .with_backoff_policy(
                state.agent_chain.retry_delay_ms,
                state.agent_chain.backoff_multiplier,
                state.agent_chain.max_backoff_ms,
            )
            .reset_for_drain(crate::agents::AgentDrain::Review)
    } else {
        state.agent_chain.clone()
    };

    let continuation = if next_phase == crate::reducer::event::PipelinePhase::Planning {
        crate::reducer::state::ContinuationState {
            invalid_output_attempts: 0,
            ..state.continuation.clone()
        }
    } else {
        state.continuation.clone()
    };

    (
        next_phase,
        next_iter,
        next_reviewer_pass,
        agent_chain,
        continuation,
    )
}

pub(super) fn reduce_commit_event(state: PipelineState, event: CommitEvent) -> PipelineState {
    const MAX_CONSECUTIVE_PUSH_FAILURES: u32 = 3;

    match event {
        CommitEvent::GenerationStarted => PipelineState {
            commit: CommitState::Generating {
                attempt: 1,
                max_attempts: crate::reducer::state::MAX_VALIDATION_RETRY_ATTEMPTS,
            },
            commit_prompt_prepared: false,
            commit_agent_invoked: false,
            commit_required_files_cleaned: false,
            commit_xml_extracted: false,
            commit_validated_outcome: None,
            commit_xml_archived: false,
            commit_selected_files: vec![],
            commit_excluded_files: vec![],
            ..state
        },
        CommitEvent::DiffPrepared {
            empty,
            content_id_sha256,
        } => PipelineState {
            commit_diff_prepared: true,
            commit_diff_empty: empty,
            commit_diff_content_id_sha256: Some(content_id_sha256),
            // If the diff is (re)prepared, any previously materialized commit inputs
            // are potentially stale (the diff file was rewritten). Force rematerialization.
            prompt_inputs: state.prompt_inputs.with_commit_cleared(),
            ..state
        },
        // DEPRECATED: DiffFailed is no longer emitted (as of fix for missing fallback).
        // This event is kept ONLY for backward compatibility with old checkpoints.
        // New handler code uses fallback instructions instead of emitting DiffFailed.
        // If this event is somehow emitted, treat as no-op to avoid termination.
        CommitEvent::DiffFailed { .. } | CommitEvent::PullRequestFailed { .. } => state,
        CommitEvent::DiffInvalidated { .. } => PipelineState {
            commit_diff_prepared: false,
            commit_diff_empty: false,
            commit_diff_content_id_sha256: None,
            commit_prompt_prepared: false,
            commit_agent_invoked: false,
            commit_required_files_cleaned: false,
            commit_xml_extracted: false,
            commit_validated_outcome: None,
            commit_xml_archived: false,
            commit_selected_files: vec![],
            commit_excluded_files: vec![],
            prompt_inputs: state.prompt_inputs.with_commit_cleared(),
            ..state
        },
        CommitEvent::PromptPrepared { .. } => PipelineState {
            commit: match state.commit {
                CommitState::NotStarted => CommitState::Generating {
                    attempt: 1,
                    max_attempts: crate::reducer::state::MAX_VALIDATION_RETRY_ATTEMPTS,
                },
                other => other,
            },
            commit_prompt_prepared: true,
            continuation: crate::reducer::state::ContinuationState {
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: state.continuation.xsd_retry_session_reuse_pending,
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                ..state.continuation
            },
            ..state
        },
        CommitEvent::AgentInvoked { .. } => PipelineState {
            commit_agent_invoked: true,
            continuation: crate::reducer::state::ContinuationState {
                xsd_retry_pending: false,
                xsd_retry_session_reuse_pending: false,
                same_agent_retry_pending: false,
                same_agent_retry_reason: None,
                last_xsd_error: None,
                ..state.continuation
            },
            ..state
        },
        CommitEvent::CommitXmlCleaned { .. } => PipelineState {
            commit_required_files_cleaned: true,
            ..state
        },
        CommitEvent::CommitXmlExtracted { .. } => PipelineState {
            commit_xml_extracted: true,
            ..state
        },
        CommitEvent::CommitXmlMissing { attempt } => PipelineState {
            commit_xml_extracted: true,
            commit_validated_outcome: Some(crate::reducer::state::CommitValidatedOutcome {
                attempt,
                message: None,
                reason: Some("Commit XML missing".to_string()),
            }),
            ..state
        },
        CommitEvent::CommitXmlValidated {
            message,
            attempt,
            files,
            excluded_files,
        } => PipelineState {
            commit_validated_outcome: Some(crate::reducer::state::CommitValidatedOutcome {
                attempt,
                message: Some(message),
                reason: None,
            }),
            commit_selected_files: files,
            commit_excluded_files: excluded_files,
            ..state
        },
        CommitEvent::CommitXmlValidationFailed { reason, attempt } => PipelineState {
            commit_validated_outcome: Some(crate::reducer::state::CommitValidatedOutcome {
                attempt,
                message: None,
                reason: Some(reason),
            }),
            ..state
        },
        CommitEvent::CommitXmlArchived { .. } => PipelineState {
            commit_xml_archived: true,
            ..state
        },
        CommitEvent::MessageGenerated { message, .. } => PipelineState {
            commit: CommitState::Generated { message },
            ..state
        },
        CommitEvent::Created { hash, .. } => {
            let needs_residual_handling =
                !state.commit_selected_files.is_empty() || state.commit_residual_retry_pass > 0;

            // Cloud mode: mark commit as pending push
            let pending_push = if state.cloud.enabled {
                Some(hash.clone())
            } else {
                None
            };

            if let Some(resume_phase) = state.termination_resume_phase {
                // Special case: commit was forced by the pre-termination safety check.
                // If residual checking is required (selective/retry-pass), we must stay in
                // CommitMessage so orchestration can run `CheckResidualFiles`.
                // Otherwise, resume the original termination phase and re-run the safety check
                // to confirm the repo is clean.
                if needs_residual_handling {
                    return PipelineState {
                        commit: CommitState::Committed { hash },
                        phase: crate::reducer::event::PipelinePhase::CommitMessage,
                        // Keep resume target until residual handling completes.
                        termination_resume_phase: Some(resume_phase),
                        // Do NOT unblock termination yet; the safety check must confirm cleanliness.
                        pre_termination_commit_checked: false,
                        context_cleaned: false,
                        commit_prompt_prepared: false,
                        commit_agent_invoked: false,
                        commit_required_files_cleaned: false,
                        commit_xml_extracted: false,
                        commit_validated_outcome: None,
                        commit_xml_archived: false,
                        commit_diff_prepared: false,
                        commit_diff_empty: false,
                        commit_diff_content_id_sha256: None,
                        // NOTE: keep commit_selected_files/commit_residual_retry_pass for residual orchestration.
                        commit_residual_files: vec![],
                        commit_excluded_files: vec![],
                        prompt_inputs: state.prompt_inputs.clone().with_commit_cleared(),
                        metrics: state.metrics.increment_commits_created_total(),
                        pending_push_commit: pending_push,
                        push_retry_count: 0,
                        last_push_error: None,
                        ..state
                    };
                }

                return PipelineState {
                    commit: CommitState::Committed { hash },
                    phase: resume_phase,
                    termination_resume_phase: None,
                    // IMPORTANT: do not assume the repo is clean; re-run safety check.
                    pre_termination_commit_checked: false,
                    previous_phase: None,
                    context_cleaned: false,
                    commit_prompt_prepared: false,
                    commit_agent_invoked: false,
                    commit_required_files_cleaned: false,
                    commit_xml_extracted: false,
                    commit_validated_outcome: None,
                    commit_xml_archived: false,
                    commit_selected_files: vec![],
                    commit_residual_files: vec![],
                    commit_residual_retry_pass: 0,
                    commit_excluded_files: vec![],
                    commit_diff_prepared: false,
                    commit_diff_empty: false,
                    commit_diff_content_id_sha256: None,
                    prompt_inputs: state.prompt_inputs.clone().with_commit_cleared(),
                    metrics: state.metrics.increment_commits_created_total(),
                    pending_push_commit: pending_push,
                    push_retry_count: 0,
                    last_push_error: None,
                    ..state
                };
            }

            // Selective commit + residual retry handling is orchestrated from `CommitState::Committed`.
            // To keep residual checking reachable, do not advance the phase until residual
            // handling completes.
            if needs_residual_handling {
                return PipelineState {
                    commit: CommitState::Committed { hash },
                    phase: crate::reducer::event::PipelinePhase::CommitMessage,
                    // Preserve previous_phase/iteration/reviewer_pass until post-commit transition.
                    context_cleaned: false,
                    commit_prompt_prepared: false,
                    commit_agent_invoked: false,
                    commit_required_files_cleaned: false,
                    commit_xml_extracted: false,
                    commit_validated_outcome: None,
                    commit_xml_archived: false,
                    commit_diff_prepared: false,
                    commit_diff_empty: false,
                    commit_diff_content_id_sha256: None,
                    // NOTE: keep commit_selected_files/commit_residual_retry_pass for residual orchestration.
                    commit_residual_files: vec![],
                    commit_excluded_files: vec![],
                    prompt_inputs: state.prompt_inputs.clone().with_commit_cleared(),
                    metrics: state.metrics.increment_commits_created_total(),
                    pending_push_commit: pending_push,
                    push_retry_count: 0,
                    last_push_error: None,
                    ..state
                };
            }

            let (next_phase, next_iter, next_reviewer_pass, agent_chain, continuation) =
                compute_post_commit_phase_data(&state);

            PipelineState {
                commit: CommitState::Committed { hash },
                phase: next_phase,
                previous_phase: None,
                iteration: next_iter,
                reviewer_pass: next_reviewer_pass,
                context_cleaned: false,
                commit_required_files_cleaned: false,
                commit_diff_prepared: false,
                commit_diff_empty: false,
                commit_diff_content_id_sha256: None,
                // NOTE: commit_selected_files and commit_residual_retry_pass are intentionally
                // NOT cleared here. The orchestration uses them in Committed state to
                // determine whether to emit CheckResidualFiles before SaveCheckpoint.
                // They are cleared by ResidualFilesNone / ResidualFilesFound on the final retry pass.
                commit_residual_files: vec![],
                commit_excluded_files: vec![],
                prompt_inputs: state.prompt_inputs.clone().with_commit_cleared(),
                agent_chain,
                continuation,
                metrics: state.metrics.increment_commits_created_total(),
                pending_push_commit: pending_push,
                push_retry_count: 0,
                last_push_error: None,
                ..state
            }
        }

        CommitEvent::GitAuthConfigured => PipelineState {
            git_auth_configured: true,
            ..state
        },

        CommitEvent::PushCompleted { commit_sha, .. } => PipelineState {
            pending_push_commit: None,
            push_count: state.push_count + 1,
            push_retry_count: 0,
            last_push_error: None,
            last_pushed_commit: Some(commit_sha),
            ..state
        },

        CommitEvent::PushFailed { error, .. } => {
            let error = crate::cloud::redaction::redact_secrets(&error);
            let new_retry_count = state.push_retry_count.saturating_add(1);
            let at_failure_limit = new_retry_count >= MAX_CONSECUTIVE_PUSH_FAILURES;

            let (pending_push_commit, unpushed_commits, final_retry_count) = if at_failure_limit {
                let commits: Vec<_> = state
                    .unpushed_commits
                    .iter()
                    .chain(state.pending_push_commit.iter())
                    .cloned()
                    .collect();
                (None, commits, 0)
            } else {
                (
                    state.pending_push_commit.clone(),
                    state.unpushed_commits.clone(),
                    new_retry_count,
                )
            };

            PipelineState {
                push_retry_count: final_retry_count,
                last_push_error: Some(error),
                pending_push_commit,
                unpushed_commits,
                ..state
            }
        }

        CommitEvent::PullRequestCreated { url, number } => PipelineState {
            pr_created: true,
            pr_url: Some(url),
            pr_number: Some(number),
            ..state
        },

        CommitEvent::GenerationFailed { .. } => PipelineState {
            commit: CommitState::NotStarted,
            commit_prompt_prepared: false,
            commit_diff_prepared: false,
            commit_diff_empty: false,
            commit_diff_content_id_sha256: None,
            commit_agent_invoked: false,
            commit_required_files_cleaned: false,
            commit_xml_extracted: false,
            commit_validated_outcome: None,
            commit_xml_archived: false,
            commit_selected_files: vec![],
            commit_excluded_files: vec![],
            commit_residual_retry_pass: 0,
            ..state
        },
        CommitEvent::Skipped { .. } => {
            if let Some(resume_phase) = state.termination_resume_phase {
                // The pre-termination safety check detected uncommitted changes and
                // routed here to attempt a commit.
                //
                // Two distinct skip scenarios:
                //
                // 1. Empty diff (orchestration-initiated skip): The diff check found
                //    nothing to commit, so orchestration emitted SkipCommit. The repo
                //    has no committable changes — unblock termination to prevent an
                //    infinite loop (safety check → commit → empty diff → skip → repeat).
                //
                // 2. Non-empty diff (AI-driven skip): The AI chose to skip despite
                //    changes existing. Re-run the safety check to verify the repo is
                //    actually clean before allowing termination.
                let checked = state.commit_diff_empty;
                return PipelineState {
                    commit: CommitState::Skipped,
                    phase: resume_phase,
                    termination_resume_phase: None,
                    pre_termination_commit_checked: checked,
                    previous_phase: None,
                    commit_prompt_prepared: false,
                    commit_agent_invoked: false,
                    commit_required_files_cleaned: false,
                    commit_xml_extracted: false,
                    commit_validated_outcome: None,
                    commit_xml_archived: false,
                    commit_selected_files: vec![],
                    commit_excluded_files: vec![],
                    commit_residual_retry_pass: 0,
                    commit_diff_prepared: false,
                    commit_diff_empty: false,
                    commit_diff_content_id_sha256: None,
                    prompt_inputs: state.prompt_inputs.clone().with_commit_cleared(),
                    ..state
                };
            }

            let (next_phase, next_iter, next_reviewer_pass) =
                compute_post_commit_transition(&state);
            // When transitioning to Review phase, reset the agent chain for Reviewer role
            // to ensure the reviewer fallback chain is used, not any other chain (Developer, Commit).
            // This handles both:
            // - Development → CommitMessage → Review (first review pass)
            // - Review → CommitMessage → Review (between review passes after fix)
            let agent_chain = if next_phase == crate::reducer::event::PipelinePhase::Review {
                crate::reducer::state::AgentChainState::initial()
                    .with_max_cycles(state.agent_chain.max_cycles)
                    .with_backoff_policy(
                        state.agent_chain.retry_delay_ms,
                        state.agent_chain.backoff_multiplier,
                        state.agent_chain.max_backoff_ms,
                    )
                    .reset_for_drain(crate::agents::AgentDrain::Review)
            } else {
                state.agent_chain.clone()
            };

            let continuation = if next_phase == crate::reducer::event::PipelinePhase::Planning {
                ContinuationState {
                    invalid_output_attempts: 0,
                    ..state.continuation
                }
            } else {
                state.continuation.clone()
            };
            PipelineState {
                commit: CommitState::Skipped,
                phase: next_phase,
                previous_phase: None,
                iteration: next_iter,
                reviewer_pass: next_reviewer_pass,
                commit_prompt_prepared: false,
                commit_agent_invoked: false,
                commit_required_files_cleaned: false,
                commit_xml_extracted: false,
                commit_validated_outcome: None,
                commit_xml_archived: false,
                commit_selected_files: vec![],
                commit_excluded_files: vec![],
                commit_residual_retry_pass: 0,
                commit_diff_prepared: false,
                commit_diff_empty: false,
                commit_diff_content_id_sha256: None,
                context_cleaned: false,
                prompt_inputs: state.prompt_inputs.clone().with_commit_cleared(),
                agent_chain,
                continuation,
                ..state
            }
        }
        CommitEvent::MessageValidationFailed { attempt, reason } => {
            reduce_commit_validation_failed(state, attempt, reason)
        }

        CommitEvent::PreTerminationSafetyCheckPassed => PipelineState {
            pre_termination_commit_checked: true,
            ..state
        },

        CommitEvent::PreTerminationUncommittedChangesDetected { .. } => {
            // Safety invariant: the pipeline must not terminate with uncommitted work.
            // Route back through the commit phase, recording the phase we must resume
            // after committing (or explicitly skipping).
            let resume_phase = state.phase;
            PipelineState {
                phase: crate::reducer::event::PipelinePhase::CommitMessage,
                termination_resume_phase: Some(resume_phase),
                // Force re-materialization of commit inputs when we re-enter commit.
                commit: CommitState::NotStarted,
                commit_prompt_prepared: false,
                commit_diff_prepared: false,
                commit_diff_empty: false,
                commit_diff_content_id_sha256: None,
                commit_agent_invoked: false,
                commit_required_files_cleaned: false,
                commit_xml_extracted: false,
                commit_validated_outcome: None,
                commit_xml_archived: false,
                commit_selected_files: vec![],
                commit_excluded_files: vec![],
                commit_residual_retry_pass: 0,
                prompt_inputs: state.prompt_inputs.with_commit_cleared(),
                // Ensure termination cannot proceed until commit finishes.
                pre_termination_commit_checked: false,
                ..state
            }
        }

        CommitEvent::ResidualFilesFound { files, pass } => {
            reduce_residual_files_found(state, files, pass)
        }

        CommitEvent::ResidualFilesNone => reduce_residual_files_none(state),
    }
}

fn reduce_residual_files_found(
    state: PipelineState,
    files: Vec<String>,
    pass: u8,
) -> PipelineState {
    let final_pass = 1u8.saturating_add(state.max_commit_residual_retries);

    if !(1..=final_pass).contains(&pass) {
        // Invariant violation: residual pass must stay within the configured retry budget.
        // Route through AwaitingDevFix so unattended remediation can proceed.
        let in_recovery_loop = state.phase == crate::reducer::event::PipelinePhase::AwaitingDevFix
            || state.previous_phase == Some(crate::reducer::event::PipelinePhase::AwaitingDevFix);

        let (dev_fix_attempt_count, recovery_escalation_level) = if !in_recovery_loop {
            (0, 0)
        } else {
            (state.dev_fix_attempt_count, state.recovery_escalation_level)
        };

        return PipelineState {
            previous_phase: Some(state.phase),
            phase: crate::reducer::event::PipelinePhase::AwaitingDevFix,
            dev_fix_triggered: false,
            failed_phase_for_recovery: Some(crate::reducer::event::PipelinePhase::CommitMessage),
            dev_fix_attempt_count,
            recovery_escalation_level,
            ..state
        };
    }

    if pass < final_pass {
        return PipelineState {
            // Residual files remain and retry budget remains — trigger the next automatic
            // commit retry pass. Reset commit state so orchestration starts a fresh cycle.
            phase: crate::reducer::event::PipelinePhase::CommitMessage,
            commit_residual_retry_pass: pass + 1,
            commit: CommitState::NotStarted,
            commit_prompt_prepared: false,
            commit_diff_prepared: false,
            commit_diff_empty: false,
            commit_diff_content_id_sha256: None,
            commit_agent_invoked: false,
            commit_required_files_cleaned: false,
            commit_xml_extracted: false,
            commit_validated_outcome: None,
            commit_xml_archived: false,
            commit_selected_files: vec![],
            commit_excluded_files: vec![],
            prompt_inputs: state.prompt_inputs.with_commit_cleared(),
            // commit_residual_files stays empty until retry budget is exhausted.
            ..state
        };
    }

    let base = PipelineState {
        commit_residual_retry_pass: 0,
        commit_selected_files: vec![],
        commit_excluded_files: vec![],
        commit_residual_files: files,
        ..state
    };

    if let Some(resume_phase) = base.termination_resume_phase {
        // Pre-termination safety path: resume the termination phase, leaving
        // pre_termination_commit_checked=false so the safety check re-runs and
        // routes back to commit if needed.
        PipelineState {
            phase: resume_phase,
            termination_resume_phase: None,
            pre_termination_commit_checked: false,
            previous_phase: None,
            commit_prompt_prepared: false,
            commit_agent_invoked: false,
            commit_required_files_cleaned: false,
            commit_xml_extracted: false,
            commit_validated_outcome: None,
            commit_xml_archived: false,
            commit_diff_prepared: false,
            commit_diff_empty: false,
            commit_diff_content_id_sha256: None,
            prompt_inputs: base.prompt_inputs.clone().with_commit_cleared(),
            context_cleaned: false,
            ..base
        }
    } else {
        let (next_phase, next_iter, next_reviewer_pass, agent_chain, continuation) =
            compute_post_commit_phase_data(&base);

        PipelineState {
            phase: next_phase,
            previous_phase: None,
            iteration: next_iter,
            reviewer_pass: next_reviewer_pass,
            context_cleaned: false,
            commit_required_files_cleaned: false,
            commit_diff_prepared: false,
            commit_diff_empty: false,
            commit_diff_content_id_sha256: None,
            prompt_inputs: base.prompt_inputs.clone().with_commit_cleared(),
            agent_chain,
            continuation,
            ..base
        }
    }
}

fn reduce_residual_files_none(state: PipelineState) -> PipelineState {
    // Working tree is clean after the commit pass. Clear all residual retry state.
    let base = PipelineState {
        commit_residual_retry_pass: 0,
        commit_selected_files: vec![],
        commit_excluded_files: vec![],
        commit_residual_files: vec![],
        ..state
    };

    if let Some(resume_phase) = base.termination_resume_phase {
        PipelineState {
            phase: resume_phase,
            termination_resume_phase: None,
            // ResidualFilesNone implies a clean working tree; unblock termination.
            pre_termination_commit_checked: true,
            previous_phase: None,
            commit_prompt_prepared: false,
            commit_agent_invoked: false,
            commit_required_files_cleaned: false,
            commit_xml_extracted: false,
            commit_validated_outcome: None,
            commit_xml_archived: false,
            commit_diff_prepared: false,
            commit_diff_empty: false,
            commit_diff_content_id_sha256: None,
            prompt_inputs: base.prompt_inputs.clone().with_commit_cleared(),
            context_cleaned: false,
            ..base
        }
    } else {
        let (next_phase, next_iter, next_reviewer_pass, agent_chain, continuation) =
            compute_post_commit_phase_data(&base);

        PipelineState {
            phase: next_phase,
            previous_phase: None,
            iteration: next_iter,
            reviewer_pass: next_reviewer_pass,
            context_cleaned: false,
            commit_required_files_cleaned: false,
            commit_diff_prepared: false,
            commit_diff_empty: false,
            commit_diff_content_id_sha256: None,
            prompt_inputs: base.prompt_inputs.clone().with_commit_cleared(),
            agent_chain,
            continuation,
            ..base
        }
    }
}

#[cfg(test)]
mod tests {
    use super::reduce_commit_event;
    use crate::reducer::event::CommitEvent;
    use crate::reducer::state::pipeline::{ExcludedFile, ExcludedFileReason};
    use crate::reducer::state::PipelineState;

    fn excluded(path: &str) -> ExcludedFile {
        ExcludedFile {
            path: path.to_string(),
            reason: ExcludedFileReason::Deferred,
        }
    }

    #[test]
    fn test_generation_started_clears_commit_excluded_files() {
        let state = PipelineState {
            commit_excluded_files: vec![excluded("src/leftover.txt")],
            ..PipelineState::initial(1, 0)
        };

        let next = reduce_commit_event(state, CommitEvent::GenerationStarted);
        assert!(
            next.commit_excluded_files.is_empty(),
            "commit_excluded_files must be cleared on commit phase reset"
        );
    }

    #[test]
    fn test_diff_invalidated_clears_commit_excluded_files() {
        let state = PipelineState {
            commit_excluded_files: vec![excluded("src/leftover.txt")],
            ..PipelineState::initial(1, 0)
        };

        let next = reduce_commit_event(
            state,
            CommitEvent::DiffInvalidated {
                reason: "missing diff".to_string(),
            },
        );
        assert!(next.commit_excluded_files.is_empty());
    }

    #[test]
    fn test_generation_failed_clears_retry_pass_and_excluded_files() {
        let state = PipelineState {
            commit_residual_retry_pass: 2,
            commit_excluded_files: vec![excluded("src/leftover.txt")],
            ..PipelineState::initial(1, 0)
        };

        let next = reduce_commit_event(
            state,
            CommitEvent::GenerationFailed {
                reason: "nope".to_string(),
            },
        );
        assert_eq!(next.commit_residual_retry_pass, 0);
        assert!(next.commit_excluded_files.is_empty());
    }

    #[test]
    fn test_skipped_clears_retry_pass_and_excluded_files() {
        let state = PipelineState {
            commit_residual_retry_pass: 2,
            commit_excluded_files: vec![excluded("src/leftover.txt")],
            ..PipelineState::initial(1, 0)
        };

        let next = reduce_commit_event(
            state,
            CommitEvent::Skipped {
                reason: "skip".to_string(),
            },
        );
        assert_eq!(next.commit_residual_retry_pass, 0);
        assert!(next.commit_excluded_files.is_empty());
    }

    #[test]
    fn test_residual_files_none_clears_excluded_files() {
        let state = PipelineState {
            commit_excluded_files: vec![excluded("src/leftover.txt")],
            ..PipelineState::initial(1, 0)
        };

        let next = reduce_commit_event(state, CommitEvent::ResidualFilesNone);
        assert!(next.commit_excluded_files.is_empty());
    }
}
