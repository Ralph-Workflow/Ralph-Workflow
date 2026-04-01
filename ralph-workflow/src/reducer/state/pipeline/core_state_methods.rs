// PipelineState methods: accessors and initialization.
//
// This file contains the impl block for PipelineState with constructor
// methods and utility accessors. Pure query helpers live in helpers.rs.

impl PipelineState {
    #[must_use]
    pub const fn execution_history(&self) -> &std::collections::VecDeque<ExecutionStep> {
        self.execution_history.as_deque()
    }

    #[must_use]
    pub fn execution_history_len(&self) -> usize {
        self.execution_history.len()
    }

    #[must_use]
    pub fn initial(developer_iters: u32, reviewer_reviews: u32) -> Self {
        Self::initial_with_continuation(
            developer_iters,
            reviewer_reviews,
            &ContinuationState::new(),
        )
    }

    /// Create initial state with custom continuation limits from config.
    ///
    /// Use this when you need to load XSD retry and continuation limits from unified config.
    /// Example:
    /// ```ignore
    /// // Config semantics: max_dev_continuations counts continuation attempts *beyond*
    /// // the initial attempt. ContinuationState::max_continue_count semantics are
    /// // "maximum total attempts including initial".
    /// let continuation = ContinuationState::with_limits(
    ///     config.general.max_xsd_retries,
    ///     1 + config.general.max_dev_continuations,
    ///     config.general.max_same_agent_retries,
    /// );
    /// let state = PipelineState::initial_with_continuation(dev_iters, reviews, continuation);
    /// ```
    #[must_use]
    pub fn initial_with_continuation(
        developer_iters: u32,
        reviewer_reviews: u32,
        continuation: &ContinuationState,
    ) -> Self {
        // Determine initial phase based on what work needs to be done
        let initial_phase = if developer_iters == 0 {
            // No development iterations → skip Planning and Development
            if reviewer_reviews == 0 {
                // No review passes either → go straight to commit
                PipelinePhase::CommitMessage
            } else {
                PipelinePhase::Review
            }
        } else {
            PipelinePhase::Planning
        };

        Self {
            phase: initial_phase,
            previous_phase: None,
            iteration: 0,
            total_iterations: developer_iters,
            reviewer_pass: 0,
            total_reviewer_passes: reviewer_reviews,
            review_issues_found: false,
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
            commit_selected_files: Vec::new(),
            commit_excluded_files: Vec::new(),
            commit_residual_retry_pass: 0,
            max_commit_residual_retries: 10,
            commit_residual_files: Vec::new(),
            context_cleaned: false,
            agent_chain: AgentChainState::initial(),
            rebase: RebaseState::NotStarted,
            commit: CommitState::NotStarted,
            execution_history: BoundedExecutionHistory::new(),
            checkpoint_saved_count: 0,
            continuation: continuation.clone(),
            connectivity: ConnectivityState::default(),
            dev_fix_triggered: false,
            dev_fix_attempt_count: 0,
            recovery_epoch: 0,
            recovery_escalation_level: 0,
            failed_phase_for_recovery: None,
            completion_marker_pending: false,
            completion_marker_is_failure: false,
            completion_marker_reason: None,
            gitignore_entries_ensured: false,
            prompt_inputs: PromptInputsState::default(),
            prompt_permissions: PromptPermissionsState::default(),
            last_substitution_log: None,
            template_validation_failed: false,
            template_validation_unsubstituted: Vec::new(),
            metrics: RunMetrics::new(developer_iters, reviewer_reviews, continuation),
            interrupted_by_user: false,
            termination_resume_phase: None,
            pre_termination_commit_checked: false,
            // Cloud mode fields (all default/disabled)
            cloud: crate::config::CloudStateConfig::disabled(),
            pending_push_commit: None,
            git_auth_configured: false,
            pr_created: false,
            pr_url: None,
            push_count: 0,
            push_retry_count: 0,
            last_push_error: None,
            unpushed_commits: Vec::new(),
            last_pushed_commit: None,
            pr_number: None,
            prompt_history: std::collections::HashMap::new(),
        }
    }

    /// Returns true if the pipeline is in a terminal state for event loop purposes.
    ///
    /// # Terminal States
    ///
    /// - **Complete phase**: Always terminal (successful completion)
    /// - **Interrupted phase**: Terminal under these conditions:
    ///   1. A checkpoint has been saved (normal Ctrl+C interruption path)
    ///   2. Transitioning from `AwaitingDevFix` phase (failure handling completed)
    ///
    /// # `AwaitingDevFix` → Interrupted Path
    ///
    /// When the pipeline terminates via completion marker emission, it transitions
    /// through `AwaitingDevFix` where:
    /// 1. Orchestration derives `EmitCompletionMarkerAndTerminate`
    /// 2. The handler writes the completion marker to filesystem
    /// 3. `CompletionMarkerEmitted` transitions the reducer state to Interrupted
    ///
    /// At this point, the completion marker has been written, signaling external
    /// orchestration that the pipeline has terminated. The `SaveCheckpoint` effect
    /// will execute next, but the phase is already considered terminal because
    /// the failure has been properly signaled.
    #[must_use]
    pub const fn is_terminal(&self) -> bool {
        use crate::reducer::event::PipelinePhase;
        match self.phase {
            PipelinePhase::Complete => true,
            PipelinePhase::Interrupted => {
                self.checkpoint_saved_count > 0
                    || matches!(self.previous_phase, Some(PipelinePhase::AwaitingDevFix))
            }
            _ => false,
        }
    }

    /// Add an execution step to the history with automatic bounding.
    ///
    /// This method implements a ring buffer strategy: when the history exceeds
    /// the configured limit, the oldest entries are dropped to maintain a bounded
    /// memory footprint. This prevents unbounded memory growth during long-running
    /// pipelines while preserving recent execution context for debugging.
    ///
    /// # Arguments
    ///
    /// * `step` - The execution step to add
    /// * `limit` - Maximum number of entries to keep (from config)
    ///
    /// # Memory Behavior
    ///
    /// With default limit of 1000 entries:
    /// - Memory usage: ~51 KB heap (based on recorded baseline measurements)
    /// - Checkpoint size: ~375 KB serialized
    /// - Growth: Bounded (oldest entries dropped when limit reached)
    #[must_use]
    pub fn with_execution_step(self, step: ExecutionStep, limit: usize) -> Self {
        Self {
            execution_history: self.execution_history.with_step(step, limit),
            ..self
        }
    }

    #[must_use]
    pub fn with_execution_history(
        self,
        history: std::collections::VecDeque<ExecutionStep>,
        limit: usize,
    ) -> Self {
        Self {
            execution_history: self.execution_history.with_replaced(history, limit),
            ..self
        }
    }

    /// Mutable method for backward compatibility during migration.
    /// Prefer `with_execution_step` for new code.
    pub fn add_execution_step(&mut self, step: ExecutionStep, limit: usize) {
        let new_history = std::mem::take(&mut self.execution_history).with_step(step, limit);
        self.execution_history = new_history;
    }

}
