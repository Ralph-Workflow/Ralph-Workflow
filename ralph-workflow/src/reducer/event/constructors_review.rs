// NOTE: Review constructors split from constructors.rs

impl PipelineEvent {
    // Review constructors
    /// Create a `ReviewPhaseStarted` event.
    #[must_use] 
    pub const fn review_phase_started() -> Self {
        Self::Review(ReviewEvent::PhaseStarted)
    }

    /// Create a `ReviewPassStarted` event.
    #[must_use] 
    pub const fn review_pass_started(pass: u32) -> Self {
        Self::Review(ReviewEvent::PassStarted { pass })
    }

    /// Create a `ReviewContextPrepared` event.
    #[must_use] 
    pub const fn review_context_prepared(pass: u32) -> Self {
        Self::Review(ReviewEvent::ContextPrepared { pass })
    }

    /// Create a `ReviewPromptPrepared` event.
    #[must_use] 
    pub const fn review_prompt_prepared(pass: u32) -> Self {
        Self::Review(ReviewEvent::PromptPrepared { pass })
    }

    /// Create a `ReviewAgentInvoked` event.
    #[must_use] 
    pub const fn review_agent_invoked(pass: u32) -> Self {
        Self::Review(ReviewEvent::AgentInvoked { pass })
    }

    /// Create a `ReviewIssuesXmlExtracted` event.
    #[must_use] 
    pub const fn review_issues_xml_extracted(pass: u32) -> Self {
        Self::Review(ReviewEvent::IssuesXmlExtracted { pass })
    }

    /// Create a `ReviewIssuesXmlCleaned` event.
    #[must_use] 
    pub const fn review_issues_xml_cleaned(pass: u32) -> Self {
        Self::Review(ReviewEvent::IssuesXmlCleaned { pass })
    }

    /// Create a `ReviewIssuesXmlMissing` event.
    #[must_use] 
    pub const fn review_issues_xml_missing(
        pass: u32,
        attempt: u32,
        error_detail: Option<String>,
    ) -> Self {
        Self::Review(ReviewEvent::IssuesXmlMissing {
            pass,
            attempt,
            error_detail,
        })
    }

    /// Create a `ReviewIssuesXmlValidated` event.
    #[must_use] 
    pub const fn review_issues_xml_validated(
        pass: u32,
        issues_found: bool,
        clean_no_issues: bool,
        issues: Vec<String>,
        no_issues_found: Option<String>,
    ) -> Self {
        Self::Review(ReviewEvent::IssuesXmlValidated {
            pass,
            issues_found,
            clean_no_issues,
            issues,
            no_issues_found,
        })
    }

    #[must_use] 
    pub const fn review_issues_markdown_written(pass: u32) -> Self {
        Self::Review(ReviewEvent::IssuesMarkdownWritten { pass })
    }

    #[must_use] 
    pub const fn review_issue_snippets_extracted(pass: u32) -> Self {
        Self::Review(ReviewEvent::IssueSnippetsExtracted { pass })
    }

    #[must_use] 
    pub const fn review_issues_xml_archived(pass: u32) -> Self {
        Self::Review(ReviewEvent::IssuesXmlArchived { pass })
    }

    #[must_use] 
    pub const fn fix_prompt_prepared(pass: u32) -> Self {
        Self::Review(ReviewEvent::FixPromptPrepared { pass })
    }

    #[must_use] 
    pub const fn fix_agent_invoked(pass: u32) -> Self {
        Self::Review(ReviewEvent::FixAgentInvoked { pass })
    }

    #[must_use] 
    pub const fn fix_result_xml_extracted(pass: u32) -> Self {
        Self::Review(ReviewEvent::FixResultXmlExtracted { pass })
    }

    #[must_use] 
    pub const fn fix_result_xml_missing(pass: u32, attempt: u32, error_detail: Option<String>) -> Self {
        Self::Review(ReviewEvent::FixResultXmlMissing {
            pass,
            attempt,
            error_detail,
        })
    }

    #[must_use] 
    pub const fn fix_result_xml_validated(
        pass: u32,
        status: crate::reducer::state::FixStatus,
        summary: Option<String>,
    ) -> Self {
        Self::Review(ReviewEvent::FixResultXmlValidated {
            pass,
            status,
            summary,
            analysis_decision: None,
        })
    }

    /// `analysis_decision`: typed review-cycle analysis decision from the fix analysis agent.
    /// Routes within the review cycle: NeedsMoreFix → fix, CycleComplete → review_commit.
    #[must_use]
    pub const fn fix_result_xml_validated_with_decision(
        pass: u32,
        status: crate::reducer::state::FixStatus,
        summary: Option<String>,
        analysis_decision: Option<crate::reducer::state::ReviewAnalysisDecision>,
    ) -> Self {
        Self::Review(ReviewEvent::FixResultXmlValidated {
            pass,
            status,
            summary,
            analysis_decision,
        })
    }

    #[must_use] 
    pub const fn fix_result_xml_cleaned(pass: u32) -> Self {
        Self::Review(ReviewEvent::FixResultXmlCleaned { pass })
    }

    #[must_use] 
    pub const fn fix_outcome_applied(pass: u32) -> Self {
        Self::Review(ReviewEvent::FixOutcomeApplied { pass })
    }

    #[must_use] 
    pub const fn fix_result_xml_archived(pass: u32) -> Self {
        Self::Review(ReviewEvent::FixResultXmlArchived { pass })
    }

    /// Create a `ReviewCompleted` event.
    #[must_use] 
    pub const fn review_completed(pass: u32, issues_found: bool) -> Self {
        Self::Review(ReviewEvent::Completed { pass, issues_found })
    }

    /// Create a `FixAttemptStarted` event.
    #[must_use] 
    pub const fn fix_attempt_started(pass: u32) -> Self {
        Self::Review(ReviewEvent::FixAttemptStarted { pass })
    }

    /// Create a `FixAttemptCompleted` event.
    #[must_use] 
    pub const fn fix_attempt_completed(pass: u32, changes_made: bool) -> Self {
        Self::Review(ReviewEvent::FixAttemptCompleted { pass, changes_made })
    }

    /// Create a `ReviewPhaseCompleted` event.
    #[must_use] 
    pub const fn review_phase_completed(early_exit: bool) -> Self {
        Self::Review(ReviewEvent::PhaseCompleted { early_exit })
    }

    /// Create a `ReviewPassCompletedClean` event.
    #[must_use] 
    pub const fn review_pass_completed_clean(pass: u32) -> Self {
        Self::Review(ReviewEvent::PassCompletedClean { pass })
    }

    /// Create a `ReviewOutputValidationFailed` event.
    #[must_use] 
    pub const fn review_output_validation_failed(
        pass: u32,
        attempt: u32,
        error_detail: Option<String>,
    ) -> Self {
        Self::Review(ReviewEvent::OutputValidationFailed {
            pass,
            attempt,
            error_detail,
        })
    }

    /// Create a `FixContinuationTriggered` event.
    #[must_use] 
    pub const fn fix_continuation_triggered(
        pass: u32,
        status: crate::reducer::state::FixStatus,
        summary: Option<String>,
    ) -> Self {
        Self::Review(ReviewEvent::FixContinuationTriggered {
            pass,
            status,
            summary,
        })
    }

    /// Create a `FixContinuationSucceeded` event.
    #[must_use] 
    pub const fn fix_continuation_succeeded(pass: u32, total_attempts: u32) -> Self {
        Self::Review(ReviewEvent::FixContinuationSucceeded {
            pass,
            total_attempts,
        })
    }

    /// Create a `FixContinuationBudgetExhausted` event.
    #[must_use] 
    pub const fn fix_continuation_budget_exhausted(
        pass: u32,
        total_attempts: u32,
        last_status: crate::reducer::state::FixStatus,
    ) -> Self {
        Self::Review(ReviewEvent::FixContinuationBudgetExhausted {
            pass,
            total_attempts,
            last_status,
        })
    }

    /// Create a `FixOutputValidationFailed` event.
    #[must_use] 
    pub const fn fix_output_validation_failed(
        pass: u32,
        attempt: u32,
        error_detail: Option<String>,
    ) -> Self {
        Self::Review(ReviewEvent::FixOutputValidationFailed {
            pass,
            attempt,
            error_detail,
        })
    }
}
