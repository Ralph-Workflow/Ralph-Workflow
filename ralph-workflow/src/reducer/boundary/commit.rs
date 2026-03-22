use crate::files::llm_output_extraction::archive_xml_file_with_workspace;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::PhaseContext;
use crate::phases::{effective_model_budget_bytes, truncate_diff_to_model_budget};
use crate::prompts::content_reference::{DiffContentReference, MAX_INLINE_CONTENT_SIZE};
use crate::prompts::{get_stored_or_generate_prompt, PromptScopeKey, RetryMode};
use crate::reducer::domain::residual::{
    parse_residual_files_status, ResidualFilesStatusParseError,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::WorkspaceIoErrorKind;
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation, PromptMode,
};
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;
use std::path::Path;

const COMMIT_XSD_ERROR_PATH: &str = ".agent/tmp/commit_xsd_error.txt";

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

    /// Invoke the current commit agent with the prepared prompt.
    pub(in crate::reducer::boundary) fn invoke_commit_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;
        use crate::reducer::event::AgentEvent;

        // Normalize agent chain state before invocation for determinism
        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Commit);

        let attempt = current_commit_attempt(&self.state.commit);
        let prompt = match ctx
            .workspace
            .read(Path::new(".agent/tmp/commit_prompt.txt"))
        {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                return Err(ErrorEvent::CommitPromptMissing { attempt }.into());
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/tmp/commit_prompt.txt".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let agent = self
            .state
            .agent_chain
            .current_agent()
            .cloned()
            .ok_or(ErrorEvent::CommitAgentNotInitialized { attempt })?;

        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Commit,
            AgentRole::Commit,
            &agent,
            None,
            prompt,
        )?;
        let result = if result.additional_events.iter().any(|e| {
            matches!(
                e,
                PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
            )
        }) {
            {
                result
                    .clone()
                    .with_additional_event(PipelineEvent::commit_agent_invoked(attempt))
            }
        } else {
            result
        };
        Ok(result)
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
        use crate::agents::AgentRole;
        use std::io::Write;

        // Precondition: orchestrator ensures prompt_mode is never Continuation.
        // Commit is atomic (not incremental work), so Continuation is semantically invalid.
        // See phase_effects/commit.rs where mode is derived from retry state.
        debug_assert!(
            !matches!(prompt_mode, PromptMode::Continuation),
            "Orchestrator must filter Continuation mode before deriving PrepareCommitPrompt effect"
        );

        let attempt = current_commit_attempt(&self.state.commit);

        if matches!(prompt_mode, PromptMode::XsdRetry) {
            let xsd_error = self
                .state
                .continuation
                .last_xsd_error
                .clone()
                .unwrap_or_else(|| {
                    "XML output failed validation. Provide valid XML output.".to_string()
                });

            let consumer_sig = self.state.agent_chain.consumer_signature_sha256();
            let diff_content_id = self
                .state
                .commit_diff_content_id_sha256
                .clone()
                .or_else(|| {
                    // If the diff content-id is unexpectedly missing, derive a stable id from the
                    // materialized model-safe diff content.
                    let model_safe_path = Path::new(".agent/tmp/commit_diff.model_safe.txt");
                    ctx.workspace
                        .read(model_safe_path)
                        .ok()
                        .map(|diff| sha256_hex_str(&diff))
                })
                .unwrap_or_else(|| "missing_commit_diff_content_id".to_string());

            // Content-id validation for replay determinism: the XSD-retry commit prompt depends
            // on the diff inputs AND the specific validation error context.
            let prompt_content_id = crate::phases::commit::commit_xsd_retry_prompt_content_id(
                &diff_content_id,
                xsd_error.as_str(),
                &consumer_sig,
            );

            let scope_key = PromptScopeKey::for_commit(
                self.state.iteration,
                attempt,
                RetryMode::Xsd {
                    count: self.state.continuation.xsd_retry_count,
                },
                self.state.recovery_epoch,
            );
            let prompt_key = scope_key.to_string();
            let (prompt, was_replayed) = get_stored_or_generate_prompt(
                &scope_key,
                &self.state.prompt_history,
                Some(&prompt_content_id),
                || {
                    // Generate with log-based validation
                    let rendered = crate::prompts::prompt_commit_xsd_retry_with_log(
                        ctx.template_context,
                        &xsd_error,
                        ctx.workspace,
                        "commit_xsd_retry",
                    );

                    // Validate using substitution log
                    if !rendered.log.is_complete() {
                        // This shouldn't happen in practice since prompt generation handles defaults,
                        // but if it does, we need to return something. The validation check below
                        // will catch it and emit the appropriate event.
                        let _ = writeln!(
                            std::io::stderr(),
                            "Warning: Template rendering produced incomplete substitution log: {:?}",
                            rendered.log.unsubstituted
                        );
                    }

                    rendered.content
                },
            );

            // Re-validate if this is a freshly generated prompt (not replayed)
            // For replayed prompts, we trust they were valid when originally generated
            let rendered_log = if was_replayed {
                None
            } else {
                // Generate again to get the log for validation
                let rendered = crate::prompts::prompt_commit_xsd_retry_with_log(
                    ctx.template_context,
                    &xsd_error,
                    ctx.workspace,
                    "commit_xsd_retry",
                );

                if !rendered.log.is_complete() {
                    let missing = rendered.log.unsubstituted.clone();
                    let result = EffectResult::event(PipelineEvent::template_rendered(
                        crate::reducer::event::PipelinePhase::CommitMessage,
                        "commit_xsd_retry".to_string(),
                        rendered.log,
                    ))
                    .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                        AgentRole::Commit,
                        "commit_xsd_retry".to_string(),
                        missing,
                        Vec::new(),
                    ))
                    .with_ui_event(UIEvent::PromptReplayHit {
                        key: prompt_key,
                        was_replayed,
                    });
                    return Ok(result);
                }

                Some(rendered.log)
            };

            let tmp_dir = Path::new(".agent/tmp");
            if !ctx.workspace.exists(tmp_dir) {
                ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
                    ErrorEvent::WorkspaceCreateDirAllFailed {
                        path: tmp_dir.display().to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    }
                })?;
            }

            // Write prompt file (non-fatal: if write fails, log warning and continue)
            // Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
            // If the prompt file write fails, we continue with orchestration - loop recovery will
            // handle convergence if needed.
            if let Err(err) = ctx
                .workspace
                .write(Path::new(".agent/tmp/commit_prompt.txt"), &prompt)
            {
                ctx.logger.warn(&format!(
                    "Failed to write commit prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
                ));
            }

            let prompt_captured_event = crate::phases::commit::prompt_captured_event(
                &prompt_key,
                &prompt,
                &prompt_content_id,
                was_replayed,
            );
            let result = crate::phases::commit::commit_prompt_prepared_result(
                attempt,
                self.state.phase,
                prompt_key,
                was_replayed,
                prompt_captured_event,
                rendered_log,
                "commit_xsd_retry",
            );
            return Ok(result);
        }

        let inputs = self
            .state
            .prompt_inputs
            .commit
            .as_ref()
            .filter(|c| c.attempt == attempt)
            .ok_or(ErrorEvent::CommitInputsNotMaterialized { attempt })?;

        let model_safe_path = Path::new(".agent/tmp/commit_diff.model_safe.txt");
        let diff_for_prompt = match &inputs.diff.representation {
            PromptInputRepresentation::Inline => match ctx.workspace.read(model_safe_path) {
                Ok(diff) => diff,
                Err(err) => {
                    ctx.logger.warn(&format!(
                        "Missing/unreadable materialized commit diff at {} ({err}); invalidating commit inputs to rematerialize",
                        model_safe_path.display()
                    ));
                    // Recoverability: tmp artifacts may be cleaned between checkpoints.
                    // Force rerunning CheckCommitDiff to recreate the diff and its materialization.
                    return Ok(EffectResult::event(PipelineEvent::commit_diff_invalidated(
                        "Missing/unreadable .agent/tmp/commit_diff.model_safe.txt".to_string(),
                    )));
                }
            },
            PromptInputRepresentation::FileReference { path } => {
                if !ctx.workspace.exists(path) {
                    ctx.logger.warn(&format!(
                        "Missing materialized commit diff reference at {}; invalidating commit inputs to rematerialize",
                        path.display()
                    ));
                    // Recoverability: tmp artifacts may be cleaned between checkpoints.
                    // Force rerunning CheckCommitDiff to recreate the diff and its materialization.
                    return Ok(EffectResult::event(PipelineEvent::commit_diff_invalidated(
                        "Missing materialized commit diff reference".to_string(),
                    )));
                }
                DiffContentReference::ReadFromFile {
                    path: path.clone(),
                    start_commit: String::new(),
                    description: format!(
                        "Diff is {} bytes (exceeds {} limit)",
                        inputs.diff.final_bytes, MAX_INLINE_CONTENT_SIZE
                    ),
                }
                .render_for_template()
            }
        };
        self.prepare_commit_prompt_with_diff_and_mode(ctx, &diff_for_prompt, prompt_mode)
    }

    /// Prepare prompt content for normal and same-agent retry modes.
    pub(in crate::reducer::boundary) fn prepare_commit_prompt_with_diff_and_mode(
        &self,
        ctx: &PhaseContext<'_>,
        diff_for_prompt: &str,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;

        let attempt = current_commit_attempt(&self.state.commit);

        let continuation_state = &self.state.continuation;
        let diff_content_id = self
            .state
            .commit_diff_content_id_sha256
            .clone()
            .unwrap_or_else(|| sha256_hex_str(diff_for_prompt));
        let consumer_sig = self.state.agent_chain.consumer_signature_sha256();
        let prompt_content_id = crate::phases::commit::commit_prompt_content_id(
            &diff_content_id,
            &consumer_sig,
            &self.state.commit_residual_files,
        );

        let (prompt_key, prompt, was_replayed, prompt_content_id, should_validate) =
            match prompt_mode {
                PromptMode::SameAgentRetry => {
                    // Same-agent retry: prepend retry guidance to the last prepared prompt for this
                    // phase (preserves XSD retry context if present).
                    let retry_preamble =
                        crate::reducer::boundary::retry_guidance::same_agent_retry_preamble(
                            continuation_state,
                        );
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
                        Some(&prompt_content_id),
                        || {
                            let previous_prompt = ctx
                                .workspace
                                .read(Path::new(".agent/tmp/commit_prompt.txt"))
                                .ok();
                            let generated_base_prompt =
                                crate::prompts::prompt_generate_commit_message_with_diff_with_log(
                                    ctx.template_context,
                                    diff_for_prompt,
                                    ctx.workspace,
                                    "commit_message_xml",
                                )
                                .content;
                            let (base_prompt, _local_should_validate) =
                                crate::phases::commit::base_prompt_for_same_agent_retry(
                                    previous_prompt.as_deref(),
                                    &generated_base_prompt,
                                );
                            format!("{retry_preamble}\n{base_prompt}")
                        },
                    );
                    let should_validate = !was_replayed;
                    (
                        prompt_key,
                        prompt,
                        was_replayed,
                        prompt_content_id,
                        should_validate,
                    )
                }
                PromptMode::Normal => {
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
                        Some(&prompt_content_id),
                        || {
                            // Use log-based rendering
                            let rendered =
                                crate::prompts::prompt_generate_commit_message_with_diff_with_log(
                                    ctx.template_context,
                                    diff_for_prompt,
                                    ctx.workspace,
                                    "commit_message_xml",
                                );
                            // Prepend residual-files context when the AI left files uncommitted
                            // in a previous pass and they are now queued for this commit.
                            crate::phases::commit::prepend_residual_files_context(
                                &rendered.content,
                                &residual_files,
                            )
                        },
                    );
                    (prompt_key, prompt, was_replayed, prompt_content_id, true)
                }
                PromptMode::XsdRetry => {
                    // XsdRetry is handled in prepare_commit_prompt() which returns early.
                    // This branch is unreachable but required for exhaustiveness.
                    unreachable!(
                    "XsdRetry mode should be handled by prepare_commit_prompt() before calling this function"
                )
                }
                PromptMode::Continuation => {
                    // Precondition violation: orchestrator must never derive Continuation mode
                    // for commit phase (validated by debug_assert above and orchestration tests).
                    unreachable!(
                        "Continuation mode is invalid for commit phase; \
                         orchestrator should constrain to {{Normal, XsdRetry, SameAgentRetry}}"
                    )
                }
            };

        let rendered_log = if should_validate && !was_replayed {
            // Generate again to get the log for validation
            // Only validate freshly generated prompts, not replayed ones
            let rendered = crate::prompts::prompt_generate_commit_message_with_diff_with_log(
                ctx.template_context,
                diff_for_prompt,
                ctx.workspace,
                "commit_message_xml",
            );

            if !rendered.log.is_complete() {
                let missing = rendered.log.unsubstituted.clone();
                let result = EffectResult::event(PipelineEvent::template_rendered(
                    crate::reducer::event::PipelinePhase::CommitMessage,
                    "commit_message_xml".to_string(),
                    rendered.log,
                ))
                .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                    AgentRole::Commit,
                    "commit_message_xml".to_string(),
                    missing,
                    Vec::new(),
                ))
                .with_ui_event(UIEvent::PromptReplayHit {
                    key: prompt_key,
                    was_replayed,
                });
                return Ok(result);
            }
            Some(rendered.log)
        } else {
            None
        };

        let prompt_captured_event = crate::phases::commit::prompt_captured_event(
            &prompt_key,
            &prompt,
            &prompt_content_id,
            was_replayed,
        );

        let tmp_dir = Path::new(".agent/tmp");
        if !ctx.workspace.exists(tmp_dir) {
            ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
                ErrorEvent::WorkspaceCreateDirAllFailed {
                    path: tmp_dir.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
        }

        // Write prompt file (non-fatal: if write fails, log warning and continue)
        // Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
        // If the prompt file write fails, we continue with orchestration - loop recovery will
        // handle convergence if needed.
        if let Err(err) = ctx
            .workspace
            .write(Path::new(".agent/tmp/commit_prompt.txt"), &prompt)
        {
            ctx.logger.warn(&format!(
                "Failed to write commit prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
            ));
        }

        let result = crate::phases::commit::commit_prompt_prepared_result(
            attempt,
            self.state.phase,
            prompt_key,
            was_replayed,
            prompt_captured_event,
            rendered_log,
            "commit_message_xml",
        );
        Ok(result)
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

        let tmp_dir = Path::new(".agent/tmp");
        if !ctx.workspace.exists(tmp_dir) {
            ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
                ErrorEvent::WorkspaceCreateDirAllFailed {
                    path: tmp_dir.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
        }
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

        if truncated_for_model_budget {
            ctx.logger.warn(&format!(
                "Diff size ({} KB) exceeds model budget ({} KB). Truncated to {} KB at: {}",
                original_bytes / 1024,
                model_budget_bytes / 1024,
                final_bytes / 1024,
                model_safe_path.display()
            ));
        } else if final_bytes > inline_budget_bytes {
            ctx.logger.warn(&format!(
                "Diff size ({} KB) exceeds inline limit ({} KB). Referencing: {}",
                final_bytes / 1024,
                inline_budget_bytes / 1024,
                model_safe_path.display()
            ));
        }

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
        let result = if truncated_for_model_budget {
            result
                .with_ui_event(UIEvent::AgentActivity {
                    agent: "pipeline".to_string(),
                    message: format!(
                        "Truncated DIFF for model budget: {} KB -> {} KB (budget {} KB)",
                        original_bytes / 1024,
                        final_bytes / 1024,
                        model_budget_bytes / 1024
                    ),
                })
                .with_additional_event(PipelineEvent::prompt_input_oversize_detected(
                    crate::reducer::event::PipelinePhase::CommitMessage,
                    PromptInputKind::Diff,
                    content_id_sha256.clone(),
                    original_bytes,
                    model_budget_bytes,
                    "model-context".to_string(),
                ))
        } else {
            result
        };
        let result = if final_bytes > inline_budget_bytes {
            result
                .with_ui_event(UIEvent::AgentActivity {
                    agent: "pipeline".to_string(),
                    message: format!(
                        "Oversize DIFF: {} KB > {} KB; using file reference",
                        final_bytes / 1024,
                        inline_budget_bytes / 1024
                    ),
                })
                .with_additional_event(PipelineEvent::prompt_input_oversize_detected(
                    crate::reducer::event::PipelinePhase::CommitMessage,
                    PromptInputKind::Diff,
                    content_id_sha256,
                    final_bytes,
                    inline_budget_bytes,
                    "inline-embedding".to_string(),
                ))
        } else {
            result
        };
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
        let tmp_dir = Path::new(".agent/tmp");
        if !ctx.workspace.exists(tmp_dir) {
            ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
                ErrorEvent::WorkspaceCreateDirAllFailed {
                    path: tmp_dir.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
        }
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

    /// Check whether commit XML output exists.
    pub(in crate::reducer::boundary) fn extract_commit_xml(
        &self,
        ctx: &PhaseContext<'_>,
    ) -> EffectResult {
        let attempt = current_commit_attempt(&self.state.commit);
        let commit_xml = Path::new(xml_paths::COMMIT_MESSAGE_XML);

        match ctx.workspace.read(commit_xml) {
            Ok(_) => EffectResult::event(PipelineEvent::commit_xml_extracted(attempt)),
            Err(_) => EffectResult::event(PipelineEvent::commit_xml_missing(attempt)),
        }
    }

    /// Archive commit XML after processing.
    pub(in crate::reducer::boundary) fn archive_commit_xml(
        &self,
        ctx: &PhaseContext<'_>,
    ) -> EffectResult {
        let attempt = current_commit_attempt(&self.state.commit);
        archive_xml_file_with_workspace(ctx.workspace, Path::new(xml_paths::COMMIT_MESSAGE_XML));
        EffectResult::event(PipelineEvent::commit_xml_archived(attempt))
    }

    // =====================================================================
    // XML validation
    // =====================================================================

    /// Validate commit message XML and map parsed outcome into pipeline events.
    pub(in crate::reducer::boundary) fn validate_commit_xml(
        &self,
        ctx: &PhaseContext<'_>,
    ) -> EffectResult {
        use crate::reducer::ui_event::XmlOutputType;

        let attempt = current_commit_attempt(&self.state.commit);
        let commit_xml = Path::new(xml_paths::COMMIT_MESSAGE_XML);

        let Ok(xml_content) = ctx.workspace.read(commit_xml) else {
            let reason =
                "XML output missing or invalid; agent must write .agent/tmp/commit_message.xml";
            let event = PipelineEvent::commit_xml_validation_failed(reason.to_string(), attempt);
            return EffectResult::with_ui(
                event,
                vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::CommitMessage,
                    content: reason.to_string(),
                    context: None,
                }],
            );
        };

        let event = match crate::phases::commit::parse_commit_xml_document(&xml_content) {
            crate::phases::commit::ParsedCommitXmlOutcome::Skipped(reason) => {
                ctx.logger.info(&format!("Commit skipped by AI: {reason}"));
                let _ = ctx
                    .workspace
                    .remove_if_exists(Path::new(COMMIT_XSD_ERROR_PATH));
                PipelineEvent::commit_skipped(reason)
            }
            crate::phases::commit::ParsedCommitXmlOutcome::Invalid(detail) => {
                let _ = ctx
                    .workspace
                    .write(Path::new(COMMIT_XSD_ERROR_PATH), &detail);
                PipelineEvent::commit_xml_validation_failed(detail, attempt)
            }
            crate::phases::commit::ParsedCommitXmlOutcome::Valid {
                message,
                files,
                excluded_files,
            } => {
                let _ = ctx
                    .workspace
                    .remove_if_exists(Path::new(COMMIT_XSD_ERROR_PATH));
                PipelineEvent::commit_xml_validated(message, files, excluded_files, attempt)
            }
        };

        EffectResult::with_ui(
            event,
            vec![UIEvent::XmlOutput {
                xml_type: XmlOutputType::CommitMessage,
                content: xml_content,
                context: None,
            }],
        )
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

    /// Create a git commit from the validated commit message.
    pub(in crate::reducer::boundary) fn create_commit(
        ctx: &PhaseContext<'_>,
        message: String,
        files: &[String],
        _excluded_files: &[crate::reducer::state::pipeline::ExcludedFile],
    ) -> Result<EffectResult> {
        use crate::git_helpers::{
            git_add_all_in_repo, git_add_specific_in_repo, git_commit_in_repo,
        };
        if files.is_empty() {
            git_add_all_in_repo(ctx.repo_root).map_err(|err| ErrorEvent::GitAddAllFailed {
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;
        } else {
            let file_refs: Vec<&str> = files.iter().map(String::as_str).collect();
            git_add_specific_in_repo(ctx.repo_root, &file_refs).map_err(
                |err: std::io::Error| ErrorEvent::GitAddSpecificFailed {
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                },
            )?;
        }

        match git_commit_in_repo(
            ctx.repo_root,
            &message,
            None,
            None,
            Some(ctx.executor),
            None,
        ) {
            Ok(Some(hash)) => Ok(EffectResult::event(PipelineEvent::commit_created(
                hash.to_string(),
                message,
            ))),
            Ok(None) => Ok(EffectResult::event(PipelineEvent::commit_skipped(
                "No changes to commit".to_string(),
            ))),
            Err(e) => Ok(EffectResult::event(
                PipelineEvent::commit_generation_failed(e.to_string()),
            )),
        }
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
