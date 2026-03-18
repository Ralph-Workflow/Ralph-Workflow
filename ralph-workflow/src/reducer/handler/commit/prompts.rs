//! Commit prompt preparation.
//!
//! This module handles generating prompts for commit message generation:
//! - Loading materialized diff inputs
//! - Generating prompts from templates
//! - Handling XSD retry prompts
//! - Handling same-agent retry prompts
//! - Validating template variables
//!
//! ## Prompt Modes
//!
//! - **Normal** - Standard commit message generation prompt
//! - **`XsdRetry`** - Retry prompt after XML validation failure
//! - **`SameAgentRetry`** - Retry prompt with retry guidance preamble
//! - **Continuation** - Not supported for commit phase (returns error)
//!
//! ## Template Validation
//!
//! All prompts are validated for unresolved template placeholders.
//! If validation fails, emits `agent_template_variables_invalid` event.
//!
//! ## Prompt Storage
//!
//! Prompts are written to `.agent/tmp/commit_prompt.txt` for:
//! - Agent invocation
//! - Same-agent retry context preservation
//! - Debugging and observability

use super::super::MainEffectHandler;
use super::current_commit_attempt;
use crate::agents::AgentRole;
use crate::phases::PhaseContext;
use crate::prompts::content_reference::{DiffContentReference, MAX_INLINE_CONTENT_SIZE};
use crate::prompts::{get_stored_or_generate_prompt, PromptScopeKey, RetryMode};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::PromptInputEvent;
use crate::reducer::event::WorkspaceIoErrorKind;
use crate::reducer::state::{PromptInputRepresentation, PromptMode};
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;
use std::io::Write;
use std::path::Path;

impl MainEffectHandler {
    /// Prepare commit prompt based on prompt mode.
    ///
    /// This is the main entry point for commit prompt preparation.
    /// It handles XSD retry mode specially, then delegates to
    /// `prepare_commit_prompt_with_diff_and_mode` for normal/retry modes.
    ///
    /// # Events Emitted
    ///
    /// - `commit_prompt_prepared` - Prompt successfully generated
    /// - `commit_diff_invalidated` - Materialized diff missing
    /// - `agent_template_variables_invalid` - Template validation failed
    ///
    /// # Errors
    ///
    /// - `CommitContinuationNotSupported` - Continuation mode not supported for commit
    /// - `CommitInputsNotMaterialized` - Inputs not materialized for this attempt
    pub(in crate::reducer::handler) fn prepare_commit_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        if matches!(prompt_mode, PromptMode::Continuation) {
            return Err(ErrorEvent::CommitContinuationNotSupported.into());
        }
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
                        .map(|diff| crate::reducer::prompt_inputs::sha256_hex_str(&diff))
                })
                .unwrap_or_else(|| "missing_commit_diff_content_id".to_string());

            // Content-id validation for replay determinism: the XSD-retry commit prompt depends
            // on the diff inputs AND the specific validation error context.
            let prompt_content_id = crate::reducer::prompt_inputs::sha256_hex_str(&format!(
                "commit_xsd_retry|diff:{}|xsd_error:{}|consumer:{}",
                diff_content_id,
                xsd_error.as_str(),
                consumer_sig,
            ));

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

            // Prepare PromptCaptured event if this is a freshly generated prompt
            let prompt_captured_event = if was_replayed {
                None
            } else {
                Some(PipelineEvent::PromptInput(
                    PromptInputEvent::PromptCaptured {
                        key: prompt_key.clone(),
                        content: prompt,
                        content_id: Some(prompt_content_id),
                    },
                ))
            };

            // Build events: CommitPromptPrepared is primary, TemplateRendered is additional (if log exists)
            let result = prompt_captured_event.map_or_else(
                || {
                    EffectResult::event(PipelineEvent::commit_prompt_prepared(attempt))
                        .with_ui_event(self.phase_transition_ui(
                            crate::reducer::event::PipelinePhase::CommitMessage,
                        ))
                        .with_ui_event(UIEvent::PromptReplayHit {
                            key: prompt_key.clone(),
                            was_replayed,
                        })
                },
                |event| {
                    EffectResult::event(PipelineEvent::commit_prompt_prepared(attempt))
                        .with_ui_event(self.phase_transition_ui(
                            crate::reducer::event::PipelinePhase::CommitMessage,
                        ))
                        .with_ui_event(UIEvent::PromptReplayHit {
                            key: prompt_key.clone(),
                            was_replayed,
                        })
                        .with_additional_event(event)
                        .maybe_with_additional_event(rendered_log.map(|log| {
                            PipelineEvent::template_rendered(
                                crate::reducer::event::PipelinePhase::CommitMessage,
                                "commit_xsd_retry".to_string(),
                                log,
                            )
                        }))
                },
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

    /// Prepare commit prompt with pre-loaded diff content and mode.
    ///
    /// This handles Normal and `SameAgentRetry` modes. `XsdRetry` mode is handled
    /// in `prepare_commit_prompt` which returns early.
    ///
    /// # Prompt Modes
    ///
    /// - **Normal** - Generate fresh prompt from template
    /// - **`SameAgentRetry`** - Prepend retry guidance to last prompt
    ///
    /// # Template Validation
    ///
    /// Validates that all template placeholders are resolved. If validation fails,
    /// emits `agent_template_variables_invalid` event.
    pub(in crate::reducer::handler) fn prepare_commit_prompt_with_diff_and_mode(
        &self,
        ctx: &PhaseContext<'_>,
        diff_for_prompt: &str,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        let attempt = current_commit_attempt(&self.state.commit);

        let continuation_state = &self.state.continuation;
        let diff_content_id = self
            .state
            .commit_diff_content_id_sha256
            .clone()
            .unwrap_or_else(|| crate::reducer::prompt_inputs::sha256_hex_str(diff_for_prompt));
        let consumer_sig = self.state.agent_chain.consumer_signature_sha256();
        // Include residual_files in content ID so prompts for residual retry passes are cached
        // separately from first-pass prompts (different context = different cache key).
        let residual_files_key = if self.state.commit_residual_files.is_empty() {
            String::new()
        } else {
            format!("|residual:{}", self.state.commit_residual_files.join(","))
        };
        let prompt_content_id = crate::reducer::prompt_inputs::sha256_hex_str(&format!(
            "commit_prompt|diff:{diff_content_id}|consumer:{consumer_sig}{residual_files_key}"
        ));

        let (prompt_key, prompt, was_replayed, prompt_content_id, should_validate) =
            match prompt_mode {
                PromptMode::SameAgentRetry => {
                    // Same-agent retry: prepend retry guidance to the last prepared prompt for this
                    // phase (preserves XSD retry context if present).
                    let retry_preamble =
                        super::super::retry_guidance::same_agent_retry_preamble(continuation_state);
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
                            let (base_prompt, local_should_validate) = ctx
                            .workspace
                            .read(Path::new(".agent/tmp/commit_prompt.txt"))
                            .map_or_else(
                                |_| {
                                    // Use log-based rendering
                                    let rendered = crate::prompts::prompt_generate_commit_message_with_diff_with_log(
                                        ctx.template_context,
                                        diff_for_prompt,
                                        ctx.workspace,
                                        "commit_message_xml",
                                    );
                                    (rendered.content, true)
                                },
                                |previous_prompt| {
                                    (
                                        super::super::retry_guidance::strip_existing_same_agent_retry_preamble(
                                            &previous_prompt,
                                        )
                                        .to_string(),
                                        false,
                                    )
                                },
                            );
                            should_validate = local_should_validate;
                            format!("{retry_preamble}\n{base_prompt}")
                        },
                    );
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
                            if residual_files.is_empty() {
                                rendered.content
                            } else {
                                let file_list = residual_files
                                    .iter()
                                    .map(|f| format!("  - {f}"))
                                    .collect::<Vec<_>>()
                                    .join("\n");
                                format!(
                                    "NOTE: The following files were carried forward from a \
                                     previous commit pass and must be accounted for in this commit run:\n\
                                     {file_list}\n\n\
                                     If you do not include a file above in `<ralph-files>`, you must list it in \
                                     `<ralph-excluded-files>` with an explicit `reason` (e.g., `internal-ignore`, \
                                     `not-task-related`, `sensitive`, `deferred`).\n\n{}",
                                     rendered.content
                                 )
                            }
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
                    return Err(ErrorEvent::CommitContinuationNotSupported.into());
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

        // Prepare PromptCaptured event if this is a freshly generated prompt
        let prompt_captured_event = if was_replayed {
            None
        } else {
            Some(PipelineEvent::PromptInput(
                PromptInputEvent::PromptCaptured {
                    key: prompt_key.clone(),
                    content: prompt.clone(),
                    content_id: Some(prompt_content_id),
                },
            ))
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

        // Build events: CommitPromptPrepared is primary, TemplateRendered is additional (if log exists)
        let result = EffectResult::event(PipelineEvent::commit_prompt_prepared(attempt))
            .with_ui_event(
                self.phase_transition_ui(crate::reducer::event::PipelinePhase::CommitMessage),
            )
            .with_ui_event(UIEvent::PromptReplayHit {
                key: prompt_key,
                was_replayed,
            })
            .maybe_with_additional_event(prompt_captured_event)
            .maybe_with_additional_event(rendered_log.map(|log| {
                PipelineEvent::template_rendered(
                    crate::reducer::event::PipelinePhase::CommitMessage,
                    "commit_message_xml".to_string(),
                    log,
                )
            }));
        Ok(result)
    }
}
