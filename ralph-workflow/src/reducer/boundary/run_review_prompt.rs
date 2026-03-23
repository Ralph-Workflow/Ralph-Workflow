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
        ensure_tmp_dir(ctx)?;

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

        let baseline_oid_for_prompts = read_baseline_oid_for_prompts(ctx)?;

        let (plan_inline, diff_inline) =
            if matches!(prompt_mode, PromptMode::Normal | PromptMode::SameAgentRetry) {
                let Some(inputs) = materialized_inputs else {
                    return Err(ErrorEvent::ReviewInputsNotMaterialized { pass }.into());
                };
                let plan_inline = resolve_plan_inline(ctx, &inputs.plan.representation)?;
                let diff_inline = resolve_diff_inline(
                    ctx,
                    &inputs.diff.representation,
                    &baseline_oid_for_prompts,
                )?;
                (plan_inline, diff_inline)
            } else {
                (None, None)
            };

        let continuation_state = &self.state.continuation;
        let branch_result = match prompt_mode {
            PromptMode::XsdRetry => build_xsd_retry_prompt(
                self,
                ctx,
                pass,
                continuation_state,
                xsd_retry_events.as_ref(),
            ),
            PromptMode::SameAgentRetry => {
                let Some(inputs) = materialized_inputs else {
                    return Err(ErrorEvent::ReviewInputsNotMaterialized { pass }.into());
                };
                build_same_agent_retry_prompt(
                    self,
                    ctx,
                    pass,
                    continuation_state,
                    inputs,
                    ReviewInlineContent {
                        plan_inline,
                        diff_inline,
                        baseline_oid: &baseline_oid_for_prompts,
                    },
                )
            }
            PromptMode::Normal => {
                let Some(inputs) = materialized_inputs else {
                    return Err(ErrorEvent::ReviewInputsNotMaterialized { pass }.into());
                };
                build_normal_prompt(
                    self,
                    ctx,
                    pass,
                    inputs,
                    plan_inline,
                    diff_inline,
                    &baseline_oid_for_prompts,
                )
            }
            PromptMode::Continuation => {
                return Err(ErrorEvent::ReviewContinuationNotSupported.into());
            }
        };
        let (
            prompt_key,
            review_prompt_xml,
            was_replayed,
            template_name,
            prompt_content_id,
            rendered_log,
        ) = match branch_result {
            Ok(tuple) => tuple,
            Err(early) => return Ok(*early),
        };

        write_review_prompt_file(ctx, &review_prompt_xml);

        let result = assemble_review_prompt_result(
            pass,
            prompt_key,
            review_prompt_xml,
            was_replayed,
            template_name,
            prompt_content_id,
            rendered_log,
        );
        let result = attach_xsd_retry_events(result, xsd_retry_events);

        Ok(result)
    }
}

// --- setup helpers ---

fn ensure_tmp_dir(ctx: &PhaseContext<'_>) -> Result<()> {
    let tmp_dir = Path::new(".agent/tmp");
    if !ctx.workspace.exists(tmp_dir) {
        ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
            ErrorEvent::WorkspaceCreateDirAllFailed {
                path: tmp_dir.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
        })?;
    }
    Ok(())
}

fn read_baseline_oid_for_prompts(ctx: &PhaseContext<'_>) -> Result<String> {
    match ctx
        .workspace
        .read(Path::new(MainEffectHandler::DIFF_BASELINE_PATH))
    {
        Ok(s) => Ok(s.trim().to_string()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(String::new()),
        Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
            path: MainEffectHandler::DIFF_BASELINE_PATH.to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()),
    }
}

fn resolve_plan_inline(
    ctx: &PhaseContext<'_>,
    representation: &PromptInputRepresentation,
) -> Result<Option<String>> {
    match representation {
        PromptInputRepresentation::Inline => {
            let plan = match ctx.workspace.read(Path::new(".agent/PLAN.md")) {
                Ok(plan) => plan,
                Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                    MainEffectHandler::sentinel_plan_content(ctx.config.isolation_mode)
                }
                Err(err) => {
                    return Err(ErrorEvent::WorkspaceReadFailed {
                        path: ".agent/PLAN.md".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    }
                    .into());
                }
            };
            Ok(Some(plan))
        }
        PromptInputRepresentation::FileReference { .. } => Ok(None),
    }
}

fn resolve_diff_inline(
    ctx: &PhaseContext<'_>,
    representation: &PromptInputRepresentation,
    baseline_oid: &str,
) -> Result<Option<String>> {
    match representation {
        PromptInputRepresentation::Inline => {
            let diff = match ctx.workspace.read(Path::new(".agent/DIFF.backup")) {
                Ok(diff) => diff,
                Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                    MainEffectHandler::fallback_diff_instructions(baseline_oid)
                }
                Err(err) => {
                    return Err(ErrorEvent::WorkspaceReadFailed {
                        path: ".agent/DIFF.backup".to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    }
                    .into());
                }
            };
            Ok(Some(diff))
        }
        PromptInputRepresentation::FileReference { .. } => Ok(None),
    }
}

fn build_plan_ref(
    inputs: &crate::reducer::state::MaterializedReviewInputs,
    plan_inline: Option<String>,
    isolation_mode: bool,
) -> PlanContentReference {
    match &inputs.plan.representation {
        PromptInputRepresentation::Inline => {
            let plan_inline = plan_inline
                .unwrap_or_else(|| MainEffectHandler::sentinel_plan_content(isolation_mode));
            PlanContentReference::Inline(plan_inline)
        }
        PromptInputRepresentation::FileReference { path } => PlanContentReference::ReadFromFile {
            primary_path: path.clone(),
            fallback_path: Some(Path::new(".agent/tmp/plan.xml").to_path_buf()),
            description: format!(
                "Plan is {} bytes (exceeds {} limit)",
                inputs.plan.final_bytes, MAX_INLINE_CONTENT_SIZE
            ),
        },
    }
}

fn build_diff_ref(
    inputs: &crate::reducer::state::MaterializedReviewInputs,
    diff_inline: Option<String>,
    baseline_oid: &str,
) -> DiffContentReference {
    match &inputs.diff.representation {
        PromptInputRepresentation::Inline => {
            let diff_inline = diff_inline
                .unwrap_or_else(|| MainEffectHandler::fallback_diff_instructions(baseline_oid));
            DiffContentReference::Inline(diff_inline)
        }
        PromptInputRepresentation::FileReference { path } => DiffContentReference::ReadFromFile {
            path: path.clone(),
            start_commit: baseline_oid.to_string(),
            description: format!(
                "Diff is {} bytes (exceeds {} limit)",
                inputs.diff.final_bytes, MAX_INLINE_CONTENT_SIZE
            ),
        },
    }
}

// --- XSD retry prompt branch ---

fn read_last_output_for_xsd_retry(ctx: &PhaseContext<'_>) -> (String, Option<String>) {
    use std::io::ErrorKind;
    let last_output_path = Path::new(".agent/tmp/last_output.xml");
    match ctx.workspace.read(last_output_path) {
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
    }
}

fn build_xsd_retry_rendered_log(
    ctx: &PhaseContext<'_>,
    xsd_error: &str,
    prompt_key: &str,
    was_replayed: bool,
    xsd_retry_events: Option<&Vec<PipelineEvent>>,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    use crate::agents::AgentRole;
    use crate::prompts::prompt_review_xsd_retry_with_context_files_and_log;

    if was_replayed {
        return Ok(None);
    }

    let rendered = prompt_review_xsd_retry_with_context_files_and_log(
        ctx.template_context,
        xsd_error,
        ctx.workspace,
        "review_xsd_retry",
    );
    if rendered.log.is_complete() {
        return Ok(Some(rendered.log));
    }
    let missing = rendered.log.unsubstituted.clone();
    let result = EffectResult::event(PipelineEvent::template_rendered(
        crate::reducer::event::PipelinePhase::Review,
        "review_xsd_retry".to_string(),
        rendered.log,
    ))
    .with_ui_event(UIEvent::PromptReplayHit {
        key: prompt_key.to_string(),
        was_replayed,
    })
    .with_additional_event(PipelineEvent::agent_template_variables_invalid(
        AgentRole::Reviewer,
        "review_xsd_retry".to_string(),
        missing,
        Vec::new(),
    ));
    let result = match xsd_retry_events {
        None => result,
        Some(events) => events
            .iter()
            .cloned()
            .fold(result, |r, ev| r.with_additional_event(ev)),
    };
    Err(Box::new(result))
}

type BranchResult = (
    String,
    String,
    bool,
    &'static str,
    Option<String>,
    Option<crate::prompts::SubstitutionLog>,
);

struct ReviewInlineContent<'a> {
    plan_inline: Option<String>,
    diff_inline: Option<String>,
    baseline_oid: &'a str,
}

fn build_xsd_retry_prompt(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    pass: u32,
    continuation_state: &crate::reducer::state::ContinuationState,
    xsd_retry_events: Option<&Vec<PipelineEvent>>,
) -> std::result::Result<BranchResult, Box<EffectResult>> {
    use crate::prompts::{
        get_stored_or_generate_prompt, prompt_review_xsd_retry_with_context_files_and_log,
        PromptScopeKey, RetryMode,
    };

    let scope_key = PromptScopeKey::for_review(
        pass,
        RetryMode::Xsd {
            count: continuation_state.invalid_output_attempts,
        },
        handler.state.recovery_epoch,
    );
    let prompt_key = scope_key.to_string();
    let xsd_error = continuation_state
        .last_review_xsd_error
        .as_deref()
        .filter(|s| !s.trim().is_empty())
        .unwrap_or("XML output failed validation. Provide valid XML output.");

    let (last_output, last_output_id_seed) = read_last_output_for_xsd_retry(ctx);
    let last_output_id = last_output_id_seed.map_or_else(
        || sha256_hex_str(&last_output),
        |seed| sha256_hex_str(&seed),
    );
    let current_prompt_content_id =
        build_review_xsd_retry_prompt_content_id(xsd_error, &last_output_id);

    let (prompt, was_replayed) = get_stored_or_generate_prompt(
        &scope_key,
        &handler.state.prompt_history,
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

    let rendered_log =
        build_xsd_retry_rendered_log(ctx, xsd_error, &prompt_key, was_replayed, xsd_retry_events)?;

    Ok((
        prompt_key,
        prompt,
        was_replayed,
        "review_xsd_retry",
        Some(current_prompt_content_id),
        rendered_log,
    ))
}

// --- Same-agent retry prompt branch ---

fn build_same_agent_retry_prompt(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    pass: u32,
    continuation_state: &crate::reducer::state::ContinuationState,
    inputs: &crate::reducer::state::MaterializedReviewInputs,
    inline: ReviewInlineContent<'_>,
) -> std::result::Result<BranchResult, Box<EffectResult>> {
    use crate::agents::AgentRole;
    use crate::prompts::{
        get_stored_or_generate_prompt, prompt_review_xml_with_references, PromptScopeKey, RetryMode,
    };

    let retry_preamble =
        crate::reducer::boundary::retry_guidance::same_agent_retry_preamble(continuation_state);
    let plan_ref = build_plan_ref(inputs, inline.plan_inline, ctx.config.isolation_mode);
    let diff_ref = build_diff_ref(inputs, inline.diff_inline, inline.baseline_oid);
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
        handler.state.recovery_epoch,
    );
    let prompt_key = scope_key.to_string();

    let current_prompt_content_id = build_review_prompt_content_id(
        "review_same_agent_retry",
        &inputs.plan.content_id_sha256,
        &inputs.diff.content_id_sha256,
        inline.baseline_oid,
        &handler.state.agent_chain.consumer_signature_sha256(),
    );

    let local_should_validate = std::cell::Cell::new(true);
    let (prompt, was_replayed) = get_stored_or_generate_prompt(
        &scope_key,
        &handler.state.prompt_history,
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
            .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                AgentRole::Reviewer,
                "review_xml".to_string(),
                missing,
                Vec::new(),
            ));
            return Err(Box::new(result));
        }
        Some(rendered.log)
    } else {
        None
    };

    Ok((
        prompt_key,
        prompt,
        was_replayed,
        "review_xml",
        Some(current_prompt_content_id),
        rendered_log,
    ))
}

// --- Normal prompt branch ---

fn build_normal_prompt(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    pass: u32,
    inputs: &crate::reducer::state::MaterializedReviewInputs,
    plan_inline: Option<String>,
    diff_inline: Option<String>,
    baseline_oid: &str,
) -> std::result::Result<BranchResult, Box<EffectResult>> {
    use crate::agents::AgentRole;
    use crate::prompts::{get_stored_or_generate_prompt, PromptScopeKey, RetryMode};

    let scope_key =
        PromptScopeKey::for_review(pass, RetryMode::Normal, handler.state.recovery_epoch);
    let prompt_key = scope_key.to_string();
    let plan_ref = build_plan_ref(inputs, plan_inline, ctx.config.isolation_mode);
    let diff_ref = build_diff_ref(inputs, diff_inline, baseline_oid);

    let current_prompt_content_id = build_review_prompt_content_id(
        "review_normal",
        &inputs.plan.content_id_sha256,
        &inputs.diff.content_id_sha256,
        baseline_oid,
        &handler.state.agent_chain.consumer_signature_sha256(),
    );
    let (prompt, was_replayed) = get_stored_or_generate_prompt(
        &scope_key,
        &handler.state.prompt_history,
        Some(&current_prompt_content_id),
        || {
            let refs = PromptContentReferences {
                prompt: None,
                plan: Some(plan_ref.clone()),
                diff: Some(diff_ref.clone()),
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
            .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                AgentRole::Reviewer,
                "review_xml".to_string(),
                missing,
                Vec::new(),
            ));
            return Err(Box::new(result));
        }
        Some(rendered.log)
    };

    Ok((
        prompt_key,
        prompt,
        was_replayed,
        "review_xml",
        Some(current_prompt_content_id),
        rendered_log,
    ))
}

// --- Final assembly helpers ---

fn write_review_prompt_file(ctx: &PhaseContext<'_>, content: &str) {
    if let Err(err) = ctx
        .workspace
        .write(Path::new(".agent/tmp/review_prompt.txt"), content)
    {
        ctx.logger.warn(&format!(
            "Failed to write review prompt file: {err}. Pipeline will continue."
        ));
    }
}

fn attach_xsd_retry_events(
    result: EffectResult,
    xsd_retry_events: Option<Vec<PipelineEvent>>,
) -> EffectResult {
    match xsd_retry_events {
        None => result,
        Some(events) => events
            .iter()
            .cloned()
            .fold(result, |r, ev| r.with_additional_event(ev)),
    }
}

fn assemble_review_prompt_result(
    pass: u32,
    prompt_key: String,
    review_prompt_xml: String,
    was_replayed: bool,
    template_name: &str,
    prompt_content_id: Option<String>,
    rendered_log: Option<crate::prompts::SubstitutionLog>,
) -> EffectResult {
    let prompt_captured_event = if was_replayed {
        None
    } else {
        Some(crate::reducer::event::PipelineEvent::PromptInput(
            crate::reducer::event::PromptInputEvent::PromptCaptured {
                key: prompt_key.clone(),
                content: review_prompt_xml,
                content_id: prompt_content_id,
            },
        ))
    };

    let result = EffectResult::event(PipelineEvent::review_prompt_prepared(pass)).with_ui_event(
        UIEvent::PromptReplayHit {
            key: prompt_key,
            was_replayed,
        },
    );
    let result = if let Some(event) = prompt_captured_event {
        result.with_additional_event(event)
    } else {
        result
    };
    if let Some(log) = rendered_log {
        result.with_additional_event(PipelineEvent::template_rendered(
            crate::reducer::event::PipelinePhase::Review,
            template_name.to_string(),
            log,
        ))
    } else {
        result
    }
}
