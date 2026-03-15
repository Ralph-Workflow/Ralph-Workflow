// Pure helper methods for PipelineState.
//
// These methods provide state queries and derived values. They contain
// no side effects and operate solely on the immutable state struct.

impl PipelineState {
    const fn initial_phase_for_run_configuration(&self) -> PipelinePhase {
        // Keep consistent with PipelineState::initial_with_continuation.
        if self.total_iterations == 0 {
            if self.total_reviewer_passes == 0 {
                PipelinePhase::CommitMessage
            } else {
                PipelinePhase::Review
            }
        } else {
            PipelinePhase::Planning
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
    ///
    /// # Edge Cases
    ///
    /// An Interrupted phase without a checkpoint and without `previous_phase` context
    /// is NOT considered terminal. This can occur when resuming from a checkpoint
    /// that was interrupted mid-execution.
    ///
    /// # Non-Terminating Pipeline Architecture
    ///
    /// Internal failures are handled via the `AwaitingDevFix` recovery loop.
    /// Completion markers are emitted only when the pipeline is actually terminating
    /// due to explicit external/catastrophic conditions.
    ///
    /// Terminal states:
    /// - `Complete`: Normal successful completion
    /// - `Interrupted` with checkpoint saved: Resumable state
    /// - `Interrupted` from `AwaitingDevFix`: Completion marker written, failure signaled
    #[must_use]
    pub const fn is_complete(&self) -> bool {
        matches!(self.phase, PipelinePhase::Complete)
            || (matches!(self.phase, PipelinePhase::Interrupted)
                && (self.checkpoint_saved_count > 0
                    // CRITICAL: AwaitingDevFix→Interrupted transition means completion marker
                    // was written during EmitCompletionMarkerAndTerminate. This is terminal even without
                    // checkpoint because the failure has been properly signaled to orchestration.
                    // This prevents "Pipeline exited without completion marker" bug.
                    || matches!(self.previous_phase, Some(PipelinePhase::AwaitingDevFix))))
    }

    #[must_use]
    pub fn current_head(&self) -> String {
        self.rebase
            .current_head()
            .unwrap_or_else(|| "HEAD".to_string())
    }

    #[must_use]
    pub fn fix_drain_active(&self) -> bool {
        self.agent_chain.current_drain == crate::agents::AgentDrain::Fix
            || self.review_issues_found
            || self.fix_prompt_prepared_pass.is_some()
            || self.fix_required_files_cleaned_pass.is_some()
            || self.fix_agent_invoked_pass.is_some()
            || self.fix_analysis_agent_invoked_pass.is_some()
            || self.fix_result_xml_extracted_pass.is_some()
            || self.fix_validated_outcome.is_some()
            || self.fix_result_xml_archived_pass.is_some()
            || self.continuation.fix_continue_pending
            || self.continuation.fix_continuation_attempt > 0
            || self.continuation.fix_status.is_some()
            || self.continuation.fix_previous_summary.is_some()
            || self.continuation.last_fix_xsd_error.is_some()
    }

    #[must_use]
    pub fn runtime_drain(&self) -> crate::agents::AgentDrain {
        match self.phase {
            PipelinePhase::Planning => crate::agents::AgentDrain::Planning,
            PipelinePhase::Development => {
                if self.agent_chain.current_drain == crate::agents::AgentDrain::Analysis {
                    crate::agents::AgentDrain::Analysis
                } else {
                    crate::agents::AgentDrain::Development
                }
            }
            PipelinePhase::Review => {
                // If we're in Analysis drain during Review phase (fix analysis step),
                // return Analysis drain
                if self.agent_chain.current_drain == crate::agents::AgentDrain::Analysis {
                    crate::agents::AgentDrain::Analysis
                } else if self.fix_drain_active() {
                    crate::agents::AgentDrain::Fix
                } else {
                    crate::agents::AgentDrain::Review
                }
            }
            PipelinePhase::CommitMessage => crate::agents::AgentDrain::Commit,
            _ => self.agent_chain.current_drain,
        }
    }

    /// Clear phase-specific progress flags for the given phase.
    ///
    /// Used by Level 2 recovery (`PhaseStart`) to restart a phase from scratch
    /// while preserving iteration counters and other global state.
    pub(crate) fn clear_phase_flags(&self, phase: PipelinePhase) -> Self {
        match phase {
            PipelinePhase::Planning => self.clear_planning_flags(),
            PipelinePhase::Development => self.clear_development_flags(),
            PipelinePhase::Review => self.clear_review_flags(),
            PipelinePhase::CommitMessage => self.clear_commit_flags(),
            _ => self.clone(),
        }
    }

    /// Clear all Planning phase progress flags.
    fn clear_planning_flags(&self) -> Self {
        Self {
            planning_prompt_prepared_iteration: None,
            planning_required_files_cleaned_iteration: None,
            planning_agent_invoked_iteration: None,
            planning_xml_extracted_iteration: None,
            planning_validated_outcome: None,
            planning_markdown_written_iteration: None,
            planning_xml_archived_iteration: None,
            ..self.clone()
        }
    }

    /// Clear all Development phase progress flags.
    fn clear_development_flags(&self) -> Self {
        Self {
            development_context_prepared_iteration: None,
            development_prompt_prepared_iteration: None,
            development_required_files_cleaned_iteration: None,
            development_agent_invoked_iteration: None,
            analysis_agent_invoked_iteration: None,
            development_xml_extracted_iteration: None,
            development_validated_outcome: None,
            development_xml_archived_iteration: None,
            ..self.clone()
        }
    }

    /// Clear all Review phase progress flags.
    fn clear_review_flags(&self) -> Self {
        Self {
            review_context_prepared_pass: None,
            review_prompt_prepared_pass: None,
            review_required_files_cleaned_pass: None,
            review_agent_invoked_pass: None,
            review_issues_xml_extracted_pass: None,
            review_validated_outcome: None,
            review_issues_markdown_written_pass: None,
            review_issue_snippets_extracted_pass: None,
            review_issues_xml_archived_pass: None,
            review_issues_found: false,
            fix_prompt_prepared_pass: None,
            fix_required_files_cleaned_pass: None,
            fix_agent_invoked_pass: None,
            fix_analysis_agent_invoked_pass: None,
            fix_result_xml_extracted_pass: None,
            fix_validated_outcome: None,
            fix_result_xml_archived_pass: None,
            ..self.clone()
        }
    }

    /// Clear all `CommitMessage` phase progress flags.
    fn clear_commit_flags(&self) -> Self {
        Self {
            commit_prompt_prepared: false,
            commit_diff_prepared: false,
            commit_diff_empty: false,
            commit_diff_content_id_sha256: None,
            commit_agent_invoked: false,
            commit_required_files_cleaned: false,
            commit_xml_extracted: false,
            commit_validated_outcome: None,
            commit_xml_archived: false,
            commit: CommitState::NotStarted,
            ..self.clone()
        }
    }

    /// Reset iteration counter (Level 3 recovery).
    ///
    /// Decrements the iteration counter (with floor at 0) and clears all
    /// phase flags to restart the current iteration from Planning phase.
    pub(crate) fn reset_iteration(&self) -> Self {
        let new_iteration = if self.total_iterations == 0 {
            0
        } else {
            self.iteration.saturating_sub(1)
        };
        let initial_phase = self.initial_phase_for_run_configuration();
        let prompt_inputs = self
            .prompt_inputs
            .clone()
            .with_planning_cleared()
            .with_development_cleared()
            .with_commit_cleared()
            .with_review_cleared()
            .with_xsd_retry_cleared();
        Self {
            iteration: new_iteration,
            phase: initial_phase,
            context_cleaned: false,
            gitignore_entries_ensured: false,
            prompt_inputs,
            continuation: self.continuation.clone().reset(),
            ..self.clone()
        }
        .clear_planning_flags()
        .clear_development_flags()
        .clear_commit_flags()
        .clear_review_flags()
    }

    /// Reset to iteration 0 (Level 4 recovery).
    ///
    /// Resets iteration counter to 0 and clears all phase flags for a
    /// complete restart from the beginning of the pipeline.
    pub(crate) fn reset_to_iteration_zero(&self) -> Self {
        let initial_phase = self.initial_phase_for_run_configuration();
        let prompt_inputs = self
            .prompt_inputs
            .clone()
            .with_planning_cleared()
            .with_development_cleared()
            .with_commit_cleared()
            .with_review_cleared()
            .with_xsd_retry_cleared();
        Self {
            iteration: 0,
            phase: initial_phase,
            context_cleaned: false,
            gitignore_entries_ensured: false,
            prompt_inputs,
            continuation: self.continuation.clone().reset(),
            ..self.clone()
        }
        .clear_planning_flags()
        .clear_development_flags()
        .clear_commit_flags()
        .clear_review_flags()
    }
}

#[cfg(test)]
mod helper_tests {
    use super::*;
    use crate::reducer::state::{
        CommitState, MaterializedCommitInputs, MaterializedDevelopmentInputs,
        MaterializedPlanningInputs, MaterializedPromptInput, PromptInputKind,
        PromptInputRepresentation, PromptInputsState, PromptMaterializationReason,
    };

    fn mp(kind: PromptInputKind) -> MaterializedPromptInput {
        MaterializedPromptInput {
            kind,
            content_id_sha256: "id".to_string(),
            consumer_signature_sha256: "sig".to_string(),
            original_bytes: 1,
            final_bytes: 1,
            model_budget_bytes: None,
            inline_budget_bytes: None,
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        }
    }

    #[test]
    fn reset_iteration_resets_global_phase_start_prereqs() {
        let mut state = PipelineState::initial(2, 0);
        state.iteration = 1;
        state.context_cleaned = true;
        state.gitignore_entries_ensured = true;
        state.continuation.same_agent_retry_pending = true;
        state.continuation.same_agent_retry_count = 2;

        state.prompt_inputs = PromptInputsState {
            planning: Some(MaterializedPlanningInputs {
                iteration: 1,
                prompt: mp(PromptInputKind::Prompt),
            }),
            development: Some(MaterializedDevelopmentInputs {
                iteration: 1,
                prompt: mp(PromptInputKind::Prompt),
                plan: mp(PromptInputKind::Plan),
            }),
            commit: Some(MaterializedCommitInputs {
                attempt: 1,
                diff: mp(PromptInputKind::Diff),
            }),
            review: None,
            xsd_retry_last_output: None,
        };

        let reset = state.reset_iteration();

        assert!(!reset.context_cleaned);
        assert!(!reset.gitignore_entries_ensured);
        assert!(!reset.continuation.same_agent_retry_pending);
        assert_eq!(reset.continuation.same_agent_retry_count, 0);
        assert!(reset.prompt_inputs.planning.is_none());
        assert!(reset.prompt_inputs.development.is_none());
        assert!(reset.prompt_inputs.commit.is_none());
    }

    #[test]
    fn reset_to_iteration_zero_resets_global_phase_start_prereqs() {
        let mut state = PipelineState::initial(2, 0);
        state.iteration = 2;
        state.context_cleaned = true;
        state.gitignore_entries_ensured = true;
        state.prompt_inputs.planning = Some(MaterializedPlanningInputs {
            iteration: 2,
            prompt: mp(PromptInputKind::Prompt),
        });

        let reset = state.reset_to_iteration_zero();

        assert_eq!(reset.iteration, 0);
        assert!(!reset.context_cleaned);
        assert!(!reset.gitignore_entries_ensured);
        assert!(reset.prompt_inputs.planning.is_none());
    }

    #[test]
    fn clear_commit_flags_resets_commit_state_machine() {
        let mut state = PipelineState::initial(1, 0);
        state.commit = CommitState::Generated {
            message: "stale".to_string(),
        };
        state.commit_prompt_prepared = true;
        state.commit_diff_prepared = true;

        let reset = state.clear_phase_flags(PipelinePhase::CommitMessage);

        assert!(matches!(reset.commit, CommitState::NotStarted));
        assert!(!reset.commit_prompt_prepared);
        assert!(!reset.commit_diff_prepared);
    }

    #[test]
    fn reset_iteration_clears_review_and_fix_flags() {
        let mut state = PipelineState::initial(2, 0);
        state.iteration = 1;

        state.review_issues_found = true;
        state.review_context_prepared_pass = Some(1);
        state.fix_prompt_prepared_pass = Some(1);
        state.fix_agent_invoked_pass = Some(1);

        let reset = state.reset_iteration();

        assert!(!reset.review_issues_found);
        assert!(reset.review_context_prepared_pass.is_none());
        assert!(reset.fix_prompt_prepared_pass.is_none());
        assert!(reset.fix_agent_invoked_pass.is_none());
    }

    #[test]
    fn reset_to_iteration_zero_clears_review_and_fix_flags() {
        let mut state = PipelineState::initial(2, 0);
        state.iteration = 2;

        state.review_issues_found = true;
        state.review_agent_invoked_pass = Some(2);
        state.fix_result_xml_extracted_pass = Some(2);

        let reset = state.reset_to_iteration_zero();

        assert_eq!(reset.iteration, 0);
        assert!(!reset.review_issues_found);
        assert!(reset.review_agent_invoked_pass.is_none());
        assert!(reset.fix_result_xml_extracted_pass.is_none());
    }

    #[test]
    fn iteration_resets_restart_at_initial_phase_for_run_configuration() {
        // When no development iterations are configured, the pipeline starts in Review
        // (if review passes exist) or CommitMessage (if neither dev nor review exist).
        // Recovery resets should use the same initial phase selection.

        let state = PipelineState::initial(0, 2);
        let reset = state.reset_to_iteration_zero();
        assert_eq!(reset.phase, PipelinePhase::Review);

        let state = PipelineState::initial(0, 0);
        let reset = state.reset_to_iteration_zero();
        assert_eq!(reset.phase, PipelinePhase::CommitMessage);

        let state = PipelineState::initial(0, 2);
        let reset = state.reset_iteration();
        assert_eq!(reset.phase, PipelinePhase::Review);

        let state = PipelineState::initial(0, 0);
        let reset = state.reset_iteration();
        assert_eq!(reset.phase, PipelinePhase::CommitMessage);
    }
}
