// NOTE: Rebase, Commit, and miscellaneous constructors split from constructors.rs

impl PipelineEvent {
    // Rebase constructors
    /// Create a `RebaseStarted` event.
    #[must_use] 
    pub const fn rebase_started(phase: RebasePhase, target_branch: String) -> Self {
        Self::Rebase(RebaseEvent::Started {
            phase,
            target_branch,
        })
    }

    /// Create a `RebaseConflictDetected` event.
    #[must_use] 
    pub const fn rebase_conflict_detected(files: Vec<PathBuf>) -> Self {
        Self::Rebase(RebaseEvent::ConflictDetected { files })
    }

    /// Create a `RebaseConflictResolved` event.
    #[must_use] 
    pub const fn rebase_conflict_resolved(files: Vec<PathBuf>) -> Self {
        Self::Rebase(RebaseEvent::ConflictResolved { files })
    }

    /// Create a `RebaseSucceeded` event.
    #[must_use] 
    pub const fn rebase_succeeded(phase: RebasePhase, new_head: String) -> Self {
        Self::Rebase(RebaseEvent::Succeeded { phase, new_head })
    }

    /// Create a `RebaseFailed` event.
    #[must_use] 
    pub const fn rebase_failed(phase: RebasePhase, reason: String) -> Self {
        Self::Rebase(RebaseEvent::Failed { phase, reason })
    }

    /// Create a `RebaseAborted` event.
    #[must_use] 
    pub const fn rebase_aborted(phase: RebasePhase, restored_to: String) -> Self {
        Self::Rebase(RebaseEvent::Aborted { phase, restored_to })
    }

    /// Create a `RebaseSkipped` event.
    #[must_use] 
    pub const fn rebase_skipped(phase: RebasePhase, reason: String) -> Self {
        Self::Rebase(RebaseEvent::Skipped { phase, reason })
    }

    // Commit constructors
    /// Create a `CommitGenerationStarted` event.
    #[must_use] 
    pub const fn commit_generation_started() -> Self {
        Self::Commit(CommitEvent::GenerationStarted)
    }

    /// Create a `CommitDiffPrepared` event.
    #[must_use] 
    pub const fn commit_diff_prepared(empty: bool, content_id_sha256: String) -> Self {
        Self::Commit(CommitEvent::DiffPrepared {
            empty,
            content_id_sha256,
        })
    }

    /// Create a `CommitDiffFailed` event.
    #[must_use] 
    pub const fn commit_diff_failed(error: String) -> Self {
        Self::Commit(CommitEvent::DiffFailed { error })
    }

    #[must_use] 
    pub const fn commit_diff_invalidated(reason: String) -> Self {
        Self::Commit(CommitEvent::DiffInvalidated { reason })
    }

    /// Create a `CommitPromptPrepared` event.
    #[must_use] 
    pub const fn commit_prompt_prepared(attempt: u32) -> Self {
        Self::Commit(CommitEvent::PromptPrepared { attempt })
    }

    /// Create a `CommitAgentInvoked` event.
    #[must_use] 
    pub const fn commit_agent_invoked(attempt: u32) -> Self {
        Self::Commit(CommitEvent::AgentInvoked { attempt })
    }

    /// Create a `CommitXmlExtracted` event.
    #[must_use] 
    pub const fn commit_xml_extracted(attempt: u32) -> Self {
        Self::Commit(CommitEvent::CommitXmlExtracted { attempt })
    }

    /// Create a `CommitXmlMissing` event.
    #[must_use] 
    pub const fn commit_xml_missing(attempt: u32) -> Self {
        Self::Commit(CommitEvent::CommitXmlMissing { attempt })
    }

    /// Create a `CommitXmlValidated` event.
    #[must_use]
    pub const fn commit_xml_validated(
        message: String,
        files: Vec<String>,
        excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile>,
        attempt: u32,
    ) -> Self {
        Self::Commit(CommitEvent::CommitXmlValidated {
            message,
            attempt,
            files,
            excluded_files,
        })
    }

    /// Create a `CommitXmlValidationFailed` event.
    #[must_use] 
    pub const fn commit_xml_validation_failed(reason: String, attempt: u32) -> Self {
        Self::Commit(CommitEvent::CommitXmlValidationFailed { reason, attempt })
    }

    /// Create a `CommitXmlArchived` event.
    #[must_use] 
    pub const fn commit_xml_archived(attempt: u32) -> Self {
        Self::Commit(CommitEvent::CommitXmlArchived { attempt })
    }

    #[must_use] 
    pub const fn commit_required_files_cleaned(attempt: u32) -> Self {
        Self::Commit(CommitEvent::CommitXmlCleaned { attempt })
    }

    /// Create a `CommitMessageGenerated` event.
    #[must_use] 
    pub const fn commit_message_generated(message: String, attempt: u32) -> Self {
        Self::Commit(CommitEvent::MessageGenerated { message, attempt })
    }

    /// Create a `CommitMessageValidationFailed` event.
    #[must_use] 
    pub const fn commit_message_validation_failed(reason: String, attempt: u32) -> Self {
        Self::Commit(CommitEvent::MessageValidationFailed { reason, attempt })
    }

    /// Create a `CommitCreated` event.
    #[must_use] 
    pub const fn commit_created(hash: String, message: String) -> Self {
        Self::Commit(CommitEvent::Created { hash, message })
    }

    /// Create a `CommitGenerationFailed` event.
    #[must_use] 
    pub const fn commit_generation_failed(reason: String) -> Self {
        Self::Commit(CommitEvent::GenerationFailed { reason })
    }

    /// Create a `CommitSkipped` event.
    #[must_use] 
    pub const fn commit_skipped(reason: String) -> Self {
        Self::Commit(CommitEvent::Skipped { reason })
    }

    /// Create a `PreTerminationSafetyCheckPassed` event.
    #[must_use] 
    pub const fn pre_termination_safety_check_passed() -> Self {
        Self::Commit(CommitEvent::PreTerminationSafetyCheckPassed)
    }

    /// Create a `PreTerminationUncommittedChangesDetected` event.
    #[must_use]
    pub const fn pre_termination_uncommitted_changes_detected(file_count: usize) -> Self {
        Self::Commit(CommitEvent::PreTerminationUncommittedChangesDetected { file_count })
    }

    /// Create a `ResidualFilesFound` event.
    ///
    /// Emitted after a selective commit pass when uncommitted files remain.
    #[must_use]
    pub const fn residual_files_found(files: Vec<String>, pass: u8) -> Self {
        Self::Commit(CommitEvent::ResidualFilesFound { files, pass })
    }

    /// Create a `ResidualFilesNone` event.
    ///
    /// Emitted after a commit pass when the working tree is clean.
    #[must_use]
    pub const fn residual_files_none() -> Self {
        Self::Commit(CommitEvent::ResidualFilesNone)
    }

    // Miscellaneous constructors
    /// Create a `ContextCleaned` event.
    #[must_use] 
    pub const fn context_cleaned() -> Self {
        Self::ContextCleaned
    }

    /// Create a `CheckpointSaved` event.
    #[must_use] 
    pub const fn checkpoint_saved(trigger: CheckpointTrigger) -> Self {
        Self::CheckpointSaved { trigger }
    }

    /// Create a `FinalizingStarted` event.
    #[must_use] 
    pub const fn finalizing_started() -> Self {
        Self::FinalizingStarted
    }

    /// Create a `PromptPermissionsRestored` event.
    #[must_use] 
    pub const fn prompt_permissions_restored() -> Self {
        Self::PromptPermissionsRestored
    }
}
