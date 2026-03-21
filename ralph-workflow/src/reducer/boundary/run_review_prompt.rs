use super::MainEffectHandler;
use crate::phases::review::boundary_domain::{
    build_review_prompt_content_id, build_review_xsd_retry_prompt_content_id,
};
use crate::phases::PhaseContext;
use crate::prompts::content_builder::PromptContentReferences;
use crate::prompts::content_reference::{
    DiffContentReference, PlanContentReference, MAX_INLINE_CONTENT_SIZE,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::{PromptInputRepresentation, PromptMode};
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    pub(super) fn prepare_review_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;
        use crate::prompts::{
            get_stored_or_generate_prompt, prompt_review_xml_with_references,
            prompt_review_xsd_retry_with_context_files_and_log, PromptScopeKey, RetryMode,
        };
        use std::io::ErrorKind;

        let tmp_dir = Path::new(".agent/tmp");
        if !ctx.workspace.exists(tmp_dir) {
            ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
                ErrorEvent::WorkspaceCreateDirAllFailed {
                    path: tmp_dir.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
        }
        let xsd_retry_events: Option<Vec<PipelineEvent>> =
            if matches!(prompt_mode, PromptMode::XsdRetry) {
                Some(self.materialize_xsd_retry_last_output(ctx, pass)?)
            } else {
                None
            };

        let materialized_inputs = self
            .state
            .prompt_inputs
            .review
            .as_ref()
            .filter(|p| p.pass == pass);

        let baseline_oid_for_prompts = match ctx.workspace.read(Path::new(Self::DIFF_BASELINE_PATH))
        {
            Ok(s) => s.trim().to_string(),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => String::new(),
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: Self::DIFF_BASELINE_PATH.to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let (plan_inline, diff_inline) =
            if matches!(prompt_mode, PromptMode::Normal | PromptMode::SameAgentRetry) {
                let Some(inputs) = materialized_inputs else {
                    return Err(ErrorEvent::ReviewInputsNotMaterialized { pass }.into());
                };
                let plan_inline = match &inputs.plan.representation {
                    PromptInputRepresentation::Inline => {
                        let plan = match ctx.workspace.read(Path::new(".agent/PLAN.md")) {
                            Ok(plan) => plan,
                            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                                Self::sentinel_plan_content(ctx.config.isolation_mode)
                            }
                            Err(err) => {
                                return Err(ErrorEvent::WorkspaceReadFailed {
                                    path: ".agent/PLAN.md".to_string(),
                                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                                }
                                .into());
                            }
                        };
                        Some(plan)
                    }
                    PromptInputRepresentation::FileReference { .. } => None,
                };
                let diff_inline = match &inputs.diff.representation {
                    PromptInputRepresentation::Inline => {
                        let diff = match ctx.workspace.read(Path::new(".agent/DIFF.backup")) {
                            Ok(diff) => diff,
                            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                                Self::fallback_diff_instructions(&baseline_oid_for_prompts)
                            }
                            Err(err) => {
                                return Err(ErrorEvent::WorkspaceReadFailed {
                                    path: ".agent/DIFF.backup".to_string(),
                                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                                }
                                .into());
                            }
                        };
                        Some(diff)
                    }
                    PromptInputRepresentation::FileReference { .. } => None,
                };
                (plan_inline, diff_inline)
            } else {
                (None, None)
            };
        let continuation_state = &self.state.continuation;
        let (
            prompt_key,
            review_prompt_xml,
            was_replayed,
            template_name,
            prompt_content_id,
            rendered_log,
        ) = match prompt_mode {
            PromptMode::XsdRetry => {
                let scope_key = PromptScopeKey::for_review(
                    pass,
                    RetryMode::Xsd {
                        count: continuation_state.invalid_output_attempts,
                    },
                    self.state.recovery_epoch,
                );
                let prompt_key = scope_key.to_string();
                let xsd_error = continuation_state
                    .last_review_xsd_error
                    .as_deref()
                    .filter(|s| !s.trim().is_empty())
                    .unwrap_or("XML output failed validation. Provide valid XML output.");

                let last_output_path = Path::new(".agent/tmp/last_output.xml");
                let (last_output, last_output_id_seed) = match ctx.workspace.read(last_output_path)
                {
                    Ok(output) => (output, None),
                    Err(err) if err.kind() == ErrorKind::NotFound => {
                        (String::new(), Some("missing_last_output.xml".to_string()))
                    }
                    Err(err) => {
                        ctx.logger.warn(&format!(
                            "Failed to read {} ({:?}); using empty last output",
                            last_output_path.display(),
                            err.kind()
                        ));
                        (
                            String::new(),
                            Some(format!("last_output_read_error:{:?}", err.kind())),
                        )
                    }
                };
                let last_output_id = last_output_id_seed.map_or_else(
                    || sha256_hex_str(&last_output),
                    |seed| sha256_hex_str(&seed),
                );
                let current_prompt_content_id =
                    build_review_xsd_retry_prompt_content_id(xsd_error, &last_output_id);

                let (prompt, was_replayed) = get_stored_or_generate_prompt(
                    &scope_key,
                    &self.state.prompt_history,
                    Some(&current_prompt_content_id),
                    || {
                        let rendered = prompt_review_xsd_retry_with_context_files_and_log(
                            ctx.template_context,
                            xsd_error,
                            ctx.workspace,
                            "review_xsd_retry",
                        );
                        rendered.content
                    },
                );

                let rendered_log = if was_replayed {
                    None
                } else {
                    let rendered = prompt_review_xsd_retry_with_context_files_and_log(
                        ctx.template_context,
                        xsd_error,
                        ctx.workspace,
                        "review_xsd_retry",
                    );
                    if !rendered.log.is_complete() {
                        let missing = rendered.log.unsubstituted.clone();
                        let result = EffectResult::event(PipelineEvent::template_rendered(
                            crate::reducer::event::PipelinePhase::Review,
                            "review_xsd_retry".to_string(),
                            rendered.log,
                        ))
                        .with_ui_event(UIEvent::PromptReplayHit {
                            key: prompt_key.clone(),
                            was_replayed,
                        })
                        .with_additional_event(
                            PipelineEvent::agent_template_variables_invalid(
                                AgentRole::Reviewer,
                                "review_xsd_retry".to_string(),
                                missing,
                                Vec::new(),
                            ),
                        );
                        let result = if let Some(events) = xsd_retry_events.as_ref() {
                            events
                                .iter()
                                .cloned()
                                .fold(result, |r, ev| r.with_additional_event(ev))
                        } else {
                            result
                        };
                        return Ok(result);
                    }
                    Some(rendered.log)
                };

                (
                    prompt_key,
                    prompt,
                    was_replayed,
                    "review_xsd_retry",
                    Some(current_prompt_content_id),
                    rendered_log,
                )
            }
            PromptMode::SameAgentRetry => {
                let retry_preamble =
                    crate::reducer::boundary::retry_guidance::same_agent_retry_preamble(
                        continuation_state,
                    );
                let Some(inputs) = materialized_inputs else {
                    return Err(ErrorEvent::ReviewInputsNotMaterialized { pass }.into());
                };
                let plan_ref = match &inputs.plan.representation {
                    PromptInputRepresentation::Inline => {
                        let plan_inline = plan_inline.unwrap_or_else(|| {
                            Self::sentinel_plan_content(ctx.config.isolation_mode)
                        });
                        PlanContentReference::Inline(plan_inline)
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
                let diff_ref = match &inputs.diff.representation {
                    PromptInputRepresentation::Inline => {
                        let diff_inline = diff_inline.unwrap_or_else(|| {
                            Self::fallback_diff_instructions(&baseline_oid_for_prompts)
                        });
                        DiffContentReference::Inline(diff_inline)
                    }
                    PromptInputRepresentation::FileReference { path } => {
                        DiffContentReference::ReadFromFile {
                            path: path.clone(),
                            start_commit: baseline_oid_for_prompts.clone(),
                            description: format!(
                                "Diff is {} bytes (exceeds {} limit)",
                                inputs.diff.final_bytes, MAX_INLINE_CONTENT_SIZE
                            ),
                        }
                    }
                };
                let refs = PromptContentReferences {
                    prompt: None,
                    plan: Some(plan_ref),
                    diff: Some(diff_ref),
                };
                let scope_key = PromptScopeKey::for_review(
                    pass,
                    RetryMode::SameAgent {
                        count: continuation_state.same_agent_retry_count,
                    },
                    self.state.recovery_epoch,
                );
                let prompt_key = scope_key.to_string();

                let current_prompt_content_id = build_review_prompt_content_id(
                    "review_same_agent_retry",
                    &inputs.plan.content_id_sha256,
                    &inputs.diff.content_id_sha256,
                    &baseline_oid_for_prompts,
                    &self.state.agent_chain.consumer_signature_sha256(),
                );

                // Track whether the generator determined that template validation is needed.
                // When the previous on-disk prompt matches current inputs (reuse path),
                // we skip TemplateRendered emission because no new rendering occurred.
                let local_should_validate = std::cell::Cell::new(true);
                let (prompt, was_replayed) = get_stored_or_generate_prompt(
                    &scope_key,
                    &self.state.prompt_history,
                    Some(&current_prompt_content_id),
                    || {
                        let (base_prompt, should_val) = ctx
                                .workspace
                                .read(Path::new(".agent/tmp/review_prompt.txt"))
                                .map_or_else(
                                    |_| {
                                        (
                                            prompt_review_xml_with_references(
                                                ctx.template_context,
                                                &refs,
                                                ctx.workspace,
                                            ),
                                            true,
                                        )
                                    },
                                    |previous_prompt| {
                                        let previous_base = crate::reducer::boundary::retry_guidance::strip_existing_same_agent_retry_preamble(&previous_prompt)
                                            .to_string();
                                        let freshly_rendered_base = prompt_review_xml_with_references(
                                            ctx.template_context,
                                            &refs,
                                            ctx.workspace,
                                        );
                                        if previous_base == freshly_rendered_base {
                                            (previous_base, false)
                                        } else {
                                            (freshly_rendered_base, true)
                                        }
                                    },
                                );
                        local_should_validate.set(should_val);
                        format!("{retry_preamble}\n{base_prompt}")
                    },
                );
                let should_validate = !was_replayed && local_should_validate.get();

                let rendered_log = if !was_replayed && should_validate {
                    let rendered = crate::prompts::prompt_review_xml_with_references_and_log(
                        ctx.template_context,
                        &refs,
                        ctx.workspace,
                        "review_xml",
                    );
                    if !rendered.log.is_complete() {
                        let missing = rendered.log.unsubstituted.clone();
                        let result = EffectResult::event(PipelineEvent::template_rendered(
                            crate::reducer::event::PipelinePhase::Review,
                            "review_xml".to_string(),
                            rendered.log,
                        ))
                        .with_ui_event(UIEvent::PromptReplayHit {
                            key: prompt_key.clone(),
                            was_replayed,
                        })
                        .with_additional_event(
                            PipelineEvent::agent_template_variables_invalid(
                                AgentRole::Reviewer,
                                "review_xml".to_string(),
                                missing,
                                Vec::new(),
                            ),
                        );
                        return Ok(result);
                    }
                    Some(rendered.log)
                } else {
                    None
                };

                (
                    prompt_key,
                    prompt,
                    was_replayed,
                    "review_xml",
                    Some(current_prompt_content_id),
                    rendered_log,
                )
            }
            PromptMode::Normal => {
                let Some(inputs) = materialized_inputs else {
                    return Err(ErrorEvent::ReviewInputsNotMaterialized { pass }.into());
                };
                let scope_key =
                    PromptScopeKey::for_review(pass, RetryMode::Normal, self.state.recovery_epoch);
                let prompt_key = scope_key.to_string();
                let plan_ref = match &inputs.plan.representation {
                    PromptInputRepresentation::Inline => {
                        let plan_inline = plan_inline.unwrap_or_else(|| {
                            Self::sentinel_plan_content(ctx.config.isolation_mode)
                        });
                        PlanContentReference::Inline(plan_inline)
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
                let diff_ref = match &inputs.diff.representation {
                    PromptInputRepresentation::Inline => {
                        let diff_inline = diff_inline.unwrap_or_else(|| {
                            Self::fallback_diff_instructions(&baseline_oid_for_prompts)
                        });
                        DiffContentReference::Inline(diff_inline)
                    }
                    PromptInputRepresentation::FileReference { path } => {
                        DiffContentReference::ReadFromFile {
                            path: path.clone(),
                            start_commit: baseline_oid_for_prompts.clone(),
                            description: format!(
                                "Diff is {} bytes (exceeds {} limit)",
                                inputs.diff.final_bytes, MAX_INLINE_CONTENT_SIZE
                            ),
                        }
                    }
                };
                let current_prompt_content_id = build_review_prompt_content_id(
                    "review_normal",
                    &inputs.plan.content_id_sha256,
                    &inputs.diff.content_id_sha256,
                    &baseline_oid_for_prompts,
                    &self.state.agent_chain.consumer_signature_sha256(),
                );
                let (prompt, was_replayed) = get_stored_or_generate_prompt(
                    &scope_key,
                    &self.state.prompt_history,
                    Some(&current_prompt_content_id),
                    || {
                        let plan_ref = plan_ref.clone();
                        let diff_ref = diff_ref.clone();

                        let refs = PromptContentReferences {
                            prompt: None,
                            plan: Some(plan_ref),
                            diff: Some(diff_ref),
                        };
                        let rendered = crate::prompts::prompt_review_xml_with_references_and_log(
                            ctx.template_context,
                            &refs,
                            ctx.workspace,
                            "review_xml",
                        );
                        rendered.content
                    },
                );

                let rendered_log = if was_replayed {
                    None
                } else {
                    let refs = PromptContentReferences {
                        prompt: None,
                        plan: Some(plan_ref),
                        diff: Some(diff_ref),
                    };
                    let rendered = crate::prompts::prompt_review_xml_with_references_and_log(
                        ctx.template_context,
                        &refs,
                        ctx.workspace,
                        "review_xml",
                    );

                    if !rendered.log.is_complete() {
                        let missing = rendered.log.unsubstituted.clone();
                        let result = EffectResult::event(PipelineEvent::template_rendered(
                            crate::reducer::event::PipelinePhase::Review,
                            "review_xml".to_string(),
                            rendered.log,
                        ))
                        .with_ui_event(UIEvent::PromptReplayHit {
                            key: prompt_key.clone(),
                            was_replayed,
                        })
                        .with_additional_event(
                            PipelineEvent::agent_template_variables_invalid(
                                AgentRole::Reviewer,
                                "review_xml".to_string(),
                                missing,
                                Vec::new(),
                            ),
                        );
                        return Ok(result);
                    }
                    Some(rendered.log)
                };

                (
                    prompt_key,
                    prompt,
                    was_replayed,
                    "review_xml",
                    Some(current_prompt_content_id),
                    rendered_log,
                )
            }
            PromptMode::Continuation => {
                return Err(ErrorEvent::ReviewContinuationNotSupported.into());
            }
        };

        let prompt_captured_event = if was_replayed {
            None
        } else {
            Some(crate::reducer::event::PipelineEvent::PromptInput(
                crate::reducer::event::PromptInputEvent::PromptCaptured {
                    key: prompt_key.clone(),
                    content: review_prompt_xml.clone(),
                    content_id: prompt_content_id,
                },
            ))
        };

        if let Err(err) = ctx.workspace.write(
            Path::new(".agent/tmp/review_prompt.txt"),
            &review_prompt_xml,
        ) {
            ctx.logger.warn(&format!(
                "Failed to write review prompt file: {err}. Pipeline will continue."
            ));
        }

        let result = EffectResult::event(PipelineEvent::review_prompt_prepared(pass))
            .with_ui_event(UIEvent::PromptReplayHit {
                key: prompt_key,
                was_replayed,
            });
        let result = if let Some(event) = prompt_captured_event {
            result.with_additional_event(event)
        } else {
            result
        };
        let result = xsd_retry_events.map_or(result.clone(), |events| {
            events
                .into_iter()
                .fold(result, |r, ev| r.with_additional_event(ev))
        });
        let result = if let Some(log) = rendered_log {
            result.with_additional_event(PipelineEvent::template_rendered(
                crate::reducer::event::PipelinePhase::Review,
                template_name.to_string(),
                log,
            ))
        } else {
            result
        };

        Ok(result)
    }
}
