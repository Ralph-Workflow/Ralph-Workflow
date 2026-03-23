/// Enhanced pipeline checkpoint for resume functionality.
///
/// Contains comprehensive state needed to resume an interrupted pipeline
/// exactly where it left off, including CLI arguments, agent configurations,
/// rebase state, and file checksums for validation.
///
/// This is inspired by video game save states - capturing the complete
/// execution context to enable seamless recovery.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PipelineCheckpoint {
    /// Checkpoint format version (for future compatibility)
    pub version: u32,

    // === Core pipeline state ===
    /// Current pipeline phase
    pub phase: PipelinePhase,

    /// Current iteration number (for developer iterations).
    ///
    /// **Semantics:** This represents the iteration currently being worked on,
    /// NOT the number of completed iterations. On resume, the orchestration
    /// will re-execute this iteration from the beginning (all progress flags
    /// are reset to None during checkpoint-to-state conversion).
    ///
    /// **Example:** If `iteration=1, total_iterations=1`, this means we're working
    /// on the first (and only) iteration, which may or may not be complete.
    /// The orchestration boundary check uses inclusive comparison:
    /// `iteration < total_iterations || (iteration == total_iterations && total_iterations > 0)`
    /// This ensures work is re-run at boundaries rather than skipped.
    pub iteration: u32,

    /// Total iterations configured
    pub total_iterations: u32,

    /// Current reviewer pass number.
    ///
    /// **Semantics:** This represents the review pass currently being worked on,
    /// NOT the number of completed passes. On resume, the orchestration will
    /// re-execute this pass from the beginning (all progress flags are reset
    /// to None during checkpoint-to-state conversion).
    ///
    /// **Example:** If `reviewer_pass=2, total_reviewer_passes=2`, this means
    /// we're working on the second (and final) pass, which may or may not be
    /// complete. The orchestration boundary check uses inclusive comparison:
    /// `reviewer_pass < total_reviewer_passes || (reviewer_pass == total_reviewer_passes && total_reviewer_passes > 0)`
    /// This ensures work is re-run at boundaries rather than skipped.
    pub reviewer_pass: u32,

    /// Total reviewer passes configured
    pub total_reviewer_passes: u32,

    // === Metadata ===
    /// Timestamp when checkpoint was saved
    pub timestamp: String,
    /// Developer agent display name
    pub developer_agent: String,
    /// Reviewer agent display name
    pub reviewer_agent: String,

    // === Enhanced state capture ===
    /// CLI argument snapshot
    pub cli_args: CliArgsSnapshot,
    /// Developer agent configuration snapshot
    pub developer_agent_config: AgentConfigSnapshot,
    /// Reviewer agent configuration snapshot
    pub reviewer_agent_config: AgentConfigSnapshot,
    /// Rebase state tracking
    pub rebase_state: RebaseState,

    // === Validation data ===
    /// Path to config file used for this run (if any)
    pub config_path: Option<String>,
    /// Checksum of config file (for validation on resume)
    pub config_checksum: Option<String>,
    /// Working directory when checkpoint was created
    pub working_dir: String,
    /// Checksum of PROMPT.md (for validation on resume)
    pub prompt_md_checksum: Option<String>,

    // === Additional state for exact restoration ===
    /// Git user name for commits (if overridden)
    pub git_user_name: Option<String>,
    /// Git user email for commits (if overridden)
    pub git_user_email: Option<String>,

    // === Run identification and lineage (v2+) ===
    /// Unique identifier for this run (UUID v4)
    pub run_id: String,
    /// Parent run ID if this is a resumed session
    pub parent_run_id: Option<String>,
    /// Number of times this session has been resumed
    pub resume_count: u32,
    /// Logging `run_id` (timestamp-based) for per-run log directory
    /// Format: YYYY-MM-DD_HH-mm-ss.SSSZ[-NN]
    /// This is separate from `run_id` which uses format "run-{uuid}"
    #[serde(skip_serializing_if = "Option::is_none")]
    pub log_run_id: Option<String>,

    // === Actual execution state (v2+) ===
    /// Actual number of developer iterations that completed
    pub actual_developer_runs: u32,
    /// Actual number of reviewer passes that completed
    pub actual_reviewer_runs: u32,

    // === Hardened resume state (v3+) ===
    /// Execution history tracking for idempotent recovery
    #[serde(skip_serializing_if = "Option::is_none")]
    pub execution_history: Option<crate::checkpoint::ExecutionHistory>,
    /// File system state for validation on resume
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file_system_state: Option<crate::checkpoint::FileSystemState>,
    /// Stored prompts used during this run
    /// Stored prompts used during this run.
    ///
    /// Value type changed from `String` to [`PromptHistoryEntry`] in `replay_metadata_version` 1.
    /// The [`crate::prompts::PromptHistoryEntry`] custom deserializer accepts both the legacy
    /// bare-string format (version 0) and the new object format (version 1) for backward compatibility.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prompt_history:
        Option<std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>>,

    /// Prompt history schema version.
    ///
    /// - `0` (default): legacy format where `prompt_history` values were bare strings.
    /// - `1`: current format where values are [`crate::prompts::PromptHistoryEntry`] objects.
    ///
    /// Defaults to `0` via `#[serde(default)]` so old checkpoints without the field
    /// deserialize with the expected legacy semantics.
    #[serde(default)]
    pub replay_metadata_version: u32,

    /// Environment snapshot for idempotent recovery
    #[serde(skip_serializing_if = "Option::is_none")]
    pub env_snapshot: Option<EnvironmentSnapshot>,

    /// Reducer-managed prompt input materialization state.
    ///
    /// This allows resumed pipelines to avoid re-materializing oversize inputs
    /// (and re-emitting oversize warnings) when the underlying content and
    /// consumer signature are unchanged.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prompt_inputs: Option<crate::reducer::state::PromptInputsState>,

    /// Reducer-managed PROMPT.md permission lifecycle state.
    ///
    /// This allows resumed pipelines to finish restoring permissions if they
    /// were interrupted after locking the prompt file.
    #[serde(default)]
    pub prompt_permissions: crate::reducer::state::PromptPermissionsState,

    /// Last template substitution log for validation and observability.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_substitution_log: Option<crate::prompts::SubstitutionLog>,

    // === Recovery state (v3+) ===
    /// Count of dev-fix recovery attempts for the current failure.
    ///
    /// Preserved across checkpoint/resume so escalation is deterministic.
    #[serde(default)]
    pub dev_fix_attempt_count: u32,

    /// Recovery epoch counter (RFC-007).
    ///
    /// Incremented each time an epoch-resetting recovery occurs (level-3/4 resets).
    /// Preserved across checkpoint/resume to ensure replay identity advances correctly.
    /// Defaults to 0 for backward-compatibility with old checkpoints.
    #[serde(default)]
    pub recovery_epoch: u32,

    /// Current recovery escalation level (0-4).
    ///
    /// Preserved across checkpoint/resume so recovery does not restart at level 1.
    #[serde(default)]
    pub recovery_escalation_level: u32,

    /// Snapshot of the phase where the current failure occurred.
    ///
    /// Preserved across checkpoint/resume so recovery returns to the correct phase.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub failed_phase_for_recovery: Option<crate::reducer::event::PipelinePhase>,

    /// Whether this interruption was triggered by user action (Ctrl+C / SIGINT).
    ///
    /// When true, the pre-termination commit safety check is skipped because the
    /// user explicitly chose to interrupt execution. All other termination paths
    /// (`AwaitingDevFix` exhaustion, `DiffFailed`, agent failures, etc.) MUST commit
    /// uncommitted work before terminating.
    ///
    /// This flag is set by the SIGINT handler when the user presses Ctrl+C and
    /// preserved across checkpoint/resume cycles.
    #[serde(default)]
    pub interrupted_by_user: bool,

    /// Commit residual retry tracking state.
    ///
    /// These fields are reducer-owned state and must be preserved across checkpoint/resume
    /// so unattended runs can deterministically resume a residual retry pass and/or carry
    /// residual files forward to the next cycle.
    #[serde(default)]
    pub commit_is_second_pass: bool,

    /// The current automatic residual retry pass being executed.
    ///
    /// `0` means no retry is active. Older checkpoints may omit this field and instead
    /// set `commit_is_second_pass = true`, which restores as retry pass `2`.
    #[serde(default)]
    pub commit_residual_retry_pass: u8,

    /// Files selected for the most recent commit pass.
    ///
    /// When non-empty, this indicates the last commit pass was selective and residual
    /// handling must run (pass-2 follow-up / carry-forward) even after checkpoint/resume.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub commit_selected_files: Vec<String>,

    /// Files excluded from the most recent commit pass, with reasons.
    ///
    /// This is audit/observability metadata that may be used for deterministic resume.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub commit_excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile>,

    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub commit_residual_files: Vec<String>,

    /// Cloud-mode state needed for checkpoint/resume semantics.
    ///
    /// This is a checkpoint-safe, redacted view of cloud state: it must not contain
    /// API tokens, git tokens, or any other credential material.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cloud_state: Option<CloudCheckpointState>,
}

impl PipelineCheckpoint {
    /// Create a new checkpoint with comprehensive state capture.
    ///
    /// This is the main constructor for creating checkpoints during pipeline execution.
    /// It captures all necessary state to enable exact restoration of the pipeline.
    ///
    /// # Arguments
    ///
    /// * `params` - All checkpoint parameters bundled in a struct
    pub fn from_params(params: CheckpointParams<'_>) -> Self {
        Self {
            version: CHECKPOINT_VERSION,
            phase: params.phase,
            iteration: params.iteration,
            total_iterations: params.total_iterations,
            reviewer_pass: params.reviewer_pass,
            total_reviewer_passes: params.total_reviewer_passes,
            timestamp: timestamp(),
            developer_agent: params.developer_agent.to_string(),
            reviewer_agent: params.reviewer_agent.to_string(),
            cli_args: params.cli_args,
            developer_agent_config: params.developer_agent_config,
            reviewer_agent_config: params.reviewer_agent_config,
            rebase_state: params.rebase_state,
            config_path: params.config_path,
            config_checksum: params.config_checksum,
            working_dir: params.working_dir,
            prompt_md_checksum: params.prompt_md_checksum,
            git_user_name: params.git_user_name.map(String::from),
            git_user_email: params.git_user_email.map(String::from),
            // New v2 fields
            run_id: params.run_id.to_string(),
            parent_run_id: params.parent_run_id.map(String::from),
            resume_count: params.resume_count,
            actual_developer_runs: params.actual_developer_runs,
            actual_reviewer_runs: params.actual_reviewer_runs,
            // New v3 fields - initialize as None, will be populated by caller
            execution_history: None,
            file_system_state: None,
            prompt_history: None,
            replay_metadata_version: 1,
            env_snapshot: None,
            prompt_inputs: None,
            prompt_permissions: crate::reducer::state::PromptPermissionsState::default(),
            log_run_id: None,
            last_substitution_log: None,
            dev_fix_attempt_count: 0,
            recovery_epoch: 0,
            recovery_escalation_level: 0,
            failed_phase_for_recovery: None,
            interrupted_by_user: false,
            commit_is_second_pass: false,
            commit_residual_retry_pass: 0,
            commit_selected_files: Vec::new(),
            commit_excluded_files: Vec::new(),
            commit_residual_files: Vec::new(),
            cloud_state: None,
        }
    }

    /// Apply recovery state from pipeline state.
    #[must_use]
    pub fn with_recovery_state(mut self, state: &crate::reducer::state::PipelineState) -> Self {
        self.dev_fix_attempt_count = state.dev_fix_attempt_count;
        self.recovery_epoch = state.recovery_epoch;
        self.recovery_escalation_level = state.recovery_escalation_level;
        self.failed_phase_for_recovery = state.failed_phase_for_recovery;
        self.interrupted_by_user = state.interrupted_by_user;
        self.commit_is_second_pass = state.commit_residual_retry_pass == 2;
        self.commit_residual_retry_pass = state.commit_residual_retry_pass;
        self.commit_selected_files = state.commit_selected_files.clone();
        self.commit_excluded_files = state.commit_excluded_files.clone();
        self.commit_residual_files = state.commit_residual_files.clone();
        if state.cloud.enabled {
            self.cloud_state =
                Some(crate::checkpoint::state::CloudCheckpointState::from_pipeline_state(state));
        }
        self
    }

    /// Get a human-readable description of the checkpoint.
    ///
    /// Returns a string describing the current phase and progress,
    /// suitable for display to the user when resuming.
    #[must_use]
    pub fn description(&self) -> String {
        match self.phase {
            PipelinePhase::Rebase => "Rebase in progress".to_string(),
            PipelinePhase::Planning => {
                format!(
                    "Planning phase, iteration {}/{}",
                    self.iteration, self.total_iterations
                )
            }
            PipelinePhase::Development => {
                format!(
                    "Development iteration {}/{}",
                    self.iteration, self.total_iterations
                )
            }
            PipelinePhase::Review => {
                if self.reviewer_pass > 0 {
                    format!(
                        "Verification review {}/{}",
                        self.reviewer_pass, self.total_reviewer_passes
                    )
                } else {
                    "Initial review".to_string()
                }
            }
            PipelinePhase::CommitMessage => "Commit message generation".to_string(),
            PipelinePhase::FinalValidation => "Final validation".to_string(),
            PipelinePhase::Complete => "Pipeline complete".to_string(),
            PipelinePhase::PreRebase => "Pre-development rebase".to_string(),
            PipelinePhase::PreRebaseConflict => "Pre-rebase conflict resolution".to_string(),
            PipelinePhase::PostRebase => "Post-review rebase".to_string(),
            PipelinePhase::PostRebaseConflict => "Post-rebase conflict resolution".to_string(),
            PipelinePhase::AwaitingDevFix => {
                "Awaiting development agent to fix pipeline failure".to_string()
            }
            PipelinePhase::Interrupted => {
                let context = if self.iteration > 0 && self.iteration < self.total_iterations {
                    Some(format!(
                        "during development (iteration {}/{})",
                        self.iteration, self.total_iterations
                    ))
                } else if self.iteration >= self.total_iterations {
                    if self.reviewer_pass > 0 {
                        Some(format!(
                            "during review (pass {}/{})",
                            self.reviewer_pass, self.total_reviewer_passes
                        ))
                    } else {
                        Some("after development phase".to_string())
                    }
                } else {
                    Some("during pipeline initialization".to_string())
                };

                ["Interrupted".to_string()]
                    .into_iter()
                    .chain(context)
                    .collect::<Vec<_>>()
                    .join(" ")
            }
        }
    }
}

/// Cloud-mode checkpoint state.
///
/// This is intentionally *credential-free* and safe to persist in checkpoints.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct CloudCheckpointState {
    #[serde(default, alias = "cloud_config")]
    pub cloud: crate::config::CloudStateConfig,
    #[serde(default)]
    pub pending_push_commit: Option<String>,
    #[serde(default)]
    pub git_auth_configured: bool,
    #[serde(default)]
    pub pr_created: bool,
    #[serde(default)]
    pub pr_url: Option<String>,
    #[serde(default)]
    pub pr_number: Option<u32>,
    #[serde(default)]
    pub push_count: u32,
    #[serde(default)]
    pub push_retry_count: u32,
    #[serde(default)]
    pub last_push_error: Option<String>,
    #[serde(default)]
    pub unpushed_commits: Vec<String>,
    #[serde(default)]
    pub last_pushed_commit: Option<String>,
}

impl CloudCheckpointState {
    #[must_use]
    pub fn from_pipeline_state(state: &crate::reducer::state::PipelineState) -> Self {
        Self {
            cloud: state.cloud.clone(),
            pending_push_commit: state.pending_push_commit.clone(),
            git_auth_configured: state.git_auth_configured,
            pr_created: state.pr_created,
            pr_url: state.pr_url.clone(),
            pr_number: state.pr_number,
            push_count: state.push_count,
            push_retry_count: state.push_retry_count,
            last_push_error: state.last_push_error.clone(),
            unpushed_commits: state.unpushed_commits.clone(),
            last_pushed_commit: state.last_pushed_commit.clone(),
        }
    }
}

/// Get current timestamp in "YYYY-MM-DD HH:MM:SS" format.
#[must_use]
pub fn timestamp() -> String {
    Local::now().format("%Y-%m-%d %H:%M:%S").to_string()
}
