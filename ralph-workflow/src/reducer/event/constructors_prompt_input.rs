impl PipelineEvent {
    #[must_use] 
    pub const fn prompt_input_oversize_detected(
        phase: PipelinePhase,
        kind: PromptInputKind,
        content_id_sha256: String,
        size_bytes: u64,
        limit_bytes: u64,
        policy: String,
    ) -> Self {
        Self::PromptInput(PromptInputEvent::OversizeDetected {
            phase,
            kind,
            content_id_sha256,
            size_bytes,
            limit_bytes,
            policy,
        })
    }

    #[must_use] 
    pub const fn planning_inputs_materialized(iteration: u32, prompt: MaterializedPromptInput) -> Self {
        Self::PromptInput(PromptInputEvent::PlanningInputsMaterialized {
            iteration,
            prompt,
        })
    }

    #[must_use] 
    pub const fn development_inputs_materialized(
        iteration: u32,
        prompt: MaterializedPromptInput,
        plan: MaterializedPromptInput,
    ) -> Self {
        Self::PromptInput(PromptInputEvent::DevelopmentInputsMaterialized {
            iteration,
            prompt,
            plan,
        })
    }

    #[must_use] 
    pub const fn review_inputs_materialized(
        pass: u32,
        plan: MaterializedPromptInput,
        diff: MaterializedPromptInput,
    ) -> Self {
        Self::PromptInput(PromptInputEvent::ReviewInputsMaterialized { pass, plan, diff })
    }

    #[must_use] 
    pub const fn commit_inputs_materialized(attempt: u32, diff: MaterializedPromptInput) -> Self {
        Self::PromptInput(PromptInputEvent::CommitInputsMaterialized { attempt, diff })
    }

    /// Create a `PromptPermissionsLocked` event.
    #[must_use] 
    pub const fn prompt_permissions_locked(warning: Option<String>) -> Self {
        Self::PromptInput(PromptInputEvent::PromptPermissionsLocked { warning })
    }

    /// Create a `PromptPermissionsRestoreWarning` event.
    #[must_use] 
    pub const fn prompt_permissions_restore_warning(warning: String) -> Self {
        Self::PromptInput(PromptInputEvent::PromptPermissionsRestoreWarning { warning })
    }

    /// Create a `TemplateRendered` event.
    ///
    /// Emitted by prompt preparation handlers after template rendering.
    /// The substitution log enables validation based on tracked substitutions.
    #[must_use] 
    pub const fn template_rendered(
        phase: PipelinePhase,
        template_name: String,
        log: crate::prompts::SubstitutionLog,
    ) -> Self {
        Self::PromptInput(PromptInputEvent::TemplateRendered {
            phase,
            template_name,
            log,
        })
    }
}
