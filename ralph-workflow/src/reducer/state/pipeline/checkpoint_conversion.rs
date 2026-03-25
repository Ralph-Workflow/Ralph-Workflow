// Checkpoint conversion logic.
//
// Implements conversion from checkpoint format to runtime PipelineState.
// This is pure state transformation with no I/O operations.

use std::collections::VecDeque;

fn bound_execution_history_steps(
    steps: VecDeque<ExecutionStep>,
    limit: usize,
) -> VecDeque<ExecutionStep> {
    if limit == 0 {
        return VecDeque::new();
    }
    let len = steps.len();
    if len <= limit {
        return steps;
    }

    // Keep only the most recent `limit` entries while dropping the oversized
    // allocation from legacy checkpoints.
    let keep_from = len.saturating_sub(limit);
    steps.into_iter().skip(keep_from).collect()
}

impl PipelineState {
    pub(crate) fn from_checkpoint_with_execution_history_limit(
        mut checkpoint: PipelineCheckpoint,
        execution_history_limit: usize,
    ) -> Self {
        let rebase_state = map_checkpoint_rebase_state(&checkpoint.rebase_state);
        let agent_chain = AgentChainState::initial();
        let last_substitution_log = checkpoint.last_substitution_log.clone();
        let (template_validation_failed, template_validation_unsubstituted) = last_substitution_log
            .as_ref()
            .map_or((false, Vec::new()), |log| {
                (!log.is_complete(), log.unsubstituted.clone())
            });

        let execution_history_steps = checkpoint
            .execution_history
            .take()
            .map(|h| h.steps)
            .unwrap_or_default();

        let cloud_state = checkpoint.cloud_state.take();
        let (
            cloud,
            pending_push_commit,
            git_auth_configured,
            pr_created,
            pr_url,
            pr_number,
            push_count,
            push_retry_count,
            last_push_error,
            unpushed_commits,
            last_pushed_commit,
        ) = cloud_state.as_ref().map_or_else(
            || {
                (
                    crate::config::CloudStateConfig::disabled(),
                    None,
                    false,
                    false,
                    None,
                    None,
                    0,
                    0,
                    None,
                    Vec::new(),
                    None,
                )
            },
            |cs| {
                // Preserve checkpoint-safe cloud state for correct resume semantics.
                // Note: git auth configuration can be re-run safely; however, restoring
                // `git_auth_configured=true` for SSH key paths would skip the env-var
                // setup in a new process. Reset it in that case.
                let git_auth_configured = match &cs.cloud.git_remote.auth_method {
                    crate::config::GitAuthStateMethod::SshKey { key_path }
                        if key_path.is_some() =>
                    {
                        false
                    }
                    _ => cs.git_auth_configured,
                };

                (
                    cs.cloud.clone(),
                    cs.pending_push_commit.clone(),
                    git_auth_configured,
                    cs.pr_created,
                    cs.pr_url.clone(),
                    cs.pr_number,
                    cs.push_count,
                    cs.push_retry_count,
                    cs.last_push_error.clone(),
                    cs.unpushed_commits.clone(),
                    cs.last_pushed_commit.clone(),
                )
            },
        );

        // Calculate bounded execution history before building state
        let bounded_steps =
            bound_execution_history_steps(execution_history_steps, execution_history_limit);
        let execution_history = if bounded_steps.is_empty() {
            BoundedExecutionHistory::new()
        } else {
            BoundedExecutionHistory::new().with_replaced(
                bounded_steps,
                execution_history_limit,
            )
        };

        Self {
            phase: map_checkpoint_phase(checkpoint.phase),
            previous_phase: None,
            // Restore iteration/pass counters from checkpoint.
            // Note: All progress flags are reset to None below.
            // Orchestration uses inclusive boundary checks:
            // `iteration < total || (iteration == total && total > 0)`
            // to ensure work is re-run at boundaries when flags are None.
            // See phase_effects.rs for the boundary logic.
            iteration: checkpoint.iteration,
            total_iterations: checkpoint.total_iterations,
            reviewer_pass: checkpoint.reviewer_pass,
            total_reviewer_passes: checkpoint.total_reviewer_passes,
            review_issues_found: false,
            // All progress flags reset to None to allow re-running current work.
            // The orchestration layer determines which step to execute based on
            // these flags combined with the iteration/pass counters.
            planning_prompt_prepared_iteration: None,
            planning_required_files_cleaned_iteration: None,
            planning_agent_invoked_iteration: None,
            planning_xml_extracted_iteration: None,
            planning_validated_outcome: None,
            planning_markdown_written_iteration: None,
            planning_xml_archived_iteration: None,
            development_context_prepared_iteration: None,
            development_prompt_prepared_iteration: None,
            development_required_files_cleaned_iteration: None,
            development_agent_invoked_iteration: None,
            analysis_agent_invoked_iteration: None,
            development_xml_extracted_iteration: None,
            development_validated_outcome: None,
            development_xml_archived_iteration: None,
            review_context_prepared_pass: None,
            review_prompt_prepared_pass: None,
            review_required_files_cleaned_pass: None,
            review_agent_invoked_pass: None,
            review_issues_xml_extracted_pass: None,
            review_validated_outcome: None,
            review_issues_markdown_written_pass: None,
            review_issue_snippets_extracted_pass: None,
            review_issues_xml_archived_pass: None,
            fix_prompt_prepared_pass: None,
            fix_required_files_cleaned_pass: None,
            fix_agent_invoked_pass: None,
            fix_analysis_agent_invoked_pass: None,
            fix_result_xml_extracted_pass: None,
            fix_validated_outcome: None,
            fix_result_xml_archived_pass: None,
            commit_prompt_prepared: false,
            commit_diff_prepared: false,
            commit_diff_empty: false,
            commit_diff_content_id_sha256: None,
            commit_agent_invoked: false,
            commit_required_files_cleaned: false,
            commit_xml_extracted: false,
            commit_validated_outcome: None,
            commit_xml_archived: false,
            commit_selected_files: checkpoint.commit_selected_files.clone(),
            commit_excluded_files: checkpoint.commit_excluded_files.clone(),
            commit_residual_retry_pass: if checkpoint.commit_residual_retry_pass > 0 {
                checkpoint.commit_residual_retry_pass
            } else if checkpoint.commit_is_second_pass {
                2
            } else {
                0
            },
            max_commit_residual_retries: 10,
            commit_residual_files: checkpoint.commit_residual_files.clone(),
            context_cleaned: false,
            agent_chain,
            rebase: rebase_state,
            commit: CommitState::NotStarted,
            execution_history,
            checkpoint_saved_count: 0,
            continuation: ContinuationState::new(),
            dev_fix_triggered: false,
            dev_fix_attempt_count: checkpoint.dev_fix_attempt_count,
            recovery_epoch: checkpoint.recovery_epoch,
            recovery_escalation_level: checkpoint.recovery_escalation_level,
            failed_phase_for_recovery: checkpoint.failed_phase_for_recovery,
            completion_marker_pending: false,
            completion_marker_is_failure: false,
            completion_marker_reason: None,
            gitignore_entries_ensured: false,
            prompt_inputs: checkpoint.prompt_inputs.unwrap_or_default(),
            prompt_permissions: checkpoint.prompt_permissions,
            last_substitution_log,
            template_validation_failed,
            template_validation_unsubstituted,
            metrics: {
                let continuation = ContinuationState::new();
                RunMetrics {
                    dev_iterations_completed: checkpoint.actual_developer_runs,
                    review_passes_completed: checkpoint.actual_reviewer_runs,
                    max_dev_iterations: checkpoint.total_iterations,
                    max_review_passes: checkpoint.total_reviewer_passes,
                    max_xsd_retry_count: continuation.max_xsd_retry_count,
                    max_dev_continuation_count: continuation.max_continue_count,
                    max_fix_continuation_count: continuation.max_fix_continue_count,
                    max_same_agent_retry_count: continuation.max_same_agent_retry_count,
                    ..RunMetrics::default()
                }
            },
            interrupted_by_user: checkpoint.interrupted_by_user,
            termination_resume_phase: None,
            pre_termination_commit_checked: false,
            // Cloud mode fields (checkpoint-safe, credential-free)
            cloud,
            pending_push_commit,
            git_auth_configured,
            pr_created,
            pr_url,
            push_count,
            push_retry_count,
            last_push_error,
            unpushed_commits,
            last_pushed_commit,
            pr_number,
            // Restore reducer-owned prompt history from checkpoint (RFC-007).
            // Legacy checkpoints stored HashMap<String, String>; the PromptHistoryEntry
            // custom deserializer migrates bare strings to PromptHistoryEntry { content, content_id: None }.
            prompt_history: checkpoint
                .prompt_history
                .unwrap_or_default()
                .into_iter()
                // checkpoint.prompt_history is already typed as HashMap<String, PromptHistoryEntry>
                // (via the updated PipelineCheckpoint type), so no conversion needed.
                .collect(),
            // Phase 4: Parallel worker state fields (reset on checkpoint resume)
            // Parallel workflows cannot be reliably resumed from checkpoint -
            // they must restart from the planning phase.
            parallel_plan: None,
            parallel_plan_validated: false,
            parallel_workers: Vec::new(),
            parallel_workers_completed: Vec::new(),
            parallel_plan_rejected_reason: None,
        }
    }
}

impl From<PipelineCheckpoint> for PipelineState {
    fn from(checkpoint: PipelineCheckpoint) -> Self {
        // `From` cannot accept configuration. Apply a conservative hard cap so
        // legacy checkpoints cannot load arbitrarily large execution history into memory.
        let limit = crate::config::Config::default().execution_history_limit;
        Self::from_checkpoint_with_execution_history_limit(checkpoint, limit)
    }
}

const fn map_checkpoint_phase(phase: CheckpointPhase) -> PipelinePhase {
    match phase {
        CheckpointPhase::Rebase | CheckpointPhase::Planning | CheckpointPhase::PreRebase => {
            PipelinePhase::Planning
        }
        CheckpointPhase::Development => PipelinePhase::Development,
        CheckpointPhase::Review => PipelinePhase::Review,
        CheckpointPhase::CommitMessage
        | CheckpointPhase::PostRebase
        | CheckpointPhase::PostRebaseConflict => PipelinePhase::CommitMessage,
        CheckpointPhase::FinalValidation => PipelinePhase::FinalValidation,
        CheckpointPhase::Complete => PipelinePhase::Complete,
        CheckpointPhase::PreRebaseConflict => PipelinePhase::Planning,
        CheckpointPhase::AwaitingDevFix => PipelinePhase::AwaitingDevFix,
        CheckpointPhase::Interrupted => PipelinePhase::Interrupted,
    }
}

fn map_checkpoint_rebase_state(rebase_state: &CheckpointRebaseState) -> RebaseState {
    match rebase_state {
        CheckpointRebaseState::NotStarted => RebaseState::NotStarted,
        CheckpointRebaseState::PreRebaseInProgress { upstream_branch }
        | CheckpointRebaseState::PostRebaseInProgress { upstream_branch } => {
            RebaseState::InProgress {
                original_head: "HEAD".to_string(),
                target_branch: upstream_branch.clone(),
            }
        }
        CheckpointRebaseState::PreRebaseCompleted { commit_oid }
        | CheckpointRebaseState::PostRebaseCompleted { commit_oid } => RebaseState::Completed {
            new_head: commit_oid.clone(),
        },
        CheckpointRebaseState::HasConflicts { files } => RebaseState::Conflicted {
            original_head: "HEAD".to_string(),
            target_branch: "main".to_string(),
            files: files.iter().map(PathBuf::from).collect(),
            resolution_attempts: 0,
        },
        CheckpointRebaseState::Failed { .. } => RebaseState::Skipped,
    }
}

// Tests are in a separate file to keep this file under the line limit.
include!("checkpoint_conversion_tests.rs");
