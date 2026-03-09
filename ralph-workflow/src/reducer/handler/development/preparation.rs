//! Development prompt preparation.
//!
//! Generates prompts based on mode (Normal, XSD Retry, Same-Agent Retry, Continuation).
//! Handles template variable validation, prompt replay from history, and prompt file writes.
//!
//! ## Prompt Modes
//!
//! - **Normal** - First attempt for iteration, uses `developer_iteration_xml` template
//! - **XSD Retry** - Invalid XML output, includes `last_output.xml` and XSD error context
//! - **Same-Agent Retry** - Agent failed (non-XML issues), prepends retry preamble
//! - **Continuation** - Partial progress, includes continuation context from previous attempt
//!
//! ## Prompt Replay
//!
//! Normal and Continuation mode prompts are replayed from history if available (same `prompt_key`).
//! This ensures deterministic prompt generation across resume operations.

#[path = "preparation/modes.rs"]
mod modes;

use modes::{PromptModeData, PromptModeResult};

use super::super::MainEffectHandler;
use crate::agents::AgentRole;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::PhaseContext;
use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::PromptMode;
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason,
};
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    /// Prepare development prompt based on prompt mode.
    ///
    /// Generates the appropriate prompt for the developer agent based on the current mode:
    ///
    /// - **Normal** - First attempt for iteration, uses `developer_iteration_xml` template
    /// - **XSD Retry** - Invalid XML output, includes `last_output.xml` and XSD error context
    /// - **Same-Agent Retry** - Agent failed (non-XML issues), prepends retry preamble
    /// - **Continuation** - Partial progress, includes continuation context from previous attempt
    ///
    /// The prompt is validated for unresolved template variables (except for explicitly ignored
    /// inline content) and written to `.agent/tmp/development_prompt.txt` for debugging and
    /// same-agent retry fallback.
    ///
    /// # Prompt Replay
    ///
    /// Normal and Continuation mode prompts are replayed from history if available (same `prompt_key`).
    /// This ensures deterministic prompt generation across resume operations.
    ///
    /// # Template Variables
    ///
    /// If template variable validation fails, an `AgentTemplateVariablesInvalid` event is emitted
    /// and the agent is not invoked. This prevents sending malformed prompts to agents.
    ///
    /// # Non-Fatal Writes
    ///
    /// Per acceptance criteria #5, prompt file write failures log warnings but do not terminate
    /// the pipeline. Loop recovery will handle convergence if needed.
    ///
    /// # Arguments
    ///
    /// * `ctx` - Phase context with workspace, template context, and prompt history
    /// * `iteration` - Current development iteration number
    /// * `prompt_mode` - Prompt generation mode (Normal, XSD Retry, Same-Agent Retry, Continuation)
    ///
    /// # Returns
    ///
    /// `EffectResult` with `DevelopmentPromptPrepared` event, plus optional
    /// `XsdRetryLastOutputMaterialized` and `PromptInputOversizeDetected` events for XSD retry mode.
    pub(in crate::reducer::handler) fn prepare_development_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        let mode_result = match prompt_mode {
            PromptMode::Continuation => self.prompt_mode_continuation(ctx, iteration),
            PromptMode::XsdRetry => self.prompt_mode_xsd_retry(ctx, iteration)?,
            PromptMode::SameAgentRetry => self.prompt_mode_same_agent_retry(ctx, iteration)?,
            PromptMode::Normal => self.prompt_mode_normal(ctx, iteration)?,
        };

        let PromptModeData {
            prompt: dev_prompt,
            template_name,
            prompt_key,
            was_replayed,
            prompt_content_id,
            rendered_log,
            additional_events,
        } = match mode_result {
            PromptModeResult::EarlyReturn(result) => return Ok(result),
            PromptModeResult::Data(data) => data,
        };

        // Collect replay observability key and prepare PromptCaptured event if needed.
        let replay_key = prompt_key.as_deref().map(|k| (k.to_string(), was_replayed));
        let prompt_captured_event = prompt_key.as_deref().and_then(|prompt_key_str| {
            if was_replayed {
                None
            } else {
                Some(crate::reducer::event::PipelineEvent::PromptInput(
                    crate::reducer::event::PromptInputEvent::PromptCaptured {
                        key: prompt_key_str.to_string(),
                        content: dev_prompt.clone(),
                        content_id: prompt_content_id.clone(),
                    },
                ))
            }
        });

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
            .write(Path::new(".agent/tmp/development_prompt.txt"), &dev_prompt)
        {
            ctx.logger.warn(&format!(
                "Failed to write development prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
            ));
        }

        // Build events: DevelopmentPromptPrepared is primary, with additional_events and TemplateRendered as additional
        let mut result = EffectResult::event(PipelineEvent::development_prompt_prepared(iteration));

        // Emit replay observability event (RFC-007)
        if let Some((key, replayed)) = replay_key {
            result = result.with_ui_event(crate::reducer::ui_event::UIEvent::PromptReplayHit {
                key,
                was_replayed: replayed,
            });
        }

        // Emit PromptCaptured event to update reducer-owned prompt history (RFC-007)
        if let Some(event) = prompt_captured_event {
            result = result.with_additional_event(event);
        }

        // Add any additional events from XSD retry materialization, etc.
        for ev in additional_events {
            result = result.with_additional_event(ev);
        }

        // Add TemplateRendered if we have a log
        if let Some(log) = rendered_log {
            result = result.with_additional_event(PipelineEvent::template_rendered(
                crate::reducer::event::PipelinePhase::Development,
                template_name.to_string(),
                log,
            ));
        }

        Ok(result)
    }

    /// Build the development prompt for `Continuation` mode.
    fn prompt_mode_continuation(&self, ctx: &PhaseContext<'_>, iteration: u32) -> PromptModeResult {
        use crate::prompts::{
            get_stored_or_generate_prompt, prompt_developer_iteration_continuation_xml,
            prompt_developer_iteration_continuation_xml_with_log,
        };

        let continuation_state = &self.state.continuation;
        let scope_key = crate::prompts::PromptScopeKey::for_development(
            iteration,
            Some(continuation_state.continuation_attempt),
            crate::prompts::RetryMode::Normal,
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let prompt_content_id = crate::reducer::prompt_inputs::sha256_hex_str(&format!(
            "development_continuation:attempt:{}:consumer:{}",
            continuation_state.continuation_attempt,
            self.state.agent_chain.consumer_signature_sha256()
        ));
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                prompt_developer_iteration_continuation_xml(
                    ctx.template_context,
                    continuation_state,
                    ctx.workspace,
                )
            },
        );
        let rendered_log = if was_replayed {
            None
        } else {
            let rendered = prompt_developer_iteration_continuation_xml_with_log(
                ctx.template_context,
                continuation_state,
                ctx.workspace,
                "developer_iteration_continuation_xml",
            );
            if !rendered.log.is_complete() {
                let missing = rendered.log.unsubstituted.clone();
                let result = EffectResult::event(PipelineEvent::template_rendered(
                    crate::reducer::event::PipelinePhase::Development,
                    "developer_iteration_continuation_xml".to_string(),
                    rendered.log,
                ))
                .with_additional_event(
                    PipelineEvent::agent_template_variables_invalid(
                        AgentRole::Developer,
                        "developer_iteration_continuation_xml".to_string(),
                        missing,
                        Vec::new(),
                    ),
                );
                return PromptModeResult::EarlyReturn(result.with_ui_event(
                    crate::reducer::ui_event::UIEvent::PromptReplayHit {
                        key: prompt_key,
                        was_replayed,
                    },
                ));
            }
            Some(rendered.log)
        };
        PromptModeResult::Data(PromptModeData {
            prompt,
            template_name: "developer_iteration_continuation_xml",
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
            rendered_log,
            additional_events: Vec::new(),
        })
    }

    /// Build the development prompt for `XsdRetry` mode.
    fn prompt_mode_xsd_retry(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<PromptModeResult> {
        use crate::prompts::{
            get_stored_or_generate_prompt,
            prompt_developer_iteration_xsd_retry_with_context_files_and_log, PromptScopeKey,
            RetryMode,
        };

        let last_output = ctx
            .workspace
            .read(Path::new(xml_paths::DEVELOPMENT_RESULT_XML))
            .or_else(|err| {
                if err.kind() == std::io::ErrorKind::NotFound {
                    // Try reading from the archived .processed file as a fallback
                    let processed_path = Path::new(".agent/tmp/development_result.xml.processed");
                    ctx.workspace.read(processed_path).inspect(|output| {
                        ctx.logger
                            .info("XSD retry: using archived .processed file as last output");
                        let _ = output;
                    })
                } else {
                    Err(err)
                }
            })
            .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                path: xml_paths::DEVELOPMENT_RESULT_XML.to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        let content_id_sha256 = sha256_hex_str(&last_output);
        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();
        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let last_output_bytes = last_output.len() as u64;

        let already_materialized = self
            .state
            .prompt_inputs
            .xsd_retry_last_output
            .as_ref()
            .is_some_and(|m| {
                m.phase == crate::reducer::event::PipelinePhase::Development
                    && m.scope_id == iteration
                    && m.last_output.content_id_sha256 == content_id_sha256
                    && m.last_output.consumer_signature_sha256 == consumer_signature_sha256
                    && ctx
                        .workspace
                        .exists(std::path::Path::new(".agent/tmp/last_output.xml"))
            });

        let mut additional_events: Vec<PipelineEvent> = Vec::new();

        if !already_materialized {
            let tmp_dir = Path::new(".agent/tmp");
            if !ctx.workspace.exists(tmp_dir) {
                ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
                    ErrorEvent::WorkspaceCreateDirAllFailed {
                        path: tmp_dir.display().to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    }
                })?;
            }
            let last_output_path = Path::new(".agent/tmp/last_output.xml");
            ctx.workspace
                .write_atomic(last_output_path, &last_output)
                .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                    path: last_output_path.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                })?;

            let input = MaterializedPromptInput {
                kind: PromptInputKind::LastOutput,
                content_id_sha256: content_id_sha256.clone(),
                consumer_signature_sha256: consumer_signature_sha256.clone(),
                original_bytes: last_output_bytes,
                final_bytes: last_output_bytes,
                model_budget_bytes: None,
                inline_budget_bytes: Some(inline_budget_bytes),
                representation: PromptInputRepresentation::FileReference {
                    path: last_output_path.to_path_buf(),
                },
                reason: PromptMaterializationReason::PolicyForcedReference,
            };
            additional_events.push(PipelineEvent::xsd_retry_last_output_materialized(
                crate::reducer::event::PipelinePhase::Development,
                iteration,
                input,
            ));
            if last_output_bytes > inline_budget_bytes {
                additional_events.push(PipelineEvent::prompt_input_oversize_detected(
                    crate::reducer::event::PipelinePhase::Development,
                    PromptInputKind::LastOutput,
                    content_id_sha256.clone(),
                    last_output_bytes,
                    inline_budget_bytes,
                    "xsd-retry-context".to_string(),
                ));
            }
        }

        let scope_key = PromptScopeKey::for_development(
            iteration,
            None,
            RetryMode::Xsd {
                count: self.state.continuation.xsd_retry_count,
            },
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();

        let prompt_content_id = sha256_hex_str(&format!(
            "development_xsd_retry:last_output:{content_id_sha256}:consumer:{consumer_signature_sha256}"
        ));

        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                prompt_developer_iteration_xsd_retry_with_context_files_and_log(
                    ctx.template_context,
                    "XML output failed validation. Provide valid XML output.",
                    ctx.workspace,
                    "developer_iteration_xsd_retry",
                )
                .content
            },
        );

        let rendered_log = if was_replayed {
            None
        } else {
            let rendered = prompt_developer_iteration_xsd_retry_with_context_files_and_log(
                ctx.template_context,
                "XML output failed validation. Provide valid XML output.",
                ctx.workspace,
                "developer_iteration_xsd_retry",
            );
            if !rendered.log.is_complete() {
                let missing = rendered.log.unsubstituted.clone();
                let result = EffectResult::event(PipelineEvent::template_rendered(
                    crate::reducer::event::PipelinePhase::Development,
                    "developer_iteration_xsd_retry".to_string(),
                    rendered.log,
                ))
                .with_additional_event(
                    PipelineEvent::agent_template_variables_invalid(
                        AgentRole::Developer,
                        "developer_iteration_xsd_retry".to_string(),
                        missing,
                        Vec::new(),
                    ),
                );
                return Ok(PromptModeResult::EarlyReturn(result.with_ui_event(
                    crate::reducer::ui_event::UIEvent::PromptReplayHit {
                        key: prompt_key,
                        was_replayed,
                    },
                )));
            }
            Some(rendered.log)
        };

        Ok(PromptModeResult::Data(PromptModeData {
            prompt,
            template_name: "developer_iteration_xsd_retry",
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
            rendered_log,
            additional_events,
        }))
    }
}
