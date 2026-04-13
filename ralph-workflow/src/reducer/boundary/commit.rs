use super::commit_helpers::*;
use crate::files::artifact_paths;
use crate::phases::PhaseContext;
use crate::phases::{effective_model_budget_bytes, truncate_diff_to_model_budget};
use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;
use crate::prompts::{get_stored_or_generate_prompt, PromptScopeKey, RetryMode};
use crate::reducer::domain::residual::{
    parse_residual_files_status, ResidualFilesStatusParseError,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::WorkspaceIoErrorKind;
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::{MaterializedPromptInput, PromptInputKind, PromptMode};
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;
use std::path::Path;

pub(in crate::reducer::boundary) const fn current_commit_attempt(
    commit: &crate::reducer::state::CommitState,
) -> u32 {
    use crate::reducer::state::CommitState;
    match commit {
        CommitState::Generating { attempt, .. } => *attempt,
        _ => 1,
    }
}

impl crate::reducer::boundary::MainEffectHandler {
    // =====================================================================
    // Agent invocation
    // =====================================================================

    fn read_commit_prompt(ctx: &PhaseContext<'_>, attempt: u32) -> Result<String> {
        match ctx
            .workspace
            .read(Path::new(".agent/tmp/commit_prompt.txt"))
        {
            Ok(s) => Ok(s),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                Err(ErrorEvent::CommitPromptMissing { attempt }.into())
            }
            Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
                path: ".agent/tmp/commit_prompt.txt".to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
            .into()),
        }
    }

    fn append_commit_agent_invoked(result: EffectResult, attempt: u32) -> EffectResult {
        use crate::reducer::event::AgentEvent;
        if result.additional_events.iter().any(|e| {
            matches!(
                e,
                PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
            )
        }) {
            result
                .clone()
                .with_additional_event(PipelineEvent::commit_agent_invoked(attempt))
        } else {
            result
        }
    }

    /// Invoke the current commit agent with the prepared prompt.
    pub(in crate::reducer::boundary) fn invoke_commit_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;

        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Commit);
        let attempt = current_commit_attempt(&self.state.commit);
        let prompt = Self::read_commit_prompt(ctx, attempt)?;
        let agent = self
            .state
            .agent_chain
            .current_agent()
            .cloned()
            .ok_or(ErrorEvent::CommitAgentNotInitialized { attempt })?;

        // RFC-009: The closure receives the AgentSession created by invoke_agent.
        // In V1, session capabilities == drain defaults, so the pre-generated prompt
        // is correct. The closure still calls capability_template_variables_from_session
        // to verify the V1 invariant holds and to exercise the RFC-009 session-aware path.
        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Commit,
            AgentRole::Commit,
            &agent,
            None,
            |session: &crate::agents::session::AgentSession| {
                let _session_vars =
                    crate::prompts::capability_template_variables_from_session(session);
                prompt.clone()
            },
        )?;
        Ok(Self::append_commit_agent_invoked(result, attempt))
    }

    // =====================================================================
    // Prompt preparation
    // =====================================================================

    /// Prepare commit prompt from materialized commit inputs.
    pub(in crate::reducer::boundary) fn prepare_commit_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        debug_assert_not_continuation(prompt_mode);
        self.prepare_commit_prompt_from_inputs(ctx, prompt_mode)
    }

    fn prepare_commit_prompt_from_inputs(
        &self,
        ctx: &PhaseContext<'_>,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        let attempt = current_commit_attempt(&self.state.commit);
        let inputs = self
            .state
            .prompt_inputs
            .commit
            .as_ref()
            .filter(|c| c.attempt == attempt)
            .ok_or(ErrorEvent::CommitInputsNotMaterialized { attempt })?;
        let model_safe_path = Path::new(".agent/tmp/commit_diff.model_safe.txt");
        match super::io_commit::load_commit_diff_for_prompt(ctx, inputs, model_safe_path) {
            Ok(diff_for_prompt) => {
                self.prepare_commit_prompt_with_diff_and_mode(ctx, &diff_for_prompt, prompt_mode)
            }
            Err(early) => Ok(*early),
        }
    }

    /// Prepare prompt content for normal and same-agent retry modes.
    pub(in crate::reducer::boundary) fn prepare_commit_prompt_with_diff_and_mode(
        &self,
        ctx: &PhaseContext<'_>,
        diff_for_prompt: &str,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        let attempt = current_commit_attempt(&self.state.commit);
        let prompt_content_id = compute_commit_prompt_content_id(self, diff_for_prompt);
        let gen = self.gen_commit_prompt_for_mode(
            ctx,
            diff_for_prompt,
            prompt_mode,
            attempt,
            &prompt_content_id,
        );
        let rendered_log = match validate_commit_message_template(ctx, diff_for_prompt, &gen) {
            Ok(log) => log,
            Err(early) => return Ok(*early),
        };
        super::io_commit::ensure_commit_tmp_dir(ctx)?;
        // Write prompt file (non-fatal: if write fails, log warning and continue)
        // Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
        super::io_commit::write_commit_prompt_file(ctx, &gen.prompt);
        let prompt_captured_event = crate::phases::commit::prompt_captured_event(
            &gen.prompt_key,
            &gen.prompt,
            &gen.prompt_content_id,
            gen.was_replayed,
        );
        Ok(crate::phases::commit::commit_prompt_prepared_result(
            attempt,
            self.state.phase,
            gen.prompt_key,
            gen.was_replayed,
            prompt_captured_event,
            rendered_log,
            "commit_message_xml",
        ))
    }

    fn gen_commit_prompt_for_mode(
        &self,
        ctx: &PhaseContext<'_>,
        diff_for_prompt: &str,
        prompt_mode: PromptMode,
        attempt: u32,
        prompt_content_id: &str,
    ) -> CommitPromptGenerated {
        match prompt_mode {
            PromptMode::SameAgentRetry => {
                self.gen_same_agent_retry_commit_prompt(ctx, diff_for_prompt, attempt, prompt_content_id)
            }
            PromptMode::Normal => {
                self.gen_normal_commit_prompt(ctx, diff_for_prompt, attempt, prompt_content_id)
            }
            PromptMode::Continuation => unreachable!(
                "Continuation mode is invalid for commit phase; \
                 orchestrator should constrain to {{Normal, SameAgentRetry}}"
            ),
        }
    }

    fn gen_same_agent_retry_commit_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        diff_for_prompt: &str,
        attempt: u32,
        prompt_content_id: &str,
    ) -> CommitPromptGenerated {
        let continuation_state = &self.state.continuation;
        let retry_preamble =
            crate::reducer::boundary::retry_guidance::same_agent_retry_preamble(continuation_state);
        let scope_key = PromptScopeKey::for_commit(
            self.state.iteration,
            attempt,
            RetryMode::SameAgent {
                count: continuation_state.same_agent_retry_count,
            },
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(prompt_content_id),
            || gen_same_agent_retry_prompt_text(ctx, diff_for_prompt, &retry_preamble),
        );
        CommitPromptGenerated {
            prompt_key,
            prompt,
            was_replayed,
            prompt_content_id: prompt_content_id.to_string(),
            should_validate: !was_replayed,
        }
    }

    fn gen_normal_commit_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        diff_for_prompt: &str,
        attempt: u32,
        prompt_content_id: &str,
    ) -> CommitPromptGenerated {
        let scope_key = PromptScopeKey::for_commit(
            self.state.iteration,
            attempt,
            RetryMode::Normal,
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let residual_files = self.state.commit_residual_files.clone();
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(prompt_content_id),
            || gen_normal_commit_prompt_text(ctx, diff_for_prompt, &residual_files),
        );
        CommitPromptGenerated {
            prompt_key,
            prompt,
            was_replayed,
            prompt_content_id: prompt_content_id.to_string(),
            should_validate: true,
        }
    }

    // =====================================================================
    // Input materialization
    // =====================================================================

    /// Materialize commit diff into reducer-visible prompt input metadata.
    pub(in crate::reducer::boundary) fn materialize_commit_inputs(
        &self,
        ctx: &PhaseContext<'_>,
        attempt: u32,
    ) -> Result<EffectResult> {
        let Ok(diff) = ctx.workspace.read(Path::new(".agent/tmp/commit_diff.txt")) else {
            ctx.logger.warn(
                    "Missing commit diff at .agent/tmp/commit_diff.txt; invalidating diff-prepared state to recompute",
                );
            return Ok(EffectResult::event(PipelineEvent::commit_diff_invalidated(
                "Missing commit diff at .agent/tmp/commit_diff.txt".to_string(),
            )));
        };

        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();
        let content_id_sha256 = sha256_hex_str(&diff);
        let original_bytes = diff.len() as u64;

        let model_budget_bytes = effective_model_budget_bytes(&self.state.agent_chain.agents);
        let (model_safe_diff, truncated_for_model_budget) =
            truncate_diff_to_model_budget(&diff, model_budget_bytes);
        let final_bytes = model_safe_diff.len() as u64;

        super::io_commit::ensure_commit_tmp_dir(ctx)?;
        let model_safe_path = Path::new(".agent/tmp/commit_diff.model_safe.txt");
        ctx.workspace
            .write_atomic(model_safe_path, &model_safe_diff)
            .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                path: model_safe_path.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let (representation, reason) = crate::phases::commit::commit_representation_and_reason(
            final_bytes,
            inline_budget_bytes,
            truncated_for_model_budget,
            model_safe_path,
        );

        log_diff_size_warnings(
            ctx,
            truncated_for_model_budget,
            original_bytes,
            model_budget_bytes,
            final_bytes,
            inline_budget_bytes,
            model_safe_path,
        );

        let input = MaterializedPromptInput {
            kind: PromptInputKind::Diff,
            content_id_sha256: content_id_sha256.clone(),
            consumer_signature_sha256,
            original_bytes,
            final_bytes,
            model_budget_bytes: Some(model_budget_bytes),
            inline_budget_bytes: Some(inline_budget_bytes),
            representation,
            reason,
        };

        let result = EffectResult::event(PipelineEvent::commit_inputs_materialized(attempt, input));
        let result = attach_truncated_budget_events(
            result,
            truncated_for_model_budget,
            &content_id_sha256,
            original_bytes,
            final_bytes,
            model_budget_bytes,
        );
        let result = attach_oversize_inline_events(
            result,
            final_bytes,
            inline_budget_bytes,
            content_id_sha256,
        );
        Ok(result)
    }

    /// Check commit diff by running `git diff`.
    pub(in crate::reducer::boundary) fn check_commit_diff(
        ctx: &PhaseContext<'_>,
    ) -> Result<EffectResult> {
        let diff = crate::git_helpers::git_diff_in_repo(ctx.repo_root).map_err(anyhow::Error::from);
        Self::check_commit_diff_with_result(ctx, diff)
    }

    /// Check commit diff with a pre-computed diff result.
    pub(in crate::reducer::boundary) fn check_commit_diff_with_result(
        ctx: &PhaseContext<'_>,
        diff: Result<String, anyhow::Error>,
    ) -> Result<EffectResult> {
        match diff {
            Ok(diff) => Self::check_commit_diff_with_content(ctx, &diff),
            Err(err) => {
                // Don't fail - substitute DIFF variable with investigation instructions
                ctx.logger.warn(&format!(
                    "git diff failed: {err}, using fallback instructions for AI investigation"
                ));

                let fallback_diff =
                    crate::phases::commit::diff_unavailable_investigation_instructions(
                        &err.to_string(),
                    );

                // Use the fallback content as the diff - it will be substituted into {{DIFF}}
                Self::check_commit_diff_with_content(ctx, &fallback_diff)
            }
        }
    }

    /// Persist pre-computed diff content and emit `commit_diff_prepared`.
    pub(in crate::reducer::boundary) fn check_commit_diff_with_content(
        ctx: &PhaseContext<'_>,
        diff: &str,
    ) -> Result<EffectResult> {
        super::io_commit::ensure_commit_tmp_dir(ctx)?;
        ctx.workspace
            .write(Path::new(".agent/tmp/commit_diff.txt"), diff)
            .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                path: ".agent/tmp/commit_diff.txt".to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        Ok(EffectResult::event(PipelineEvent::commit_diff_prepared(
            diff.trim().is_empty(),
            sha256_hex_str(diff),
        )))
    }

    // =====================================================================
    // XML extraction
    // =====================================================================

    /// Check whether JSON commit artifact exists.
    pub(in crate::reducer::boundary) fn extract_commit_xml(
        &self,
        ctx: &PhaseContext<'_>,
    ) -> EffectResult {
        let attempt = current_commit_attempt(&self.state.commit);
        if crate::phases::commit::has_json_commit_artifact(ctx.workspace) {
            EffectResult::event(PipelineEvent::commit_xml_extracted(attempt))
        } else {
            EffectResult::event(PipelineEvent::commit_xml_missing(attempt))
        }
    }

    /// Archive commit XML after processing.
    pub(in crate::reducer::boundary) fn archive_commit_xml(
        &self,
        ctx: &PhaseContext<'_>,
    ) -> EffectResult {
        let attempt = current_commit_attempt(&self.state.commit);
        artifact_paths::archive_xml_file_with_workspace(
            ctx.workspace,
            Path::new(artifact_paths::COMMIT_MESSAGE_XML),
        );
        crate::files::archive_json_artifact_with_workspace(ctx.workspace, "commit_message");
        EffectResult::event(PipelineEvent::commit_xml_archived(attempt))
    }

    // =====================================================================
    // JSON validation
    // =====================================================================

    /// Validate commit message output from JSON artifact and map parsed outcome into pipeline events.
    pub(in crate::reducer::boundary) fn validate_commit_xml(
        &self,
        ctx: &PhaseContext<'_>,
    ) -> EffectResult {
        use crate::reducer::ui_event::XmlOutputType;
        let attempt = current_commit_attempt(&self.state.commit);
        match crate::phases::commit::try_parse_commit_from_json_artifact(ctx.workspace) {
            Some(parsed) => {
                let event = commit_event_from_parsed_outcome(ctx, parsed, attempt);
                EffectResult::with_ui(
                    event,
                    vec![UIEvent::XmlOutput {
                        xml_type: XmlOutputType::CommitMessage,
                        content: "(from JSON artifact)".to_string(),
                        context: None,
                    }],
                )
            }
            None => {
                let reason =
                    "No commit message found: JSON artifact (.agent/tmp/commit_message.json) absent";
                let event =
                    PipelineEvent::commit_xml_validation_failed(reason.to_string(), attempt);
                EffectResult::with_ui(
                    event,
                    vec![UIEvent::XmlOutput {
                        xml_type: XmlOutputType::CommitMessage,
                        content: reason.to_string(),
                        context: None,
                    }],
                )
            }
        }
    }

    /// Emit a commit outcome event from validated state.
    pub(in crate::reducer::boundary) fn apply_commit_message_outcome(
        &self,
        _ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        let attempt = current_commit_attempt(&self.state.commit);
        let outcome = self
            .state
            .commit_validated_outcome
            .as_ref()
            .ok_or(ErrorEvent::ValidatedCommitOutcomeMissing { attempt })?;

        let event = crate::phases::commit::commit_outcome_event_from_validated(
            outcome.message.clone(),
            outcome.reason.clone(),
            outcome.attempt,
        );

        Ok(EffectResult::event(event))
    }

    // =====================================================================
    // Git commit execution
    // =====================================================================

    fn stage_commit_files(ctx: &PhaseContext<'_>, files: &[String]) -> Result<()> {
        use crate::git_helpers::{git_add_all_in_repo, git_add_specific_in_repo};
        if files.is_empty() {
            git_add_all_in_repo(ctx.repo_root)
                .map(|_| ())
                .map_err(|err| ErrorEvent::GitAddAllFailed {
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                })
                .map_err(anyhow::Error::from)
        } else {
            let file_refs: Vec<&str> = files.iter().map(String::as_str).collect();
            git_add_specific_in_repo(ctx.repo_root, &file_refs)
                .map(|_| ())
                .map_err(|err: std::io::Error| ErrorEvent::GitAddSpecificFailed {
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                })
                .map_err(anyhow::Error::from)
        }
    }

    fn commit_result_event(
        result: std::io::Result<Option<git2::Oid>>,
        message: String,
    ) -> EffectResult {
        match result {
            Ok(Some(hash)) => {
                EffectResult::event(PipelineEvent::commit_created(hash.to_string(), message))
            }
            Ok(None) => EffectResult::event(PipelineEvent::commit_skipped(
                "No changes to commit".to_string(),
            )),
            Err(e) => EffectResult::event(PipelineEvent::commit_generation_failed(e.to_string())),
        }
    }

    /// Create a git commit from the validated commit message.
    /// Create a git commit from the validated commit message.
    pub(in crate::reducer::boundary) fn create_commit(
        ctx: &PhaseContext<'_>,
        message: String,
        files: &[String],
        _excluded_files: &[crate::reducer::state::pipeline::ExcludedFile],
    ) -> Result<EffectResult> {
        use crate::git_helpers::git_commit_in_repo;
        Self::stage_commit_files(ctx, files)?;
        let commit_result = git_commit_in_repo(
            ctx.repo_root,
            &message,
            None,
            None,
            Some(ctx.executor),
            None,
        );
        Ok(Self::commit_result_event(commit_result, message))
    }

    /// Skip commit with a reason.
    pub(in crate::reducer::boundary) const fn skip_commit(
        _ctx: &mut PhaseContext<'_>,
        reason: String,
    ) -> EffectResult {
        EffectResult::event(PipelineEvent::commit_skipped(reason))
    }

    /// Guard against terminating while uncommitted changes remain.
    pub(in crate::reducer::boundary) fn check_uncommitted_changes_before_termination(
        ctx: &PhaseContext<'_>,
    ) -> Result<EffectResult> {
        use crate::git_helpers::git_snapshot_in_repo;

        let status =
            git_snapshot_in_repo(ctx.repo_root).map_err(|err| ErrorEvent::GitStatusFailed {
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        let event = match parse_residual_files_status(&status) {
            Ok(files) => {
                let file_count = files.len();
                ctx.logger.warn(&format!(
                    "Pre-termination safety check: Uncommitted changes detected ({file_count} files). \
                     This should never happen - work should be committed before termination."
                ));

                PipelineEvent::pre_termination_uncommitted_changes_detected(file_count)
            }
            Err(ResidualFilesStatusParseError::Empty) => {
                ctx.logger
                    .info("Pre-termination safety check: No uncommitted changes found.");

                PipelineEvent::pre_termination_safety_check_passed()
            }
        };

        Ok(EffectResult::event(event))
    }

    /// Emit residual-file events after selective commit passes.
    pub(in crate::reducer::boundary) fn check_residual_files(
        ctx: &PhaseContext<'_>,
        pass: u8,
    ) -> Result<EffectResult> {
        use crate::git_helpers::git_snapshot_in_repo;

        let status =
            git_snapshot_in_repo(ctx.repo_root).map_err(|err| ErrorEvent::GitStatusFailed {
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        match parse_residual_files_status(&status) {
            Err(ResidualFilesStatusParseError::Empty) => {
                ctx.logger.info(&format!(
                    "Residual files check (pass {pass}): Working tree is clean."
                ));
                Ok(EffectResult::event(PipelineEvent::residual_files_none()))
            }
            Ok(files) => {
                ctx.logger.warn(&format!(
                    "Residual files check (pass {pass}): {} uncommitted file(s) remain after selective commit.",
                    files.len()
                ));

                Ok(EffectResult::event(PipelineEvent::residual_files_found(
                    files, pass,
                )))
            }
        }
    }
}

fn commit_event_from_parsed_outcome(
    ctx: &PhaseContext<'_>,
    parsed: crate::phases::commit::ParsedCommitXmlOutcome,
    attempt: u32,
) -> PipelineEvent {
    match parsed {
        crate::phases::commit::ParsedCommitXmlOutcome::Skipped(reason) => {
            ctx.logger.info(&format!("Commit skipped by AI: {reason}"));
            PipelineEvent::commit_skipped(reason)
        }
        crate::phases::commit::ParsedCommitXmlOutcome::Invalid(detail) => {
            PipelineEvent::commit_xml_validation_failed(detail, attempt)
        }
        crate::phases::commit::ParsedCommitXmlOutcome::Valid {
            message,
            files,
            excluded_files,
        } => PipelineEvent::commit_xml_validated(message, files, excluded_files, attempt),
    }
}

