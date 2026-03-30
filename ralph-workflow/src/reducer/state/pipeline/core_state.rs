// Core PipelineState struct definition.
//
// This is the checkpoint payload - the single source of truth for pipeline progress.
// All state fields are immutable from the reducer's perspective. State transitions
// occur exclusively through the reduce function.
//
// Methods are in core_state_methods.rs; BoundedExecutionHistory is in
// bounded_execution_history.rs.

/// Immutable pipeline state - the single source of truth for pipeline progress.
///
/// This struct captures complete execution context and doubles as the checkpoint
/// data structure for resume functionality. Serialize it to JSON to save state;
/// deserialize to resume interrupted runs.
///
/// # Invariants
///
/// - `iteration` is always `<= total_iterations`
/// - `reviewer_pass` is always `<= total_reviewer_passes`
/// - `agent_chain` maintains fallback order and retry counts
/// - State transitions only occur through the `reduce` function
///
/// # See Also
///
/// - `reduce` for state transitions
/// - `determine_next_effect` for effect derivation
#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct PipelineState {
    pub phase: PipelinePhase,
    pub previous_phase: Option<PipelinePhase>,
    pub iteration: u32,
    pub total_iterations: u32,
    pub reviewer_pass: u32,
    pub total_reviewer_passes: u32,
    pub review_issues_found: bool,
    /// Tracks whether the planning prompt was prepared for the current iteration.
    #[serde(default)]
    pub planning_prompt_prepared_iteration: Option<u32>,
    /// Tracks whether planning required files were cleaned for the current iteration.
    #[serde(default, alias = "planning_xml_cleaned_iteration")]
    pub planning_required_files_cleaned_iteration: Option<u32>,
    /// Tracks whether the planning agent was invoked for the current iteration.
    #[serde(default)]
    pub planning_agent_invoked_iteration: Option<u32>,
    /// Tracks whether `.agent/tmp/plan.xml` was successfully extracted for the iteration.
    #[serde(default)]
    pub planning_xml_extracted_iteration: Option<u32>,
    /// Stores the validated outcome for the current planning iteration.
    #[serde(default)]
    pub planning_validated_outcome: Option<PlanningValidatedOutcome>,
    /// Tracks whether PLAN.md has been written for the current iteration.
    #[serde(default)]
    pub planning_markdown_written_iteration: Option<u32>,
    /// Tracks whether `.agent/tmp/plan.xml` was archived for the current iteration.
    #[serde(default)]
    pub planning_xml_archived_iteration: Option<u32>,
    /// Tracks whether development context was prepared for the current iteration.
    ///
    /// Used to sequence single-task development effects.
    #[serde(default)]
    pub development_context_prepared_iteration: Option<u32>,
    /// Tracks whether the development prompt was prepared for the current iteration.
    #[serde(default)]
    pub development_prompt_prepared_iteration: Option<u32>,
    /// Tracks whether development required files were cleaned for the current iteration.
    #[serde(default, alias = "development_xml_cleaned_iteration")]
    pub development_required_files_cleaned_iteration: Option<u32>,
    /// Tracks whether the developer agent was invoked for the current iteration.
    #[serde(default)]
    pub development_agent_invoked_iteration: Option<u32>,
    /// Tracks whether the analysis agent was invoked for the current iteration.
    ///
    /// Analysis agent runs after EVERY development iteration to produce
    /// an objective assessment of progress by comparing git diff against PLAN.md.
    /// This ensures continuous verification throughout the development phase.
    #[serde(default)]
    pub analysis_agent_invoked_iteration: Option<u32>,
    /// Tracks whether `.agent/tmp/development_result.xml` was extracted for the current iteration.
    #[serde(default)]
    pub development_xml_extracted_iteration: Option<u32>,
    /// Stores the validated development outcome for the current iteration.
    #[serde(default)]
    pub development_validated_outcome: Option<DevelopmentValidatedOutcome>,
    /// Tracks whether the development XML was archived for the current iteration.
    #[serde(default)]
    pub development_xml_archived_iteration: Option<u32>,
    /// Tracks whether review context was prepared for the current pass.
    ///
    /// Used to sequence single-task review effects (`PrepareReviewContext` -> ...).
    #[serde(default)]
    pub review_context_prepared_pass: Option<u32>,
    /// Tracks whether the review prompt was prepared for the current pass.
    #[serde(default)]
    pub review_prompt_prepared_pass: Option<u32>,
    /// Tracks whether review required files were cleaned for the current pass.
    #[serde(default, alias = "review_issues_xml_cleaned_pass")]
    pub review_required_files_cleaned_pass: Option<u32>,
    /// Tracks whether the reviewer agent was invoked for the current pass.
    #[serde(default)]
    pub review_agent_invoked_pass: Option<u32>,
    /// Tracks whether `.agent/tmp/issues.xml` was successfully extracted for the current pass.
    #[serde(default)]
    pub review_issues_xml_extracted_pass: Option<u32>,
    /// Stores the validated outcome for the current review pass.
    ///
    /// This is used to sequence post-validation single-task effects (write markdown,
    /// archive XML) before the reducer advances to the next pass/phase.
    #[serde(default)]
    pub review_validated_outcome: Option<ReviewValidatedOutcome>,
    /// Tracks whether ISSUES.md has been written for the current pass.
    #[serde(default)]
    pub review_issues_markdown_written_pass: Option<u32>,
    /// Tracks whether review issue snippets were extracted for the current pass.
    #[serde(default)]
    pub review_issue_snippets_extracted_pass: Option<u32>,
    #[serde(default)]
    pub review_issues_xml_archived_pass: Option<u32>,

    #[serde(default)]
    pub fix_prompt_prepared_pass: Option<u32>,

    /// Tracks whether fix required files were cleaned for the current pass.
    #[serde(default, alias = "fix_result_xml_cleaned_pass")]
    pub fix_required_files_cleaned_pass: Option<u32>,

    #[serde(default)]
    pub fix_agent_invoked_pass: Option<u32>,

    /// Tracks whether fix analysis agent was invoked for the current reviewer pass.
    ///
    /// This mirrors `analysis_agent_invoked_iteration` for development.
    /// After every fix agent invocation, an analysis agent verifies the fix.
    #[serde(default)]
    pub fix_analysis_agent_invoked_pass: Option<u32>,

    #[serde(default)]
    pub fix_result_xml_extracted_pass: Option<u32>,

    #[serde(default)]
    pub fix_validated_outcome: Option<FixValidatedOutcome>,

    #[serde(default)]
    pub fix_result_xml_archived_pass: Option<u32>,
    /// Tracks whether the commit prompt was prepared for the current commit attempt.
    #[serde(default)]
    pub commit_prompt_prepared: bool,
    /// Tracks whether the commit diff has been computed for the current attempt.
    #[serde(default)]
    pub commit_diff_prepared: bool,
    /// Tracks whether the computed commit diff was empty.
    #[serde(default)]
    pub commit_diff_empty: bool,
    /// Content identifier (sha256 hex) of the prepared commit diff.
    ///
    /// This is recorded when the diff is prepared and is used by orchestration guards
    /// to avoid reusing stale materialized prompt inputs across checkpoint resumes or
    /// when tmp artifacts change.
    #[serde(default)]
    pub commit_diff_content_id_sha256: Option<String>,
    /// Tracks whether the commit agent was invoked for the current commit attempt.
    #[serde(default)]
    pub commit_agent_invoked: bool,
    /// Tracks whether commit required files were cleaned for the current attempt.
    #[serde(default, alias = "commit_xml_cleaned")]
    pub commit_required_files_cleaned: bool,
    /// Tracks whether `.agent/tmp/commit_message.xml` was extracted for the current attempt.
    #[serde(default)]
    pub commit_xml_extracted: bool,
    /// Stores the validated commit outcome for the current attempt.
    #[serde(default)]
    pub commit_validated_outcome: Option<CommitValidatedOutcome>,
    /// Files to selectively stage for the next commit.
    ///
    /// Populated from `CommitXmlValidated.files` when the commit agent specifies
    /// a file list. Empty means commit all changed files (default behavior).
    ///
    /// Cleared whenever commit phase state is reset.
    #[serde(default)]
    pub commit_selected_files: Vec<String>,
    /// Excluded file metadata from the current commit agent output.
    ///
    /// Populated from `CommitXmlValidated.excluded_files`.
    /// Cleared whenever commit phase state is reset.
    #[serde(default)]
    pub commit_excluded_files: Vec<ExcludedFile>,
    /// The current automatic residual retry pass being executed for commit cleanup.
    ///
    /// `0` means no residual retry is in progress. `2` means the pipeline is executing
    /// the first automatic retry after residual files were found on pass 1. Higher values
    /// continue the unattended retry loop until the configured retry budget is exhausted.
    ///
    /// Cleared when commit phase state is reset at cycle boundary.
    #[serde(default)]
    pub commit_residual_retry_pass: u8,
    /// Maximum additional residual commit retries after the initial residual check.
    ///
    /// This is config-derived reducer state. `10` means pass 1 plus ten additional retry
    /// passes (carry forward after pass 11). `0` means carry forward immediately after pass 1.
    #[serde(default = "default_max_commit_residual_retries")]
    pub max_commit_residual_retries: u8,
    /// Files remaining uncommitted after the residual retry budget is exhausted.
    ///
    /// Carries forward to the next development cycle so the agent can address
    /// them in the next iteration. NOT cleared by commit phase reset.
    #[serde(default)]
    pub commit_residual_files: Vec<String>,
    /// Tracks whether commit XML has been archived for the current attempt.
    #[serde(default)]
    pub commit_xml_archived: bool,
    pub context_cleaned: bool,
    pub agent_chain: AgentChainState,
    pub rebase: RebaseState,
    pub commit: CommitState,
    #[serde(default)]
    pub execution_history: BoundedExecutionHistory,
    /// Count of `CheckpointSaved` events applied to state.
    ///
    /// This is a reducer-visible record of checkpoint saves, intended for
    /// observability and tests that enforce checkpointing happens via effects.
    #[serde(default)]
    pub checkpoint_saved_count: u32,
    /// Continuation state for development iterations.
    ///
    /// Tracks context from previous attempts when status is "partial" or "failed"
    /// to enable continuation-aware prompting.
    #[serde(default)]
    pub continuation: ContinuationState,

    /// Run-level execution metrics.
    ///
    /// This is the single source of truth for all iteration/attempt/retry/fallback
    /// statistics. Updated deterministically by the reducer based on events.
    #[serde(default)]
    pub metrics: RunMetrics,

    /// Whether `TriggerDevFixFlow` has been executed in the current `AwaitingDevFix` phase.
    ///
    /// This flag is set to true when `DevFixTriggered` event is reduced.
    /// It ensures the event loop allows at least one iteration to execute
    /// `TriggerDevFixFlow` before checking completion, preventing premature
    /// exit when max iterations is imminent.
    #[serde(default)]
    pub dev_fix_triggered: bool,

    /// Count of dev-fix recovery attempts for current failure.
    ///
    /// Tracks how many times we've attempted to recover from the same failure.
    /// Reset when recovery succeeds or when moving to a different failure context.
    /// Used to determine recovery escalation level:
    /// - Attempts 1-3: Retry same operation (Level 1)
    /// - Attempts 4-6: Reset to phase start (Level 2)
    /// - Attempts 7-9: Reset iteration counter (Level 3)
    /// - Attempts 10+: Reset to iteration 0 (Level 4)
    #[serde(default)]
    pub dev_fix_attempt_count: u32,

    /// Recovery epoch counter.
    ///
    /// Incremented each time an epoch-resetting recovery occurs:
    /// - Level 3 (`reset_iteration`): decrements iteration and restarts from Planning
    /// - Level 4 (`reset_to_iteration_zero`): resets to iteration 0
    ///
    /// Level 1 and Level 2 recoveries do NOT increment this counter because
    /// they do not change the iteration scope.
    ///
    /// Combined with the iteration counter in `PromptScopeKey`, this ensures
    /// replay identity advances atomically with recovery scope changes. Old
    /// `prompt_history` entries under pre-reset iteration keys are naturally
    /// bypassed because the iteration value in the key changes.
    ///
    /// Defaults to 0 for backward-compatibility with old checkpoints (via
    /// `#[serde(default)]`).
    #[serde(default)]
    pub recovery_epoch: u32,

    /// Current recovery escalation level.
    ///
    /// Tracks which recovery strategy is being applied:
    /// - 0: No recovery in progress
    /// - 1: Retry same operation (attempts 1-3)
    /// - 2: Reset to phase start (attempts 4-6)
    /// - 3: Reset iteration counter (attempts 7-9)
    /// - 4: Reset to iteration 0 (attempts 10+)
    #[serde(default)]
    pub recovery_escalation_level: u32,

    /// Snapshot of the phase where the current failure occurred.
    ///
    /// Preserved when transitioning to `AwaitingDevFix` so we know which phase
    /// to return to after dev-fix completes. Set when entering `AwaitingDevFix`,
    /// cleared when recovery succeeds or when reaching terminal state.
    #[serde(default)]
    pub failed_phase_for_recovery: Option<PipelinePhase>,

    /// Whether the pipeline should (re)attempt emitting a completion marker.
    ///
    /// This is reserved for explicit termination paths (safety valve / catastrophic
    /// external termination), not attempt-count based recovery escalation.
    ///
    /// When true, orchestration must derive `Effect::EmitCompletionMarkerAndTerminate`
    /// until it succeeds (`CompletionMarkerEmitted`) so external orchestration can
    /// reliably observe termination.
    #[serde(default)]
    pub completion_marker_pending: bool,

    /// Whether the pending completion marker represents a failure.
    #[serde(default)]
    pub completion_marker_is_failure: bool,

    /// Optional reason to include in the completion marker for failures.
    #[serde(default)]
    pub completion_marker_reason: Option<String>,

    /// Whether gitignore entries have been ensured for this pipeline run.
    ///
    /// Set to true after `Effect::EnsureGitignoreEntries` completes successfully.
    /// This prevents re-running the effect on every orchestration cycle.
    #[serde(default)]
    pub gitignore_entries_ensured: bool,

    /// Canonical, reducer-visible prompt inputs after oversize materialization.
    ///
    /// This is the single source of truth for any inline-vs-reference and
    /// model-budget truncation decisions. Effects must not silently re-truncate
    /// or re-reference content on retries; instead, they should consume these
    /// materialized inputs (or materialize them exactly once per content id).
    #[serde(default)]
    pub prompt_inputs: PromptInputsState,

    /// PROMPT.md permission lifecycle state.
    ///
    /// Tracks best-effort read-only protection during execution and restoration
    /// on all graceful termination paths (success and failure).
    #[serde(default)]
    pub prompt_permissions: PromptPermissionsState,

    /// Last template substitution log for validation and observability.
    ///
    /// Updated when `TemplateRendered` event is reduced. Used by the reducer
    /// to validate templates based on tracked substitutions rather than
    /// regex scanning rendered output.
    #[serde(default)]
    pub last_substitution_log: Option<crate::prompts::SubstitutionLog>,

    /// Whether the last template validation failed based on the substitution log.
    #[serde(default)]
    pub template_validation_failed: bool,

    /// Unsubstituted placeholders from the last rendered template.
    #[serde(default)]
    pub template_validation_unsubstituted: Vec<String>,

    /// True if pipeline was interrupted by user signal (Ctrl+C).
    /// This is the ONLY case where pre-termination commit safety check is skipped.
    /// All other termination paths (`AwaitingDevFix` exhaustion, programmatic interrupts, etc.)
    /// must commit before terminating.
    #[serde(default)]
    pub interrupted_by_user: bool,

    /// When set, the pipeline has detected uncommitted changes during the
    /// pre-termination safety check and routed back through the commit phase.
    ///
    /// After the commit is created (or explicitly skipped), the reducer must
    /// return to this phase and allow termination to proceed.
    #[serde(default)]
    pub termination_resume_phase: Option<PipelinePhase>,

    /// True if pre-termination commit safety check has been performed.
    /// Prevents infinite loops when checking for uncommitted changes before Complete/Interrupted.
    #[serde(default)]
    pub pre_termination_commit_checked: bool,

    // ========================================================================
    // Cloud Mode State Fields (INTERNAL USE ONLY)
    // ========================================================================
    //
    // These fields are only populated when cloud mode is enabled (internal env-config).
    // In CLI mode, they remain in their default (None/false) state and are not used.
    //
    // Cloud mode is environment-variable only and not exposed to users.
    /// Cloud configuration (redacted) for pure orchestration.
    ///
    /// This is a checkpoint-safe view (no secrets) derived from runtime cloud config.
    /// When enabled=false, all cloud-specific effects are skipped.
    #[serde(default)]
    pub cloud: crate::config::CloudStateConfig,

    /// Commit SHA pending push (cloud mode only, None in CLI mode).
    ///
    /// Set when `CommitCreated` event is reduced in cloud mode.
    /// Cleared when `CommitEvent::PushCompleted` is reduced.
    /// Used by orchestration to emit `PushToRemote` effects.
    #[serde(default)]
    pub pending_push_commit: Option<String>,

    /// Whether git auth has been configured this run (cloud mode only).
    ///
    /// Set to true when `CommitEvent::GitAuthConfigured` is reduced.
    /// Used to avoid re-configuring authentication on every push.
    #[serde(default)]
    pub git_auth_configured: bool,

    /// Whether PR has been created (cloud mode only).
    ///
    /// Set to true when `CommitEvent::PullRequestCreated` is reduced.
    /// Prevents duplicate PR creation attempts.
    #[serde(default)]
    pub pr_created: bool,

    /// URL of created PR (cloud mode only).
    ///
    /// Populated when `CommitEvent::PullRequestCreated` is reduced.
    /// Used for reporting and observability.
    #[serde(default)]
    pub pr_url: Option<String>,

    /// Count of successful push operations (cloud mode only).
    ///
    /// Incremented when `CommitEvent::PushCompleted` is reduced.
    /// Used for metrics and observability.
    #[serde(default)]
    pub push_count: u32,

    /// Consecutive push failure count for the current pending commit.
    ///
    /// Reset on `CommitEvent::PushCompleted` or when the pending push is cleared.
    #[serde(default)]
    pub push_retry_count: u32,

    /// Last push error message (cloud mode only).
    ///
    /// Used for completion reporting and observability. Must not contain secrets.
    #[serde(default)]
    pub last_push_error: Option<String>,

    /// Commits that failed to push after exhausting retries.
    ///
    /// This is used for completion reporting so failures are not silent.
    #[serde(default)]
    pub unpushed_commits: Vec<String>,

    /// SHA of the last successfully pushed commit (cloud mode only).
    ///
    /// Updated when `CommitEvent::PushCompleted` is reduced.
    /// Used for observability and debugging.
    #[serde(default)]
    pub last_pushed_commit: Option<String>,

    /// PR number for the created pull request (cloud mode only).
    ///
    /// Populated when `CommitEvent::PullRequestCreated` is reduced.
    /// Used for reporting and observability.
    #[serde(default)]
    pub pr_number: Option<u32>,

    // ========================================================================
    // Phase 4: Parallel Worker State Fields
    // ========================================================================
    //
    // Tracks parallel plan execution state for RFC-009 Phase 4 parallel workers.
    // These fields manage the parallel plan lifecycle:
    // parallel_plan -> EvaluateParallelPlan -> ParallelWorkersDispatched -> ParallelWorkerCompleted

    /// The current parallel plan being evaluated or executed.
    ///
    /// Set when `ParallelPlanProduced` or `ParallelPlanValidated` is reduced.
    /// Cleared when the parallel workflow completes or falls back to single-agent.
    #[serde(default)]
    pub parallel_plan: Option<crate::agents::session::ParallelPlan>,

    /// Identities of workers that have been dispatched for the parallel plan.
    ///
    /// Set when `ParallelWorkersDispatched` is reduced.
    /// Cleared when the parallel workflow completes or falls back.
    #[serde(default)]
    pub parallel_workers: Vec<crate::agents::session::WorkerIdentity>,

    /// IDs of workers that have completed their work units.
    ///
    /// Updated when `ParallelWorkerCompleted` is reduced.
    /// When all workers in `parallel_workers` have completed, verification is triggered.
    #[serde(default)]
    pub parallel_workers_completed: Vec<String>,

    /// Reason for parallel plan rejection, if the plan was rejected.
    ///
    /// Set when `ParallelPlanRejected` is reduced.
    /// Cleared when falling back to single-agent mode completes.
    #[serde(default)]
    pub parallel_plan_rejected_reason: Option<String>,

    /// Whether the parallel plan has been validated and is ready for dispatch.
    ///
    /// Set to true when `ParallelPlanValidated` is reduced.
    /// Set to false when `ParallelPlanProduced` is reduced (new plan needs evaluation).
    /// Cleared when the parallel workflow completes or falls back.
    #[serde(default)]
    pub parallel_plan_validated: bool,

    /// Whether the verifier has completed review of parallel worker outputs.
    ///
    /// Set to true when `VerifierCompleted` is reduced.
    /// Used by the orchestration layer to determine the next action.
    #[serde(default)]
    pub parallel_verification_completed: bool,

    /// Current iteration of the parallel verification loop.
    ///
    /// Incremented each time `ParallelWorkReworked` is reduced.
    /// Used as a max-iteration guard to prevent infinite verification loops.
    /// When this reaches the max verification iterations, the workflow falls back to single-agent.
    #[serde(default)]
    pub parallel_verification_iteration: u32,

    /// Reducer-owned prompt history for deterministic resume replay (RFC-007).
    ///
    /// Maps `PromptScopeKey::to_string()` keys to `PromptHistoryEntry` values
    /// containing the generated prompt and optional content-id for stale-replay detection.
    ///
    /// # Ownership Contract
    ///
    /// This field is the **single source of truth** for prompt history. It replaces
    /// the `PhaseContext::prompt_history` side-channel, ensuring that all history
    /// updates are observable as reducer events (`PromptCaptured`) and are part of
    /// the pure event loop.
    ///
    /// # Clearing Semantics
    ///
    /// Cleared atomically alongside `recovery_epoch` increment on Level-3 and Level-4
    /// recovery to prevent stale prompts from crossing iteration-scope boundaries.
    ///
    /// Defaults to empty map for backward-compatibility with checkpoints that pre-date
    /// this field (they populate via `from_checkpoint_with_execution_history_limit`).
    #[serde(default)]
    pub prompt_history: std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
}

const fn default_max_commit_residual_retries() -> u8 {
    10
}
