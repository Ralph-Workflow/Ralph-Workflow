//! Planning prompt preparation.
//!
//! Handles the preparation of planning prompts in different modes:
//! - Normal: Initial planning prompt with PROMPT.md references
//! - `XsdRetry`: Retry prompt with validation errors and last output
//! - `SameAgentRetry`: Retry with same agent, prepending retry guidance
//!
//! Each mode handles input materialization, template rendering, and placeholder validation.

use super::super::MainEffectHandler;
use crate::agents::AgentRole;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::PhaseContext;
use crate::prompts::content_reference::{PromptContentReference, MAX_INLINE_CONTENT_SIZE};
use crate::prompts::{
    get_stored_or_generate_prompt, prompt_planning_xml_with_references, PromptScopeKey, RetryMode,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{ErrorEvent, PipelineEvent, PipelinePhase, PromptInputEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::PromptMode;
use crate::reducer::state::{
    MaterializedPromptInput, PromptInputKind, PromptInputRepresentation,
    PromptMaterializationReason,
};
use crate::reducer::ui_event::UIEvent;
use anyhow::Result;
use std::path::Path;

const PLANNING_PROMPT_PATH: &str = ".agent/tmp/planning_prompt.txt";

impl MainEffectHandler {
    pub(in crate::reducer::handler) fn prepare_planning_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        let mut additional_events: Vec<PipelineEvent> = Vec::new();
        let tmp_dir = Path::new(".agent/tmp");
        if !ctx.workspace.exists(tmp_dir) {
            ctx.workspace.create_dir_all(tmp_dir).map_err(|err| {
                ErrorEvent::WorkspaceCreateDirAllFailed {
                    path: tmp_dir.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
            })?;
        }

        let continuation_state = &self.state.continuation;

        let (prompt, template_name, prompt_key, was_replayed, _should_validate, rendered_log) =
            match prompt_mode {
                PromptMode::XsdRetry => {
                    // Materialize last invalid output to a stable path so the retry prompt can
                    // reference it without inlining content into the prompt itself.
                    let last_output = ctx
                        .workspace
                        .read(Path::new(xml_paths::PLAN_XML))
                        .or_else(|err| {
                            if err.kind() == std::io::ErrorKind::NotFound {
                                // Try reading from the archived .processed file as a fallback
                                let processed_path = Path::new(".agent/tmp/plan.xml.processed");
                                ctx.workspace.read(processed_path).inspect(|output| {
                                    ctx.logger.info(
                                        "XSD retry: using archived .processed file as last output",
                                    );
                                    let _ = output;
                                })
                            } else {
                                Err(err)
                            }
                        })
                        .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                            path: xml_paths::PLAN_XML.to_string(),
                            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                        })?;

                    let content_id_sha256 = sha256_hex_str(&last_output);
                    let consumer_signature_sha256 =
                        self.state.agent_chain.consumer_signature_sha256();
                    let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
                    let last_output_bytes = last_output.len() as u64;

                    let already_materialized = self
                        .state
                        .prompt_inputs
                        .xsd_retry_last_output
                        .as_ref()
                        .is_some_and(|m| {
                            m.phase == PipelinePhase::Planning
                                && m.scope_id == iteration
                                && m.last_output.content_id_sha256 == content_id_sha256
                                && m.last_output.consumer_signature_sha256
                                    == consumer_signature_sha256
                        });

                    if !already_materialized {
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
                            consumer_signature_sha256,
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
                            PipelinePhase::Planning,
                            iteration,
                            input,
                        ));
                        if last_output_bytes > inline_budget_bytes {
                            additional_events.push(PipelineEvent::prompt_input_oversize_detected(
                                PipelinePhase::Planning,
                                PromptInputKind::LastOutput,
                                content_id_sha256,
                                last_output_bytes,
                                inline_budget_bytes,
                                "xsd-retry-context".to_string(),
                            ));
                        }
                    }
                    let rendered =
                        crate::prompts::prompt_planning_xsd_retry_with_context_files_and_log(
                            ctx.template_context,
                            "Previous XML output failed XSD validation. Please provide valid XML conforming to the schema.",
                            ctx.workspace,
                            "planning_xsd_retry",
                        );

                    if !rendered.log.is_complete() {
                        let missing = rendered.log.unsubstituted.clone();
                        let result = EffectResult::event(PipelineEvent::template_rendered(
                            PipelinePhase::Planning,
                            "planning_xsd_retry".to_string(),
                            rendered.log,
                        ))
                        .with_additional_event(
                            PipelineEvent::agent_template_variables_invalid(
                                AgentRole::Developer,
                                "planning_xsd_retry".to_string(),
                                missing,
                                Vec::new(),
                            ),
                        );
                        return Ok(result);
                    }

                    (
                        rendered.content,
                        "planning_xsd_retry",
                        None,
                        false,
                        true,
                        Some(rendered.log),
                    )
                }
                PromptMode::SameAgentRetry => {
                    // Same-agent retry: prepend retry guidance to the last prepared prompt for this
                    // phase (preserves XSD retry / continuation context if present).
                    let retry_preamble =
                        super::super::retry_guidance::same_agent_retry_preamble(continuation_state);
                    let inputs = self
                        .state
                        .prompt_inputs
                        .planning
                        .as_ref()
                        .filter(|p| p.iteration == iteration)
                        .ok_or(ErrorEvent::PlanningInputsNotMaterialized { iteration })?;

                    let prompt_ref = match &inputs.prompt.representation {
                        PromptInputRepresentation::Inline => {
                            let prompt_md =
                                ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
                                    ErrorEvent::WorkspaceReadFailed {
                                        path: "PROMPT.md".to_string(),
                                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                                    }
                                })?;
                            PromptContentReference::inline(prompt_md)
                        }
                        PromptInputRepresentation::FileReference { path } => {
                            PromptContentReference::file_path(
                                path.clone(),
                                "Original user requirements from PROMPT.md",
                            )
                        }
                    };

                    let (base_prompt, should_validate) = ctx
                        .workspace
                        .read(Path::new(PLANNING_PROMPT_PATH))
                        .map_or_else(
                            |_| {
                                (
                                    prompt_planning_xml_with_references(
                                        ctx.template_context,
                                        &prompt_ref,
                                        ctx.workspace,
                                    ),
                                    true,
                                )
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
                    let prompt = format!("{retry_preamble}\n{base_prompt}");
                    let scope_key = PromptScopeKey::for_planning(
                        iteration,
                        RetryMode::SameAgent {
                            count: continuation_state.same_agent_retry_count,
                        },
                        self.state.recovery_epoch,
                    );
                    let prompt_key = scope_key.to_string();
                    let rendered_log = if should_validate {
                        let rendered = crate::prompts::prompt_planning_xml_with_references_and_log(
                            ctx.template_context,
                            &prompt_ref,
                            ctx.workspace,
                            "planning_xml",
                        );
                        if !rendered.log.is_complete() {
                            let missing = rendered.log.unsubstituted.clone();
                            let result = EffectResult::event(PipelineEvent::template_rendered(
                                PipelinePhase::Planning,
                                "planning_xml".to_string(),
                                rendered.log,
                            ))
                            .with_additional_event(
                                PipelineEvent::agent_template_variables_invalid(
                                    AgentRole::Developer,
                                    "planning_xml".to_string(),
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
                        prompt,
                        "planning_xml",
                        Some(prompt_key),
                        false,
                        should_validate,
                        rendered_log,
                    )
                }
                PromptMode::Normal => {
                    let inputs = self
                        .state
                        .prompt_inputs
                        .planning
                        .as_ref()
                        .filter(|p| p.iteration == iteration)
                        .ok_or(ErrorEvent::PlanningInputsNotMaterialized { iteration })?;

                    let prompt_ref = match &inputs.prompt.representation {
                        PromptInputRepresentation::Inline => {
                            let prompt_md =
                                ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
                                    ErrorEvent::WorkspaceReadFailed {
                                        path: "PROMPT.md".to_string(),
                                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                                    }
                                })?;
                            PromptContentReference::inline(prompt_md)
                        }
                        PromptInputRepresentation::FileReference { path } => {
                            PromptContentReference::file_path(
                                path.clone(),
                                "Original user requirements from PROMPT.md",
                            )
                        }
                    };

                    let scope_key = PromptScopeKey::for_planning(
                        iteration,
                        RetryMode::Normal,
                        self.state.recovery_epoch,
                    );
                    let prompt_key = scope_key.to_string();
                    let prompt_ref_for_template = prompt_ref.clone();
                    let (prompt, was_replayed) =
                        get_stored_or_generate_prompt(&scope_key, &self.state.prompt_history, None, || {
                            // Use log-based rendering
                            let rendered =
                                crate::prompts::prompt_planning_xml_with_references_and_log(
                                    ctx.template_context,
                                    &prompt_ref_for_template,
                                    ctx.workspace,
                                    "planning_xml",
                                );
                            rendered.content
                        });

                    // Validate freshly generated prompts (not replayed ones)
                    let rendered_log = if was_replayed {
                        None
                    } else {
                        let rendered = crate::prompts::prompt_planning_xml_with_references_and_log(
                            ctx.template_context,
                            &prompt_ref,
                            ctx.workspace,
                            "planning_xml",
                        );

                        if !rendered.log.is_complete() {
                            let missing = rendered.log.unsubstituted.clone();
                            let result = EffectResult::event(PipelineEvent::template_rendered(
                                PipelinePhase::Planning,
                                "planning_xml".to_string(),
                                rendered.log,
                            ))
                            .with_additional_event(
                                PipelineEvent::agent_template_variables_invalid(
                                    AgentRole::Developer,
                                    "planning_xml".to_string(),
                                    missing,
                                    Vec::new(),
                                ),
                            );
                            return Ok(result);
                        }
                        Some(rendered.log)
                    };

                    (
                        prompt,
                        "planning_xml",
                        Some(prompt_key),
                        was_replayed,
                        true,
                        rendered_log,
                    )
                }
                PromptMode::Continuation => {
                    return Err(ErrorEvent::PlanningContinuationNotSupported.into());
                }
            };

        // Collect replay observability key and prepare PromptCaptured event if needed.
        let replay_key = prompt_key.as_deref().map(|k| (k.to_string(), was_replayed));
        let prompt_captured_event = prompt_key.as_deref().and_then(|prompt_key_str| {
            if was_replayed {
                None
            } else {
                Some(PipelineEvent::PromptInput(PromptInputEvent::PromptCaptured {
                    key: prompt_key_str.to_string(),
                    content: prompt.clone(),
                    content_id: None,
                }))
            }
        });

        // Write prompt file (non-fatal: if write fails, log warning and continue)
        // Per acceptance criteria #5: Template rendering errors must never terminate the pipeline.
        // If the prompt file write fails, we continue with orchestration - loop recovery will
        // handle convergence if needed.
        if let Err(err) = ctx
            .workspace
            .write(Path::new(PLANNING_PROMPT_PATH), &prompt)
        {
            ctx.logger.warn(&format!(
                "Failed to write planning prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
            ));
        }

        // Build events: PlanningPromptPrepared is primary, with additional_events and TemplateRendered as additional
        let mut result = EffectResult::event(PipelineEvent::planning_prompt_prepared(iteration));

        // Emit replay observability event (RFC-007)
        if let Some((key, replayed)) = replay_key {
            result = result.with_ui_event(UIEvent::PromptReplayHit {
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
                crate::reducer::event::PipelinePhase::Planning,
                template_name.to_string(),
                log,
            ));
        }

        Ok(result)
    }
}
