use super::MainEffectHandler;
use crate::agents::AgentRole;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::development::boundary_domain::{
    build_development_prompt_content_id, PromptModeData, PromptModeResult,
};
use crate::phases::development::prompt_mode_strategy::{
    derive_development_prompt_execution_path, DevelopmentPromptExecutionPath,
};
use crate::phases::PhaseContext;
use crate::prompts::content_builder::PromptContentReferences;
use crate::prompts::content_reference::{
    PlanContentReference, PromptContentReference, MAX_INLINE_CONTENT_SIZE,
};
use crate::prompts::{
    get_stored_or_generate_prompt, prompt_developer_iteration_continuation_xml,
    prompt_developer_iteration_continuation_xml_with_log,
    prompt_developer_iteration_xml_with_references,
    prompt_developer_iteration_xml_with_references_and_log,
    prompt_developer_iteration_xsd_retry_with_context_files_and_log, PromptScopeKey, RetryMode,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason, PromptMode,
};
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    pub(in crate::reducer::boundary) fn prepare_development_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        // PRECONDITION: Orchestrator has pre-decided prompt_mode based on state.
        // Pure domain helper converts mode into execution path, removing policy branching from boundary.
        let execution_path = derive_development_prompt_execution_path(prompt_mode);

        // Execute the pre-decided path (dispatch to mode-specific helper based on domain decision).
        let mode_result = match execution_path {
            DevelopmentPromptExecutionPath::Continuation => {
                self.prompt_mode_continuation(ctx, iteration)
            }
            DevelopmentPromptExecutionPath::XsdRetry => {
                self.prompt_mode_xsd_retry(ctx, iteration)?
            }
            DevelopmentPromptExecutionPath::SameAgentRetry => {
                self.prompt_mode_same_agent_retry(ctx, iteration)?
            }
            DevelopmentPromptExecutionPath::Normal => self.prompt_mode_normal(ctx, iteration)?,
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
            result.with_ui_event(UIEvent::PromptReplayHit { key, was_replayed })
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
        let scope_key = PromptScopeKey::for_development(
            iteration,
            Some(continuation_state.continuation_attempt),
            RetryMode::Normal,
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let prompt_content_id = sha256_hex_str(&format!(
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
                    UIEvent::PromptReplayHit {
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
                        .exists(Path::new(".agent/tmp/last_output.xml"))
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
                .chain((last_output_bytes > inline_budget_bytes).then_some(
                    PipelineEvent::prompt_input_oversize_detected(
                        crate::reducer::event::PipelinePhase::Development,
                        PromptInputKind::LastOutput,
                        content_id_sha256.clone(),
                        last_output_bytes,
                        inline_budget_bytes,
                        "xsd-retry-context".to_string(),
                    ),
                ))
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
                    UIEvent::PromptReplayHit {
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
                        AgentRole::Developer,
                        "developer_iteration_xml".to_string(),
                        missing,
                        Vec::new(),
                    ),
                );
                return Ok(PromptModeResult::EarlyReturn(result.with_ui_event(
                    UIEvent::PromptReplayHit {
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
                        AgentRole::Developer,
                        "developer_iteration_xml".to_string(),
                        missing,
                        Vec::new(),
                    ),
                );
                return Ok(PromptModeResult::EarlyReturn(result.with_ui_event(
                    UIEvent::PromptReplayHit {
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
