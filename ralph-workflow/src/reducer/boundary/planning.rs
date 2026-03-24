//! Planning phase handler.
//!
//! Handles all effects for the Planning phase:
//! - Input materialization (PROMPT.md size handling)
//! - Prompt preparation (normal, XSD retry, same-agent retry modes)
//! - Agent invocation and XML cleanup
//! - XML extraction and validation
//! - Output processing (PLAN.md writing, archiving)

use super::get_stored_or_generate_prompt_with_validation;
use super::MainEffectHandler;
use crate::agents::AgentRole;
use crate::files::llm_output_extraction::archive_xml_file_with_workspace;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::planning::{
    apply_same_agent_retry_preamble, parse_planning_markdown, planning_prompt_content_id,
    planning_xsd_retry_prompt_content_id,
};
use crate::phases::PhaseContext;
use crate::prompts::content_reference::{PromptContentReference, MAX_INLINE_CONTENT_SIZE};
use crate::prompts::{
    get_stored_or_generate_prompt, prompt_planning_xml_with_references, PromptScopeKey, RetryMode,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{
    AgentEvent, ErrorEvent, PipelineEvent, PipelinePhase, PromptInputEvent, WorkspaceIoErrorKind,
};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::PromptMode;
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason,
};
use crate::reducer::ui_event::{UIEvent, XmlOutputContext, XmlOutputType};
use anyhow::Result;
use std::path::Path;

const PLANNING_PROMPT_PATH: &str = ".agent/tmp/planning_prompt.txt";

impl MainEffectHandler {
    pub(in crate::reducer::boundary) fn materialize_planning_inputs(
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

        let content_id_sha256 = sha256_hex_str(&prompt_md);
        let original_bytes = prompt_md.len() as u64;
        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();

        let prompt_backup_path = Path::new(".agent/PROMPT.md.backup");
        let (representation, reason) = if original_bytes > inline_budget_bytes {
            crate::files::create_prompt_backup_with_workspace(ctx.workspace).map_err(|err| {
                ErrorEvent::WorkspaceWriteFailed {
                    path: prompt_backup_path.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
            ctx.logger.warn(&format!(
                "PROMPT size ({} KB) exceeds inline limit ({} KB). Referencing: {}",
                original_bytes / 1024,
                inline_budget_bytes / 1024,
                prompt_backup_path.display()
            ));
            (
                PromptInputRepresentation::FileReference {
                    path: prompt_backup_path.to_path_buf(),
                },
                PromptMaterializationReason::InlineBudgetExceeded,
            )
        } else {
            (
                PromptInputRepresentation::Inline,
                PromptMaterializationReason::WithinBudgets,
            )
        };

        let input = MaterializedPromptInput {
            kind: PromptInputKind::Prompt,
            content_id_sha256: content_id_sha256.clone(),
            consumer_signature_sha256,
            original_bytes,
            final_bytes: original_bytes,
            model_budget_bytes: None,
            inline_budget_bytes: Some(inline_budget_bytes),
            representation,
            reason,
        };

        let result = EffectResult::event(PipelineEvent::planning_inputs_materialized(
            iteration, input,
        ));
        Ok(apply_planning_oversize_events(
            result,
            original_bytes,
            inline_budget_bytes,
            &content_id_sha256,
        ))
    }

    pub(in crate::reducer::boundary) fn prepare_planning_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        ensure_planning_tmp_dir(ctx)?;
        match prompt_mode {
            PromptMode::XsdRetry => self.prepare_planning_xsd_retry_prompt(ctx, iteration),
            PromptMode::SameAgentRetry => {
                self.prepare_planning_same_agent_retry_prompt(ctx, iteration)
            }
            PromptMode::Normal => self.prepare_planning_normal_prompt(ctx, iteration),
            PromptMode::Continuation => Err(ErrorEvent::PlanningContinuationNotSupported.into()),
        }
    }

    fn prepare_planning_xsd_retry_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        // Read last output from workspace, call pure hash/prompt-ID helpers, then write context file for retry effect.
        let last_output = read_planning_xsd_retry_last_output(ctx)?;
        let content_id_sha256 = sha256_hex_str(&last_output);
        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();
        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let last_output_bytes = last_output.len() as u64;

        let xsd_retry_events = materialize_planning_xsd_retry_last_output(
            self,
            ctx,
            iteration,
            &last_output,
            XsdRetryLastOutputParams {
                content_id_sha256: &content_id_sha256,
                consumer_signature_sha256: &consumer_signature_sha256,
                inline_budget_bytes,
                last_output_bytes,
            },
        )?;

        let scope_key = PromptScopeKey::for_planning(
            iteration,
            RetryMode::Xsd {
                count: self.state.continuation.xsd_retry_count,
            },
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let prompt_content_id =
            planning_xsd_retry_prompt_content_id(&content_id_sha256, &consumer_signature_sha256);

        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                crate::prompts::prompt_planning_xsd_retry_with_context_files_and_log(
                    ctx.template_context,
                    "Previous XML output failed XSD validation. Please provide valid XML conforming to the schema.",
                    ctx.workspace,
                    "planning_xsd_retry",
                )
                .content
            },
        );

        let rendered_log = match gen_planning_xsd_retry_rendered_log(ctx, was_replayed, &prompt_key)
        {
            Ok(log) => log,
            Err(early) => return Ok(*early),
        };

        let capture = PlanningPromptCapture {
            prompt,
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
        };
        write_planning_prompt(ctx, &capture.prompt);
        Ok(assemble_planning_prompt_result(
            iteration,
            capture,
            "planning_xsd_retry",
            rendered_log,
            xsd_retry_events,
        ))
    }

    fn prepare_planning_same_agent_retry_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        let retry_preamble =
            super::retry_guidance::same_agent_retry_preamble(&self.state.continuation);
        let inputs = get_planning_inputs(self, iteration)?;
        let prompt_ref = build_planning_prompt_ref(ctx, &inputs.prompt)?;
        let (capture, rendered_log) = match build_same_agent_retry_capture(
            ctx,
            self,
            iteration,
            &prompt_ref,
            &retry_preamble,
            &inputs.prompt,
        ) {
            Ok(pair) => pair,
            Err(early) => return Ok(*early),
        };

        write_planning_prompt(ctx, &capture.prompt);
        Ok(assemble_planning_prompt_result(
            iteration,
            capture,
            "planning_xml",
            rendered_log,
            None,
        ))
    }

    fn prepare_planning_normal_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        let inputs = get_planning_inputs(self, iteration)?;

        let prompt_ref = build_planning_prompt_ref(ctx, &inputs.prompt)?;

        let scope_key =
            PromptScopeKey::for_planning(iteration, RetryMode::Normal, self.state.recovery_epoch);
        let prompt_key = scope_key.to_string();

        let prompt_content_id = planning_prompt_content_id(
            "planning_normal",
            &inputs.prompt.content_id_sha256,
            &inputs.prompt.consumer_signature_sha256,
        );
        let prompt_ref_for_template = prompt_ref.clone();
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                let rendered = crate::prompts::prompt_planning_xml_with_references_and_log(
                    ctx.template_context,
                    &prompt_ref_for_template,
                    ctx.workspace,
                    "planning_xml",
                );
                rendered.content
            },
        );

        let rendered_log = match gen_planning_normal_rendered_log(
            ctx,
            &prompt_ref,
            !was_replayed,
            &prompt_key,
            was_replayed,
        ) {
            Ok(log) => log,
            Err(early) => return Ok(*early),
        };

        let capture = PlanningPromptCapture {
            prompt,
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
        };
        write_planning_prompt(ctx, &capture.prompt);
        Ok(assemble_planning_prompt_result(
            iteration,
            capture,
            "planning_xml",
            rendered_log,
            None,
        ))
    }

    pub(in crate::reducer::boundary) fn invoke_planning_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        // Normalize agent chain state before invocation for determinism
        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Planning);

        // Read prepared prompt, then invoke agent effect.
        let prompt = match ctx.workspace.read(Path::new(PLANNING_PROMPT_PATH)) {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                return Err(ErrorEvent::PlanningPromptMissing { iteration }.into());
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: PLANNING_PROMPT_PATH.to_string(),
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
            .unwrap_or_else(|| ctx.developer_agent.to_string());

        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Planning,
            AgentRole::Developer,
            &agent,
            None,
            |_session: &crate::agents::session::AgentSession| prompt.clone(),
        )?;
        Ok(maybe_add_planning_invoked_event(result, iteration))
    }

    pub(in crate::reducer::boundary) fn extract_planning_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        // Read XML from workspace and emit extraction event.
        let plan_xml = Path::new(xml_paths::PLAN_XML);
        let content = ctx.workspace.read(plan_xml);

        match content {
            Ok(_) => EffectResult::event(PipelineEvent::planning_xml_extracted(iteration)),
            Err(_) => EffectResult::event(PipelineEvent::planning_xml_missing(
                iteration,
                self.state.continuation.invalid_output_attempts,
            )),
        }
    }

    pub(in crate::reducer::boundary) fn validate_planning_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        // Read XML from workspace, call pure parser/formatter, then emit validation outcome.
        let Ok(plan_xml) = ctx.workspace.read(Path::new(xml_paths::PLAN_XML)) else {
            return Ok(EffectResult::event(
                PipelineEvent::planning_output_validation_failed(
                    iteration,
                    self.state.continuation.invalid_output_attempts,
                ),
            ));
        };

        parse_planning_markdown(&plan_xml).map_or_else(
            || {
                Ok(EffectResult::event(
                    PipelineEvent::planning_output_validation_failed(
                        iteration,
                        self.state.continuation.invalid_output_attempts,
                    ),
                ))
            },
            |markdown| {
                Ok(EffectResult::with_ui(
                    PipelineEvent::planning_xml_validated(iteration, true, Some(markdown)),
                    vec![UIEvent::XmlOutput {
                        xml_type: XmlOutputType::DevelopmentPlan,
                        content: plan_xml,
                        context: Some(XmlOutputContext {
                            iteration: Some(iteration),
                            pass: None,
                            snippets: Vec::new(),
                        }),
                    }],
                ))
            },
        )
    }

    pub(in crate::reducer::boundary) fn write_planning_markdown(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
        // Read validated markdown from state, then write PLAN.md through workspace.
        let markdown = self
            .state
            .planning_validated_outcome
            .as_ref()
            .filter(|outcome| outcome.iteration == iteration)
            .and_then(|outcome| outcome.markdown.clone())
            .ok_or(ErrorEvent::ValidatedPlanningMarkdownMissing { iteration })?;

        ctx.workspace
            .write(Path::new(".agent/PLAN.md"), &markdown)
            .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                path: ".agent/PLAN.md".to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        Ok(EffectResult::event(
            PipelineEvent::planning_markdown_written(iteration),
        ))
    }

    pub(in crate::reducer::boundary) fn archive_planning_xml(
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        // Archive canonical XML path and emit archive event.
        archive_xml_file_with_workspace(ctx.workspace, Path::new(xml_paths::PLAN_XML));
        EffectResult::event(PipelineEvent::planning_xml_archived(iteration))
    }

    pub(in crate::reducer::boundary) fn apply_planning_outcome(
        &self,
        _ctx: &mut PhaseContext<'_>,
        iteration: u32,
        valid: bool,
    ) -> EffectResult {
        let (event, ui_events) = planning_outcome(iteration, valid, self.state.phase);
        EffectResult::with_ui(event, ui_events)
    }
}

/// Pure outcome mapping: given validity and the current phase, return the
/// completion event and any UI transition events.
///
/// Extracted so callers receive a typed `(PipelineEvent, Vec<UIEvent>)` that can
/// be unit-tested without constructing `MainEffectHandler` or a `PhaseContext`.
fn planning_outcome(
    iteration: u32,
    valid: bool,
    current_phase: PipelinePhase,
) -> (PipelineEvent, Vec<UIEvent>) {
    let ui_events: Vec<_> = valid
        .then_some(UIEvent::PhaseTransition {
            from: Some(current_phase),
            to: PipelinePhase::Development,
        })
        .into_iter()
        .collect();
    (
        PipelineEvent::plan_generation_completed(iteration, valid),
        ui_events,
    )
}

fn get_planning_inputs(
    handler: &MainEffectHandler,
    iteration: u32,
) -> std::result::Result<
    &crate::reducer::state::MaterializedPlanningInputs,
    crate::reducer::event::ErrorEvent,
> {
    handler
        .state
        .prompt_inputs
        .planning
        .as_ref()
        .filter(|p| p.iteration == iteration)
        .ok_or(ErrorEvent::PlanningInputsNotMaterialized { iteration })
}

fn apply_planning_oversize_events(
    result: EffectResult,
    original_bytes: u64,
    inline_budget_bytes: u64,
    content_id_sha256: &str,
) -> EffectResult {
    if original_bytes > inline_budget_bytes {
        let result = result.with_ui_event(UIEvent::AgentActivity {
            agent: "pipeline".to_string(),
            message: format!(
                "Oversize PROMPT: {} KB > {} KB; using file reference",
                original_bytes / 1024,
                inline_budget_bytes / 1024
            ),
        });
        result.with_additional_event(PipelineEvent::prompt_input_oversize_detected(
            PipelinePhase::Planning,
            PromptInputKind::Prompt,
            content_id_sha256.to_string(),
            original_bytes,
            inline_budget_bytes,
            "inline-embedding".to_string(),
        ))
    } else {
        result
    }
}

fn build_same_agent_scope_and_ids(
    handler: &MainEffectHandler,
    iteration: u32,
    prompt_info: &MaterializedPromptInput,
) -> (PromptScopeKey, String, String) {
    let scope_key = PromptScopeKey::for_planning(
        iteration,
        RetryMode::SameAgent {
            count: handler.state.continuation.same_agent_retry_count,
        },
        handler.state.recovery_epoch,
    );
    let prompt_content_id = planning_prompt_content_id(
        "planning_same_agent_retry",
        &prompt_info.content_id_sha256,
        &prompt_info.consumer_signature_sha256,
    );
    let prompt_key = scope_key.to_string();
    (scope_key, prompt_key, prompt_content_id)
}

fn build_same_agent_retry_capture(
    ctx: &PhaseContext<'_>,
    handler: &MainEffectHandler,
    iteration: u32,
    prompt_ref: &PromptContentReference,
    retry_preamble: &str,
    prompt_info: &MaterializedPromptInput,
) -> std::result::Result<
    (
        PlanningPromptCapture,
        Option<crate::prompts::SubstitutionLog>,
    ),
    Box<EffectResult>,
> {
    let (scope_key, prompt_key, prompt_content_id) =
        build_same_agent_scope_and_ids(handler, iteration, prompt_info);
    let (prompt, was_replayed, should_validate) = get_stored_or_generate_prompt_with_validation(
        &scope_key,
        &handler.state.prompt_history,
        Some(&prompt_content_id),
        || load_same_agent_retry_base_prompt(ctx, prompt_ref, retry_preamble),
    );
    let rendered_log = gen_planning_normal_rendered_log(
        ctx,
        prompt_ref,
        prompt_needs_validation(should_validate, was_replayed),
        &prompt_key,
        was_replayed,
    )?;
    let capture = PlanningPromptCapture {
        prompt,
        prompt_key: Some(prompt_key),
        was_replayed,
        prompt_content_id: Some(prompt_content_id),
    };
    Ok((capture, rendered_log))
}

fn prompt_needs_validation(should_validate: bool, was_replayed: bool) -> bool {
    should_validate && !was_replayed
}

fn write_planning_prompt(ctx: &PhaseContext<'_>, prompt: &str) {
    if let Err(err) = ctx.workspace.write(Path::new(PLANNING_PROMPT_PATH), prompt) {
        ctx.logger.warn(&format!(
            "Failed to write planning prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
        ));
    }
}

/// Load the base prompt for a same-agent retry and return (prompt_with_preamble, should_validate).
///
/// If a previous prompt exists on disk, strips its preamble and reuses it (no validation needed).
/// Otherwise generates a fresh prompt from templates (validation required).
fn load_same_agent_retry_base_prompt(
    ctx: &PhaseContext<'_>,
    prompt_ref: &crate::prompts::content_reference::PromptContentReference,
    retry_preamble: &str,
) -> (String, bool) {
    let (base_prompt, should_validate) = ctx
        .workspace
        .read(Path::new(PLANNING_PROMPT_PATH))
        .map_or_else(
            |_| {
                (
                    prompt_planning_xml_with_references(
                        ctx.template_context,
                        prompt_ref,
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
    (
        apply_same_agent_retry_preamble(retry_preamble, &base_prompt),
        should_validate,
    )
}

struct PlanningPromptCapture {
    prompt: String,
    prompt_key: Option<String>,
    was_replayed: bool,
    prompt_content_id: Option<String>,
}

fn ensure_planning_tmp_dir(ctx: &PhaseContext<'_>) -> Result<()> {
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

fn read_planning_xsd_retry_last_output(ctx: &PhaseContext<'_>) -> Result<String> {
    ctx.workspace
        .read(Path::new(xml_paths::PLAN_XML))
        .or_else(|err| {
            if err.kind() == std::io::ErrorKind::NotFound {
                let processed_path = Path::new(".agent/tmp/plan.xml.processed");
                ctx.workspace.read(processed_path).inspect(|_output| {
                    ctx.logger
                        .info("XSD retry: using archived .processed file as last output");
                })
            } else {
                Err(err)
            }
        })
        .map_err(|err| {
            ErrorEvent::WorkspaceReadFailed {
                path: xml_paths::PLAN_XML.to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
            .into()
        })
}

struct XsdRetryLastOutputParams<'a> {
    content_id_sha256: &'a str,
    consumer_signature_sha256: &'a str,
    inline_budget_bytes: u64,
    last_output_bytes: u64,
}

fn is_xsd_retry_last_output_already_materialized(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    iteration: u32,
    params: &XsdRetryLastOutputParams<'_>,
) -> bool {
    handler
        .state
        .prompt_inputs
        .xsd_retry_last_output
        .as_ref()
        .is_some_and(|m| {
            m.phase == PipelinePhase::Planning
                && m.scope_id == iteration
                && m.last_output.content_id_sha256 == params.content_id_sha256
                && m.last_output.consumer_signature_sha256 == params.consumer_signature_sha256
                && ctx
                    .workspace
                    .exists(std::path::Path::new(".agent/tmp/last_output.xml"))
        })
}

fn build_xsd_retry_last_output_events(
    iteration: u32,
    input: MaterializedPromptInput,
    content_id_sha256: &str,
    last_output_bytes: u64,
    inline_budget_bytes: u64,
) -> Vec<PipelineEvent> {
    std::iter::once(PipelineEvent::xsd_retry_last_output_materialized(
        PipelinePhase::Planning,
        iteration,
        input,
    ))
    .chain((last_output_bytes > inline_budget_bytes).then_some(
        PipelineEvent::prompt_input_oversize_detected(
            PipelinePhase::Planning,
            PromptInputKind::LastOutput,
            content_id_sha256.to_string(),
            last_output_bytes,
            inline_budget_bytes,
            "xsd-retry-context".to_string(),
        ),
    ))
    .collect()
}

fn materialize_planning_xsd_retry_last_output(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    iteration: u32,
    last_output: &str,
    params: XsdRetryLastOutputParams<'_>,
) -> Result<Option<Vec<PipelineEvent>>> {
    if is_xsd_retry_last_output_already_materialized(handler, ctx, iteration, &params) {
        return Ok(None);
    }
    let last_output_path = Path::new(".agent/tmp/last_output.xml");
    ctx.workspace
        .write_atomic(last_output_path, last_output)
        .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
            path: last_output_path.display().to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        })?;
    let input = MaterializedPromptInput {
        kind: PromptInputKind::LastOutput,
        content_id_sha256: params.content_id_sha256.to_string(),
        consumer_signature_sha256: params.consumer_signature_sha256.to_string(),
        original_bytes: params.last_output_bytes,
        final_bytes: params.last_output_bytes,
        model_budget_bytes: None,
        inline_budget_bytes: Some(params.inline_budget_bytes),
        representation: PromptInputRepresentation::FileReference {
            path: last_output_path.to_path_buf(),
        },
        reason: PromptMaterializationReason::PolicyForcedReference,
    };
    Ok(Some(build_xsd_retry_last_output_events(
        iteration,
        input,
        params.content_id_sha256,
        params.last_output_bytes,
        params.inline_budget_bytes,
    )))
}

fn xsd_retry_incomplete_early_return(
    log: &crate::prompts::SubstitutionLog,
    prompt_key: &str,
    was_replayed: bool,
) -> Box<EffectResult> {
    Box::new(
        planning_template_incomplete_early_return(
            log,
            "planning_xsd_retry",
            prompt_key,
            was_replayed,
        )
        .unwrap_or_else(|| EffectResult::event(PipelineEvent::planning_prompt_prepared(0))),
    )
}

fn gen_planning_xsd_retry_rendered_log(
    ctx: &PhaseContext<'_>,
    was_replayed: bool,
    prompt_key: &str,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    if was_replayed {
        return Ok(None);
    }
    let rendered = crate::prompts::prompt_planning_xsd_retry_with_context_files_and_log(
        ctx.template_context,
        "Previous XML output failed XSD validation. Please provide valid XML conforming to the schema.",
        ctx.workspace,
        "planning_xsd_retry",
    );
    match rendered.log.is_complete() {
        true => Ok(Some(rendered.log)),
        false => Err(xsd_retry_incomplete_early_return(
            &rendered.log,
            prompt_key,
            was_replayed,
        )),
    }
}

fn gen_planning_normal_rendered_log(
    ctx: &PhaseContext<'_>,
    prompt_ref: &PromptContentReference,
    should_render: bool,
    prompt_key: &str,
    was_replayed: bool,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    if !should_render {
        return Ok(None);
    }
    let rendered = crate::prompts::prompt_planning_xml_with_references_and_log(
        ctx.template_context,
        prompt_ref,
        ctx.workspace,
        "planning_xml",
    );
    match rendered.log.is_complete() {
        true => Ok(Some(rendered.log)),
        false => {
            let missing = rendered.log.unsubstituted.clone();
            Err(Box::new(
                EffectResult::event(PipelineEvent::template_rendered(
                    PipelinePhase::Planning,
                    "planning_xml".to_string(),
                    rendered.log,
                ))
                .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                    AgentRole::Developer,
                    "planning_xml".to_string(),
                    missing,
                    Vec::new(),
                ))
                .with_ui_event(UIEvent::PromptReplayHit {
                    key: prompt_key.to_string(),
                    was_replayed,
                }),
            ))
        }
    }
}

fn assemble_planning_prompt_result(
    iteration: u32,
    capture: PlanningPromptCapture,
    template_name: &str,
    rendered_log: Option<crate::prompts::SubstitutionLog>,
    xsd_retry_events: Option<Vec<PipelineEvent>>,
) -> EffectResult {
    let PlanningPromptCapture {
        prompt,
        prompt_key,
        was_replayed,
        prompt_content_id,
    } = capture;
    let replay_key = prompt_key.as_deref().map(|k| (k.to_string(), was_replayed));
    let prompt_captured_event = prompt_key.as_deref().and_then(|k| {
        (!was_replayed).then(|| {
            PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                key: k.to_string(),
                content: prompt.clone(),
                content_id: prompt_content_id.clone(),
            })
        })
    });
    let result = EffectResult::event(PipelineEvent::planning_prompt_prepared(iteration));
    let result = replay_key.map_or(result.clone(), |(key, replayed)| {
        result.with_ui_event(UIEvent::PromptReplayHit {
            key,
            was_replayed: replayed,
        })
    });
    let result =
        prompt_captured_event.map_or(result.clone(), |ev| result.with_additional_event(ev));
    let result = xsd_retry_events
        .into_iter()
        .flatten()
        .fold(result, |r, ev| r.with_additional_event(ev));
    rendered_log.map_or(result.clone(), |log| {
        result.with_additional_event(PipelineEvent::template_rendered(
            PipelinePhase::Planning,
            template_name.to_string(),
            log,
        ))
    })
}

fn maybe_add_planning_invoked_event(result: EffectResult, iteration: u32) -> EffectResult {
    if result.additional_events.iter().any(|e| {
        matches!(
            e,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        )
    }) {
        result.with_additional_event(PipelineEvent::planning_agent_invoked(iteration))
    } else {
        result
    }
}

fn planning_template_incomplete_early_return(
    log: &crate::prompts::SubstitutionLog,
    template_name: &str,
    prompt_key: &str,
    was_replayed: bool,
) -> Option<EffectResult> {
    if log.is_complete() {
        return None;
    }
    let missing = log.unsubstituted.clone();
    Some(
        EffectResult::event(PipelineEvent::template_rendered(
            PipelinePhase::Planning,
            template_name.to_string(),
            log.clone(),
        ))
        .with_additional_event(PipelineEvent::agent_template_variables_invalid(
            AgentRole::Developer,
            template_name.to_string(),
            missing,
            Vec::new(),
        ))
        .with_ui_event(UIEvent::PromptReplayHit {
            key: prompt_key.to_string(),
            was_replayed,
        }),
    )
}

fn build_planning_prompt_ref(
    ctx: &PhaseContext<'_>,
    input: &MaterializedPromptInput,
) -> Result<PromptContentReference> {
    match &input.representation {
        PromptInputRepresentation::Inline => {
            let prompt_md = ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
                ErrorEvent::WorkspaceReadFailed {
                    path: "PROMPT.md".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
            Ok(PromptContentReference::inline(prompt_md))
        }
        PromptInputRepresentation::FileReference { path } => Ok(PromptContentReference::file_path(
            path.clone(),
            "Original user requirements from PROMPT.md",
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reducer::event::PipelinePhase;

    #[test]
    fn planning_outcome_emits_completion_event_with_valid_flag() {
        let (event, _) = planning_outcome(2, true, PipelinePhase::Planning);
        assert!(
            matches!(
                event,
                PipelineEvent::Planning(
                    crate::reducer::event::PlanningEvent::GenerationCompleted {
                        iteration: 2,
                        valid: true
                    }
                )
            ),
            "valid=true must produce GenerationCompleted(valid=true), got {event:?}"
        );
    }

    #[test]
    fn planning_outcome_includes_phase_transition_when_valid() {
        let (_, ui_events) = planning_outcome(1, true, PipelinePhase::Planning);
        assert_eq!(
            ui_events.len(),
            1,
            "valid=true must include one UI phase transition event"
        );
        assert!(
            matches!(
                ui_events[0],
                UIEvent::PhaseTransition {
                    from: Some(PipelinePhase::Planning),
                    to: PipelinePhase::Development,
                }
            ),
            "UI event must transition Planning → Development, got {:?}",
            ui_events[0]
        );
    }

    #[test]
    fn planning_outcome_no_ui_transition_when_invalid() {
        let (_, ui_events) = planning_outcome(1, false, PipelinePhase::Planning);
        assert!(
            ui_events.is_empty(),
            "valid=false must produce no UI transition events"
        );
    }
}
