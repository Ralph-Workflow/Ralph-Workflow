/// Determine the next effect to execute based on current state.
///
/// This function is pure - it only reads state and returns an effect.
/// The actual execution happens in the effect handler.
///
/// # Priority Order for Effects
///
/// 1. Continuation context cleanup (highest priority)
/// 2. Same-agent retry pending (timeout/internal error, retry same agent)
/// 2. XSD retry pending (validation failed, retry with same agent/session)
/// 3. Continue pending (output valid but incomplete, new session)
/// 4. Rebase in progress
/// 5. Agent chain exhausted
/// 6. Backoff wait
/// 7. Phase-specific effects
#[must_use]
pub fn determine_next_effect(state: &PipelineState) -> Effect {
    // Terminal: once aborted, drive a single checkpoint save so the event loop can
    // deterministically complete (Interrupted + checkpoint_saved_count > 0).
    if state.phase == PipelinePhase::Interrupted && state.checkpoint_saved_count == 0 {
        // BUT: if restoration is pending, do that FIRST before termination effects.
        if state.prompt_permissions.restore_needed && !state.prompt_permissions.restored {
            return Effect::RestorePromptPermissions;
        }

        // Do NOT bypass the pre-termination commit safety check here.
        // The ONLY exception is Ctrl+C (interrupted_by_user=true), which is handled
        // in phase-specific orchestration.
        return determine_next_effect_for_phase(state);
    }

    // Startup: Lock PROMPT.md permissions before any work (best-effort protection)
    if !state.prompt_permissions.locked {
        return Effect::LockPromptPermissions;
    }

    // Loop detection: check if the same effect has been derived too many times consecutively.
    // This prevents infinite tight loops when XSD retry or other recovery mechanisms cannot
    // converge (e.g., due to workspace/CWD path mismatch).
    let effect_fingerprint = compute_effect_fingerprint(state);
    let loop_detected = state
        .continuation
        .last_effect_kind
        .as_deref()
        .is_some_and(|last| last == effect_fingerprint)
        && state.continuation.consecutive_same_effect_count
            >= state.continuation.max_consecutive_same_effect;

    if loop_detected
        && !matches!(
            state.phase,
            PipelinePhase::Complete | PipelinePhase::Interrupted
        )
    {
        // MANDATORY RECOVERY: we're in a tight loop and not in a terminal phase
        return Effect::TriggerLoopRecovery {
            detected_loop: effect_fingerprint,
            loop_count: state.continuation.consecutive_same_effect_count,
        };
    }

    if state.continuation.context_cleanup_pending {
        return Effect::CleanupContinuationContext;
    }

    // Timeout context write: when a timeout with partial output occurs but the agent has no
    // session ID, we must extract the context from the logfile and write it to a temp file
    // BEFORE the same-agent retry prompt is prepared.
    if state.continuation.timeout_context_write_pending {
        return derive_timeout_context_write_effect(state);
    }

    // Same-agent retry: invocation failed (timeout/internal error), retry same agent with
    // retry-specific prompt guidance.
    if state.continuation.same_agent_retry_pending {
        if state.continuation.same_agent_retries_exhausted() {
            debug_assert!(
                false,
                "Unexpected state: same_agent_retry_pending=true but same_agent_retries_exhausted()=true. \
                 The reducer should have cleared same_agent_retry_pending when retries exhausted. \
                 same_agent_retry_count={}, max_same_agent_retry_count={}",
                state.continuation.same_agent_retry_count,
                state.continuation.max_same_agent_retry_count
            );
        } else {
            return derive_same_agent_retry_effect(state);
        }
    }

    // XSD retry: validation failed, retry with same agent/session if not exhausted.
    // Note: The reducer should clear xsd_retry_pending when retries are exhausted, so
    // normally we wouldn't see xsd_retry_pending=true AND xsd_retries_exhausted()=true.
    if state.continuation.xsd_retry_pending {
        if state.continuation.xsd_retries_exhausted() {
            // Edge case: xsd_retry_pending is true but retries are exhausted.
            // This shouldn't happen in normal operation since the reducer clears
            // xsd_retry_pending when exhausting retries. However, if it does occur
            // (e.g., due to a bug or unexpected state), we fall through to normal
            // phase effects rather than deriving a retry effect that would fail.
            debug_assert!(
                false,
                "Unexpected state: xsd_retry_pending=true but xsd_retries_exhausted()=true. \
                 The reducer should have cleared xsd_retry_pending when retries exhausted. \
                 xsd_retry_count={}, max_xsd_retry_count={}",
                state.continuation.xsd_retry_count, state.continuation.max_xsd_retry_count
            );
            // Fall through to normal phase effects
        } else {
            return derive_xsd_retry_effect(state);
        }
    }

    // Development continuation pending: output valid but work incomplete, start new session
    // Only check continue_pending in Development phase to avoid confusion with fix_continue_pending
    if state.phase == PipelinePhase::Development && state.continuation.continue_pending {
        if state.continuation.continuations_exhausted() {
            // Exhausted continuation budget - accept current state as complete
            // The budget exhaustion is handled by state reduction, so we proceed
            // to normal phase-specific effects
        } else {
            // Trigger continuation with new session
            return derive_continuation_effect(state);
        }
    }

    // Fix continuation pending: fix output valid but issues remain, start new session
    // Only check fix_continue_pending in Review phase to be explicit about phase context
    if state.phase == PipelinePhase::Review && state.continuation.fix_continue_pending {
        if state.continuation.fix_continuations_exhausted() {
            // Exhausted fix continuation budget - proceed to commit
            // The budget exhaustion is handled by state reduction
        } else {
            // Trigger fix continuation with new session
            return derive_continuation_effect(state);
        }
    }

    if matches!(
        state.rebase,
        RebaseState::InProgress { .. } | RebaseState::Conflicted { .. }
    ) {
        let phase = match state.phase {
            PipelinePhase::Planning => RebasePhase::Initial,
            _ => RebasePhase::PostReview,
        };

        return match &state.rebase {
            RebaseState::InProgress { target_branch, .. } => Effect::RunRebase {
                phase,
                target_branch: target_branch.clone(),
            },
            RebaseState::Conflicted { .. } => Effect::ResolveRebaseConflicts {
                strategy: super::event::ConflictStrategy::Continue,
            },
            _ => unreachable!("checked rebase state before matching"),
        };
    }

    if !state.agent_chain.agents.is_empty() && state.agent_chain.is_exhausted() {
        let progressed = match state.phase {
            PipelinePhase::Planning | PipelinePhase::Development => state.iteration > 0,
            PipelinePhase::Review => state.reviewer_pass > 0,
            PipelinePhase::CommitMessage => matches!(
                state.commit,
                CommitState::Generated { .. }
                    | CommitState::Committed { .. }
                    | CommitState::Skipped
            ),
            PipelinePhase::FinalValidation
            | PipelinePhase::Finalizing
            | PipelinePhase::Complete
            | PipelinePhase::AwaitingDevFix
            | PipelinePhase::Interrupted => false,
        };

        if progressed
            && state.checkpoint_saved_count == 0
            && !matches!(
                state.phase,
                PipelinePhase::Complete
                    | PipelinePhase::Interrupted
                    | PipelinePhase::AwaitingDevFix
            )
        {
            return Effect::SaveCheckpoint {
                trigger: CheckpointTrigger::Interrupt,
            };
        }

        // AwaitingDevFix is the phase we transition to AFTER reporting agent chain exhaustion.
        // If we're already in AwaitingDevFix with an exhausted chain, don't report exhaustion
        // again - instead fall through to phase-specific orchestration (TriggerDevFixFlow).
        if matches!(state.phase, PipelinePhase::AwaitingDevFix) {
            // Fall through to determine_next_effect_for_phase
        } else {
            return Effect::ReportAgentChainExhausted {
                role: state.agent_chain.current_role,
                phase: state.phase,
                cycle: state.agent_chain.retry_cycle,
            };
        }
    }

    if let Some(duration_ms) = state.agent_chain.backoff_pending_ms {
        return Effect::BackoffWait {
            role: state.agent_chain.current_role,
            cycle: state.agent_chain.retry_cycle,
            duration_ms,
        };
    }

    // Cloud mode orchestration: sequence cloud-specific operations
    // CRITICAL: All cloud-specific logic is guarded by cloud.enabled check.
    // When cloud mode is disabled, this entire block is skipped and behavior is
    // identical to current CLI behavior.
    if state.cloud.enabled {
        // After a successful commit, push immediately (cloud mode only)
        if let Some(commit_sha) = &state.pending_push_commit {
            // Configure git auth first if not done yet
            if !state.git_auth_configured {
                // Format auth method for the effect
                let auth_method = match &state.cloud.git_remote.auth_method {
                    crate::config::GitAuthStateMethod::SshKey { key_path } => key_path
                        .as_ref().map_or_else(|| "ssh-key:default".to_string(), |p| format!("ssh-key:{p}")),
                    crate::config::GitAuthStateMethod::Token { username } => {
                        format!("token:{username}")
                    }
                    crate::config::GitAuthStateMethod::CredentialHelper { helper } => {
                        format!("credential-helper:{helper}")
                    }
                };
                return Effect::ConfigureGitAuth { auth_method };
            }

            // Then push the commit
            if state.cloud.git_remote.push_branch.is_empty() {
                return Effect::EmitCompletionMarkerAndTerminate {
                    is_failure: true,
                    reason: Some(
                        "Cloud mode is enabled but no push branch was resolved".to_string(),
                    ),
                };
            }
            return Effect::PushToRemote {
                remote: state.cloud.git_remote.remote_name.clone(),
                branch: state.cloud.git_remote.push_branch.clone(),
                force: state.cloud.git_remote.force_push,
                commit_sha: commit_sha.clone(),
            };
        }

        // In Finalizing phase, create PR if configured
        if state.phase == PipelinePhase::Finalizing
            && state.cloud.git_remote.create_pr
            && !state.pr_created
        {
            // PR creation is only meaningful if we actually produced commits.
            // If no commits were created, skip PR creation even if configured.
            if state.metrics.commits_created_total == 0 {
                // Fall through to normal phase effects (finalization/cleanup).
                // Completion reporting will still include push_count/unpushed_commits.
            } else {
                if !state.unpushed_commits.is_empty()
                    || state.push_count == 0
                    || state.last_pushed_commit.is_none()
                {
                    return Effect::EmitCompletionMarkerAndTerminate {
                        is_failure: true,
                        reason: Some(
                            "Cannot create PR because required pushes did not succeed (unpushed commits remain)"
                                .to_string(),
                        ),
                    };
                }

                if state.cloud.git_remote.push_branch.is_empty() {
                    return Effect::EmitCompletionMarkerAndTerminate {
                        is_failure: true,
                        reason: Some(
                            "Cloud mode is enabled but no PR head branch was resolved".to_string(),
                        ),
                    };
                }
                let (title, body) = render_cloud_pr_title_and_body(state);
                return Effect::CreatePullRequest {
                    base_branch: state
                        .cloud
                        .git_remote
                        .pr_base_branch
                        .clone()
                        .unwrap_or_else(|| "main".to_string()),
                    head_branch: state.cloud.git_remote.push_branch.clone(),
                    title,
                    body,
                };
            }
        }
    }

    // Recovery completion: if the pipeline entered recovery due to a commit failure,
    // only clear recovery state AFTER CreateCommit has succeeded.
    //
    // Commit success is represented by CommitState::Committed (or Skipped) which occurs
    // after the CreateCommit/SkipCommit effect has completed and the reducer advanced
    // the phase. We intentionally do this here (not in commit-phase orchestration) so
    // we don't clear counters before retrying a potentially failing CreateCommit.
    if state.dev_fix_attempt_count > 0
        && state.recovery_escalation_level > 0
        && state.failed_phase_for_recovery == Some(PipelinePhase::CommitMessage)
        && matches!(
            state.commit,
            CommitState::Committed { .. } | CommitState::Skipped
        )
    {
        return Effect::EmitRecoverySuccess {
            level: state.recovery_escalation_level,
            total_attempts: state.dev_fix_attempt_count,
        };
    }

    determine_next_effect_for_phase(state)
}
