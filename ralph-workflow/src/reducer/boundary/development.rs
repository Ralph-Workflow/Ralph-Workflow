use super::MainEffectHandler;
use crate::agents::AgentRole;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::development::boundary_domain::{
    build_continuation_context_markdown, build_development_prompt_content_id,
    derive_development_status, parse_files_changed_lines, select_representation_by_inline_budget,
    PromptModeData, PromptModeResult,
};
use crate::phases::PhaseContext;
use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;
use crate::reducer::effect::{ContinuationContextData, EffectResult};
use crate::reducer::event::{AgentEvent, ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason,
};
use crate::reducer::ui_event::{UIEvent, XmlOutputContext, XmlOutputType};
use crate::workspace::Workspace;
use anyhow::Result;
use std::path::Path;
impl MainEffectHandler {
    pub(in crate::reducer::boundary) fn prepare_development_context(
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        let _ = crate::files::create_prompt_backup_with_workspace(ctx.workspace);
        EffectResult::event(PipelineEvent::development_context_prepared(iteration))
    }

    pub(in crate::reducer::boundary) fn invoke_development_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Development);
        let prompt = ctx
            .workspace
            .read(Path::new(".agent/tmp/development_prompt.txt"))
            .map_err(|_| ErrorEvent::DevelopmentPromptMissing { iteration })?;
        let agent = self
            .state
            .agent_chain
            .current_agent()
            .cloned()
            .unwrap_or_else(|| ctx.developer_agent.to_string());
        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Development,
            AgentRole::Developer,
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
            result.with_additional_event(PipelineEvent::development_agent_invoked(iteration))
        } else {
            result
        };
        Ok(result)
    }

    pub(in crate::reducer::boundary) fn archive_development_xml(
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        use crate::files::llm_output_extraction::archive_xml_file_with_workspace;
        archive_xml_file_with_workspace(
            ctx.workspace,
            Path::new(xml_paths::DEVELOPMENT_RESULT_XML),
        );
        EffectResult::event(PipelineEvent::development_xml_archived(iteration))
    }

    pub(in crate::reducer::boundary) fn apply_development_outcome(
        &self,
        _ctx: &mut PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        self.state
            .development_validated_outcome
            .as_ref()
            .filter(|outcome| outcome.iteration == iteration)
            .ok_or(ErrorEvent::ValidatedDevelopmentOutcomeMissing { iteration })?;
        Ok(EffectResult::event(
            PipelineEvent::development_outcome_applied(iteration),
        ))
    }
}
pub(in crate::reducer::boundary) fn write_continuation_context_to_workspace(
    workspace: &dyn Workspace,
    logger: &crate::logger::Logger,
    data: &ContinuationContextData,
) -> Result<()> {
    let tmp_dir = Path::new(".agent/tmp");
    if !workspace.exists(tmp_dir) {
        workspace.create_dir_all(tmp_dir).map_err(|err| {
            ErrorEvent::WorkspaceCreateDirAllFailed {
                path: tmp_dir.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
        })?;
    }
    let content = build_continuation_context_markdown(data);
    workspace
        .write(Path::new(".agent/tmp/continuation_context.md"), &content)
        .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
            path: ".agent/tmp/continuation_context.md".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        })?;
    logger.info("Continuation context written to .agent/tmp/continuation_context.md");
    Ok(())
}

impl MainEffectHandler {
    pub(in crate::reducer::boundary) fn materialize_development_inputs(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        let prompt_md = ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
            ErrorEvent::WorkspaceReadFailed {
                path: "PROMPT.md".to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
        })?;

        let plan_md = ctx
            .workspace
            .read(Path::new(".agent/PLAN.md"))
            .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                path: ".agent/PLAN.md".to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;
        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();
        let prompt_backup_path = Path::new(".agent/PROMPT.md.backup");
        if prompt_md.len() as u64 > inline_budget_bytes {
            crate::files::create_prompt_backup_with_workspace(ctx.workspace).map_err(|err| {
                ErrorEvent::WorkspaceWriteFailed {
                    path: prompt_backup_path.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
            ctx.logger.warn(&format!(
                "PROMPT size ({} KB) exceeds inline limit ({} KB). Referencing: {}",
                (prompt_md.len() as u64) / 1024,
                inline_budget_bytes / 1024,
                prompt_backup_path.display()
            ));
        }
        let (prompt_representation, prompt_reason) = select_representation_by_inline_budget(
            prompt_md.len() as u64,
            inline_budget_bytes,
            prompt_backup_path,
        );

        let plan_path = Path::new(".agent/PLAN.md");
        if plan_md.len() as u64 > inline_budget_bytes {
            ctx.logger.warn(&format!(
                "PLAN size ({} KB) exceeds inline limit ({} KB). Referencing: {}",
                (plan_md.len() as u64) / 1024,
                inline_budget_bytes / 1024,
                plan_path.display()
            ));
        }
        let (plan_representation, plan_reason) = select_representation_by_inline_budget(
            plan_md.len() as u64,
            inline_budget_bytes,
            plan_path,
        );
        let prompt_input = MaterializedPromptInput {
            kind: PromptInputKind::Prompt,
            content_id_sha256: sha256_hex_str(&prompt_md),
            consumer_signature_sha256: consumer_signature_sha256.clone(),
            original_bytes: prompt_md.len() as u64,
            final_bytes: prompt_md.len() as u64,
            model_budget_bytes: None,
            inline_budget_bytes: Some(inline_budget_bytes),
            representation: prompt_representation,
            reason: prompt_reason,
        };
        let plan_input = MaterializedPromptInput {
            kind: PromptInputKind::Plan,
            content_id_sha256: sha256_hex_str(&plan_md),
            consumer_signature_sha256,
            original_bytes: plan_md.len() as u64,
            final_bytes: plan_md.len() as u64,
            model_budget_bytes: None,
            inline_budget_bytes: Some(inline_budget_bytes),
            representation: plan_representation,
            reason: plan_reason,
        };
        let result = EffectResult::event(PipelineEvent::development_inputs_materialized(
            iteration,
            prompt_input.clone(),
            plan_input.clone(),
        ));
        let result = if prompt_input.original_bytes > inline_budget_bytes {
            let result = result.with_ui_event(UIEvent::AgentActivity {
                agent: "pipeline".to_string(),
                message: format!(
                    "Oversize PROMPT: {} KB > {} KB; using file reference",
                    prompt_input.original_bytes / 1024,
                    inline_budget_bytes / 1024
                ),
            });
            result.with_additional_event(PipelineEvent::prompt_input_oversize_detected(
                crate::reducer::event::PipelinePhase::Development,
                PromptInputKind::Prompt,
                prompt_input.content_id_sha256.clone(),
                prompt_input.original_bytes,
                inline_budget_bytes,
                "inline-embedding".to_string(),
            ))
        } else {
            result
        };
        if plan_input.original_bytes > inline_budget_bytes {
            let result = result.with_ui_event(UIEvent::AgentActivity {
                agent: "pipeline".to_string(),
                message: format!(
                    "Oversize PLAN: {} KB > {} KB; using file reference",
                    plan_input.original_bytes / 1024,
                    inline_budget_bytes / 1024
                ),
            });
            return Ok(result.with_additional_event(
                PipelineEvent::prompt_input_oversize_detected(
                    crate::reducer::event::PipelinePhase::Development,
                    PromptInputKind::Plan,
                    plan_input.content_id_sha256.clone(),
                    plan_input.original_bytes,
                    inline_budget_bytes,
                    "inline-embedding".to_string(),
                ),
            ));
        }

        Ok(result)
    }
}

use crate::prompts::content_builder::PromptContentReferences;
use crate::prompts::content_reference::{PlanContentReference, PromptContentReference};
use crate::prompts::{
    get_stored_or_generate_prompt, prompt_developer_iteration_continuation_xml,
    prompt_developer_iteration_continuation_xml_with_log,
    prompt_developer_iteration_xml_with_references,
    prompt_developer_iteration_xml_with_references_and_log,
    prompt_developer_iteration_xsd_retry_with_context_files_and_log, PromptScopeKey, RetryMode,
};
use crate::reducer::state::PromptMode;
impl MainEffectHandler {
    pub(in crate::reducer::boundary) fn prepare_development_prompt(
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
        if let Err(err) = ctx
            .workspace
            .write(Path::new(".agent/tmp/development_prompt.txt"), &dev_prompt)
        {
            ctx.logger.warn(&format!(
                "Failed to write development prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
            ));
        }
        let result = EffectResult::event(PipelineEvent::development_prompt_prepared(iteration));
        let result = if let Some((key, was_replayed)) = replay_key {
            result.with_ui_event(crate::reducer::ui_event::UIEvent::PromptReplayHit {
                key,
                was_replayed,
            })
        } else {
            result
        };
        let result = if let Some(event) = prompt_captured_event {
            result.with_additional_event(event)
        } else {
            result
        };
        let result = additional_events
            .into_iter()
            .fold(result, |r, ev| r.with_additional_event(ev));
        let result = if let Some(log) = rendered_log {
            result.with_additional_event(PipelineEvent::template_rendered(
                crate::reducer::event::PipelinePhase::Development,
                template_name.to_string(),
                log,
            ))
        } else {
            result
        };
        Ok(result)
    }

    fn prompt_mode_continuation(&self, ctx: &PhaseContext<'_>, iteration: u32) -> PromptModeResult {
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

    fn prompt_mode_xsd_retry(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<PromptModeResult> {
        let last_output = ctx
            .workspace
            .read(Path::new(xml_paths::DEVELOPMENT_RESULT_XML))
            .or_else(|err| {
                if err.kind() == std::io::ErrorKind::NotFound {
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

        let xsd_retry_events: Option<Vec<PipelineEvent>> = if !already_materialized {
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
            let events: Vec<_> =
                std::iter::once(PipelineEvent::xsd_retry_last_output_materialized(
                    crate::reducer::event::PipelinePhase::Development,
                    iteration,
                    input,
                ))
                .chain(
                    (last_output_bytes > inline_budget_bytes)
                        .then_some(PipelineEvent::prompt_input_oversize_detected(
                            crate::reducer::event::PipelinePhase::Development,
                            PromptInputKind::LastOutput,
                            content_id_sha256.clone(),
                            last_output_bytes,
                            inline_budget_bytes,
                            "xsd-retry-context".to_string(),
                        ))
                        .into_iter(),
                )
                .collect();
            Some(events)
        } else {
            None
        };

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
                    self.state.continuation.is_continuation(),
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
                self.state.continuation.is_continuation(),
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
            additional_events: xsd_retry_events.unwrap_or_default(),
        }))
    }

    pub(super) fn prompt_mode_same_agent_retry(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<PromptModeResult> {
        use crate::reducer::state::PromptInputRepresentation;

        let continuation_state = &self.state.continuation;
        let retry_preamble = super::retry_guidance::same_agent_retry_preamble(continuation_state);
        let inputs = self
            .state
            .prompt_inputs
            .development
            .as_ref()
            .filter(|p| p.iteration == iteration)
            .ok_or(ErrorEvent::DevelopmentInputsNotMaterialized { iteration })?;

        let prompt_ref = match &inputs.prompt.representation {
            PromptInputRepresentation::Inline => {
                let prompt_md = ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
                    ErrorEvent::WorkspaceReadFailed {
                        path: "PROMPT.md".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    }
                })?;
                PromptContentReference::inline(prompt_md)
            }
            PromptInputRepresentation::FileReference { path } => PromptContentReference::file_path(
                path.clone(),
                "Original user requirements from PROMPT.md",
            ),
        };

        let plan_ref = match &inputs.plan.representation {
            PromptInputRepresentation::Inline => {
                let plan_md = ctx
                    .workspace
                    .read(Path::new(".agent/PLAN.md"))
                    .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                        path: ".agent/PLAN.md".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    })?;
                PlanContentReference::Inline(plan_md)
            }
            PromptInputRepresentation::FileReference { path } => {
                PlanContentReference::ReadFromFile {
                    primary_path: path.clone(),
                    fallback_path: Some(Path::new(".agent/tmp/plan.xml").to_path_buf()),
                    description: format!(
                        "Plan is {} bytes (exceeds {} limit)",
                        inputs.plan.final_bytes, MAX_INLINE_CONTENT_SIZE
                    ),
                }
            }
        };

        let refs = PromptContentReferences {
            prompt: Some(prompt_ref),
            plan: Some(plan_ref),
            diff: None,
        };

        let scope_key = PromptScopeKey::for_development(
            iteration,
            None,
            RetryMode::SameAgent {
                count: continuation_state.same_agent_retry_count,
            },
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let prompt_content_id = build_development_prompt_content_id(
            "same_agent_retry",
            &inputs.prompt.content_id_sha256,
            &inputs.plan.content_id_sha256,
            &inputs.prompt.consumer_signature_sha256,
            &inputs.plan.consumer_signature_sha256,
        );
        let should_validate = false;
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                let (base_prompt, ..) = ctx
                    .workspace
                    .read(Path::new(".agent/tmp/development_prompt.txt"))
                    .map_or_else(
                        |_| {
                            (
                                prompt_developer_iteration_xml_with_references(
                                    ctx.template_context,
                                    &refs,
                                    ctx.workspace,
                                ),
                                true,
                            )
                        },
                        |previous_prompt| {
                            (
                                super::retry_guidance::strip_existing_same_agent_retry_preamble(
                                    &previous_prompt,
                                )
                                .to_string(),
                                false,
                            )
                        },
                    );
                format!("{retry_preamble}\n{base_prompt}")
            },
        );

        let rendered_log = if should_validate && !was_replayed {
            let rendered = prompt_developer_iteration_xml_with_references_and_log(
                ctx.template_context,
                &refs,
                ctx.workspace,
                "developer_iteration_xml",
            );
            if !rendered.log.is_complete() {
                let missing = rendered.log.unsubstituted.clone();
                let result = EffectResult::event(PipelineEvent::template_rendered(
                    crate::reducer::event::PipelinePhase::Development,
                    "developer_iteration_xml".to_string(),
                    rendered.log,
                ))
                .with_additional_event(
                    PipelineEvent::agent_template_variables_invalid(
                        crate::agents::AgentRole::Developer,
                        "developer_iteration_xml".to_string(),
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
        } else {
            None
        };

        Ok(PromptModeResult::Data(PromptModeData {
            prompt,
            template_name: "developer_iteration_xml",
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
            rendered_log,
            additional_events: Vec::new(),
        }))
    }

    pub(super) fn prompt_mode_normal(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<PromptModeResult> {
        use crate::reducer::state::PromptInputRepresentation;

        let inputs = self
            .state
            .prompt_inputs
            .development
            .as_ref()
            .filter(|p| p.iteration == iteration)
            .ok_or(ErrorEvent::DevelopmentInputsNotMaterialized { iteration })?;

        let prompt_md = match &inputs.prompt.representation {
            PromptInputRepresentation::Inline => {
                let prompt_md = ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
                    ErrorEvent::WorkspaceReadFailed {
                        path: "PROMPT.md".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    }
                })?;
                Some(prompt_md)
            }
            PromptInputRepresentation::FileReference { .. } => None,
        };
        let plan_md = match &inputs.plan.representation {
            PromptInputRepresentation::Inline => {
                let plan_md = ctx
                    .workspace
                    .read(Path::new(".agent/PLAN.md"))
                    .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                        path: ".agent/PLAN.md".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    })?;
                Some(plan_md)
            }
            PromptInputRepresentation::FileReference { .. } => None,
        };

        let scope_key = PromptScopeKey::for_development(
            iteration,
            None,
            RetryMode::Normal,
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let prompt_content_id = build_development_prompt_content_id(
            "normal",
            &inputs.prompt.content_id_sha256,
            &inputs.plan.content_id_sha256,
            &inputs.prompt.consumer_signature_sha256,
            &inputs.plan.consumer_signature_sha256,
        );
        let prompt_ref = match &inputs.prompt.representation {
            PromptInputRepresentation::Inline => {
                let prompt_md =
                    prompt_md.ok_or(ErrorEvent::DevelopmentInputsNotMaterialized { iteration })?;
                PromptContentReference::inline(prompt_md)
            }
            PromptInputRepresentation::FileReference { path } => PromptContentReference::file_path(
                path.clone(),
                "Original user requirements from PROMPT.md",
            ),
        };
        let plan_ref = match &inputs.plan.representation {
            PromptInputRepresentation::Inline => {
                let plan_md =
                    plan_md.ok_or(ErrorEvent::DevelopmentInputsNotMaterialized { iteration })?;
                PlanContentReference::Inline(plan_md)
            }
            PromptInputRepresentation::FileReference { path } => {
                PlanContentReference::ReadFromFile {
                    primary_path: path.clone(),
                    fallback_path: Some(Path::new(".agent/tmp/plan.xml").to_path_buf()),
                    description: format!(
                        "Plan is {} bytes (exceeds {} limit)",
                        inputs.plan.final_bytes, MAX_INLINE_CONTENT_SIZE
                    ),
                }
            }
        };
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                let prompt_ref = prompt_ref.clone();
                let plan_ref = plan_ref.clone();
                let refs = PromptContentReferences {
                    prompt: Some(prompt_ref),
                    plan: Some(plan_ref),
                    diff: None,
                };
                let rendered = prompt_developer_iteration_xml_with_references_and_log(
                    ctx.template_context,
                    &refs,
                    ctx.workspace,
                    "developer_iteration_xml",
                );
                rendered.content
            },
        );

        let rendered_log = if was_replayed {
            None
        } else {
            let refs = PromptContentReferences {
                prompt: Some(prompt_ref),
                plan: Some(plan_ref),
                diff: None,
            };
            let rendered = prompt_developer_iteration_xml_with_references_and_log(
                ctx.template_context,
                &refs,
                ctx.workspace,
                "developer_iteration_xml",
            );

            if !rendered.log.is_complete() {
                let missing = rendered.log.unsubstituted.clone();
                let result = EffectResult::event(PipelineEvent::template_rendered(
                    crate::reducer::event::PipelinePhase::Development,
                    "developer_iteration_xml".to_string(),
                    rendered.log,
                ))
                .with_additional_event(
                    PipelineEvent::agent_template_variables_invalid(
                        crate::agents::AgentRole::Developer,
                        "developer_iteration_xml".to_string(),
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
            template_name: "developer_iteration_xml",
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
            rendered_log,
            additional_events: Vec::new(),
        }))
    }
}

const DEVELOPMENT_XSD_ERROR_PATH: &str = ".agent/tmp/development_xsd_error.txt";

impl MainEffectHandler {
    pub(in crate::reducer::boundary) fn extract_development_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        let xml_path = Path::new(xml_paths::DEVELOPMENT_RESULT_XML);
        let initial_event = UIEvent::IterationProgress {
            current: iteration,
            total: self.state.total_iterations,
        };

        match ctx.workspace.read(xml_path) {
            Ok(content) => EffectResult::with_ui(
                PipelineEvent::development_xml_extracted(iteration),
                vec![
                    initial_event,
                    UIEvent::XmlOutput {
                        xml_type: XmlOutputType::DevelopmentResult,
                        content,
                        context: Some(XmlOutputContext {
                            iteration: Some(iteration),
                            pass: None,
                            snippets: Vec::new(),
                        }),
                    },
                ],
            ),
            Err(_) => EffectResult::with_ui(
                PipelineEvent::development_xml_missing(
                    iteration,
                    self.state.continuation.invalid_output_attempts,
                ),
                vec![initial_event],
            ),
        }
    }

    pub(in crate::reducer::boundary) fn validate_development_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        use crate::files::llm_output_extraction::{
            validate_continuation_development_result_xml, validate_development_result_xml,
        };

        let Ok(xml) = ctx
            .workspace
            .read(Path::new(xml_paths::DEVELOPMENT_RESULT_XML))
        else {
            return EffectResult::event(PipelineEvent::development_output_validation_failed(
                iteration,
                self.state.continuation.invalid_output_attempts,
            ));
        };

        let validation_result = if self.state.continuation.is_continuation() {
            validate_continuation_development_result_xml(&xml)
        } else {
            validate_development_result_xml(&xml)
        };

        match validation_result {
            Ok(elements) => {
                let _ = ctx
                    .workspace
                    .remove_if_exists(Path::new(DEVELOPMENT_XSD_ERROR_PATH));
                let status =
                    derive_development_status(elements.is_completed(), elements.is_partial());

                let files_changed = parse_files_changed_lines(elements.files_changed.as_deref());

                EffectResult::event(PipelineEvent::development_xml_validated(
                    iteration,
                    status,
                    elements.summary.clone(),
                    files_changed,
                    elements.next_steps,
                ))
            }
            Err(err) => {
                let _ = ctx.workspace.write(
                    Path::new(DEVELOPMENT_XSD_ERROR_PATH),
                    &err.format_for_ai_retry(),
                );
                EffectResult::event(PipelineEvent::development_output_validation_failed(
                    iteration,
                    self.state.continuation.invalid_output_attempts,
                ))
            }
        }
    }
}
