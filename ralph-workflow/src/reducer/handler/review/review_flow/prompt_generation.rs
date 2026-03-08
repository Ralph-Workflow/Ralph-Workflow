// Review phase prompt generation.
//
// This module handles building prompts for the reviewer agent across different invocation modes:
// Normal, XsdRetry, and SameAgentRetry. It manages materialized input embedding (inline vs
// file references), template validation, and prompt history capture.
//
// ## Responsibilities
//
// - Building prompts for 3 modes: Normal, XsdRetry, SameAgentRetry
// - Reading materialized inputs and deciding inline vs file-reference embedding
// - For XsdRetry: materializing last_output.xml and emitting events
// - For SameAgentRetry: prepending retry guidance to previous prompt
// - For Normal: using prompt template with content references
// - Building `PromptContentReferences` with `PlanContentReference` and `DiffContentReference`
// - Validating templates for unresolved placeholders
// - Capturing prompts to history
// - Writing `.agent/tmp/review_prompt.txt`
//
// ## Prompt Modes
//
// - **Normal**: First invocation or after successful validation - uses full template
// - **XsdRetry**: After XML validation failure - includes XSD error and last output
// - **SameAgentRetry**: After agent invocation failure - prepends retry guidance
//
// ## See Also
//
// - `input_materialization.rs` - PLAN and DIFF preparation
// - `validation.rs` - XML validation that triggers XSD retry

impl MainEffectHandler {
    pub(in crate::reducer::handler) fn prepare_review_prompt(
        &self,
        ctx: &mut PhaseContext<'_>,
        pass: u32,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;
        use crate::prompts::{
            get_stored_or_generate_prompt, prompt_review_xml_with_references,
            prompt_review_xsd_retry_with_context_files_and_log,
        };
        use std::path::Path;

        let tmp_dir = Path::new(".agent/tmp");
        if !ctx.workspace.exists(tmp_dir) {
            ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
                ErrorEvent::WorkspaceCreateDirAllFailed {
                    path: tmp_dir.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
        }
        let mut additional_events: Vec<PipelineEvent> = Vec::new();

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

        let (plan_inline, diff_inline) = if matches!(prompt_mode, PromptMode::Normal) {
            let Some(inputs) = materialized_inputs else {
                return Err(ErrorEvent::ReviewInputsNotMaterialized { pass }.into());
            };
            let plan_inline = match &inputs.plan.representation {
                PromptInputRepresentation::Inline => {
                    // Use sentinel if .agent/PLAN.md is missing.
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
                    // Use fallback if .agent/DIFF.backup is missing.
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
        if matches!(prompt_mode, PromptMode::XsdRetry) {
            let xsd_retry_events = self.materialize_xsd_retry_last_output(ctx, pass)?;
            additional_events.extend(xsd_retry_events);
        }
        let (
            prompt_key,
            review_prompt_xml,
            was_replayed,
            template_name,
            _should_validate,
            rendered_log,
        ) = match prompt_mode {
            PromptMode::XsdRetry => {
                let prompt_key = format!(
                    "review_{pass}_xsd_retry_{}",
                    continuation_state.invalid_output_attempts
                );
                // Use the actual XSD error from state, or fall back to generic message
                let xsd_error = continuation_state
                    .last_review_xsd_error
                    .as_deref()
                    .unwrap_or("XML output failed validation. Provide valid XML output.");
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
                    .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                        AgentRole::Reviewer,
                        "review_xsd_retry".to_string(),
                        missing,
                        Vec::new(),
                    ));
                    return Ok(result);
                }
                // XSD retry prompts must not replay potentially stale prompt history content.
                (
                    prompt_key,
                    rendered.content,
                    false,
                    "review_xsd_retry",
                    true,
                    Some(rendered.log),
                )
            }
            PromptMode::SameAgentRetry => {
                // Same-agent retry: prepend retry guidance to the last prepared prompt for this
                // phase (preserves XSD retry / normal context if present).
                let retry_preamble =
                    crate::reducer::handler::retry_guidance::same_agent_retry_preamble(
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
                            start_commit: baseline_oid_for_prompts,
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
                let (base_prompt, should_validate) =
                    match ctx.workspace.read(Path::new(".agent/tmp/review_prompt.txt")) {
                        Ok(previous_prompt) => (
                            crate::reducer::handler::retry_guidance::strip_existing_same_agent_retry_preamble(&previous_prompt)
                                .to_string(),
                            false,
                        ),
                        Err(_) => {
                            (
                                prompt_review_xml_with_references(
                                    ctx.template_context,
                                    &refs,
                                    ctx.workspace,
                                ),
                                true,
                            )
                        }
                    };
                let prompt = format!("{retry_preamble}\n{base_prompt}");
                let prompt_key = format!(
                    "review_{pass}_same_agent_retry_{}",
                    continuation_state.same_agent_retry_count
                );
                let rendered_log = if should_validate {
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
                        .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                            AgentRole::Reviewer,
                            "review_xml".to_string(),
                            missing,
                            Vec::new(),
                        ));
                        return Ok(result);
                    }
                    Some(rendered.log)
                } else {
                    None
                };
                (
                    prompt_key,
                    prompt,
                    false,
                    "review_xml",
                    should_validate,
                    rendered_log,
                )
            }
            PromptMode::Normal => {
                let Some(inputs) = materialized_inputs else {
                    return Err(ErrorEvent::ReviewInputsNotMaterialized { pass }.into());
                };
                let prompt_key = format!("review_{pass}");
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
                            start_commit: baseline_oid_for_prompts,
                            description: format!(
                                "Diff is {} bytes (exceeds {} limit)",
                                inputs.diff.final_bytes, MAX_INLINE_CONTENT_SIZE
                            ),
                        }
                    }
                };
                let (prompt, was_replayed) =
                    get_stored_or_generate_prompt(&prompt_key, &ctx.prompt_history, || {
                        let plan_ref = plan_ref.clone();
                        let diff_ref = diff_ref.clone();

                        let refs = PromptContentReferences {
                            prompt: None,
                            plan: Some(plan_ref),
                            diff: Some(diff_ref),
                        };
                        // Use log-based rendering
                        let rendered = crate::prompts::prompt_review_xml_with_references_and_log(
                            ctx.template_context,
                            &refs,
                            ctx.workspace,
                            "review_xml",
                        );
                        rendered.content
                    });

                // Validate freshly generated prompts (not replayed ones)
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
                        .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                            AgentRole::Reviewer,
                            "review_xml".to_string(),
                            missing,
                            Vec::new(),
                        ));
                        return Ok(result);
                    }
                    Some(rendered.log)
                };

                (
                    prompt_key,
                    prompt,
                    was_replayed,
                    "review_xml",
                    true,
                    rendered_log,
                )
            }
            PromptMode::Continuation => {
                return Err(ErrorEvent::ReviewContinuationNotSupported.into());
            }
        };

        if !was_replayed {
            ctx.capture_prompt(&prompt_key, &review_prompt_xml);
        }

        // Write prompt file (non-fatal: if write fails, log warning and continue)
        // Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
        // If the prompt file write fails, we continue with orchestration - loop recovery will
        // handle convergence if needed.
        if let Err(err) = ctx.workspace.write(
            Path::new(".agent/tmp/review_prompt.txt"),
            &review_prompt_xml,
        ) {
            ctx.logger.warn(&format!(
                "Failed to write review prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
            ));
        }

        // Build events: ReviewPromptPrepared is primary, with additional_events and TemplateRendered as additional
        let mut result = EffectResult::event(PipelineEvent::review_prompt_prepared(pass));

        // Add any additional events from XSD retry materialization, etc.
        for ev in additional_events {
            result = result.with_additional_event(ev);
        }

        // Add TemplateRendered if we have a log
        if let Some(log) = rendered_log {
            result = result.with_additional_event(PipelineEvent::template_rendered(
                crate::reducer::event::PipelinePhase::Review,
                template_name.to_string(),
                log,
            ));
        }

        Ok(result)
    }
}
