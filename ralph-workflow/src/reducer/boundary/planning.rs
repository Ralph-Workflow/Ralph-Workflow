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
        if original_bytes > inline_budget_bytes {
            let result = result.with_ui_event(UIEvent::AgentActivity {
                agent: "pipeline".to_string(),
                message: format!(
                    "Oversize PROMPT: {} KB > {} KB; using file reference",
                    original_bytes / 1024,
                    inline_budget_bytes / 1024
                ),
            });
            return Ok(result.with_additional_event(
                PipelineEvent::prompt_input_oversize_detected(
                    PipelinePhase::Planning,
                    PromptInputKind::Prompt,
                    content_id_sha256,
                    original_bytes,
                    inline_budget_bytes,
                    "inline-embedding".to_string(),
                ),
            ));
        }
        Ok(result)
    }

    pub(in crate::reducer::boundary) fn prepare_planning_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
        prompt_mode: PromptMode,
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

        let continuation_state = &self.state.continuation;

        let (
            prompt,
            template_name,
            prompt_key,
            was_replayed,
            _should_validate,
            rendered_log,
            prompt_content_id,
            xsd_retry_events,
        ) = match prompt_mode {
            PromptMode::XsdRetry => {
                // Read last output from workspace, call pure hash/prompt-ID helpers, then write context file for retry effect.
                let last_output = ctx
                    .workspace
                    .read(Path::new(xml_paths::PLAN_XML))
                    .or_else(|err| {
                        if err.kind() == std::io::ErrorKind::NotFound {
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
                let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();
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
                            && m.last_output.consumer_signature_sha256 == consumer_signature_sha256
                            && ctx
                                .workspace
                                .exists(std::path::Path::new(".agent/tmp/last_output.xml"))
                    });

                let xsd_retry_events: Option<Vec<PipelineEvent>> = if !already_materialized {
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
                            PipelinePhase::Planning,
                            iteration,
                            input,
                        ))
                        .chain((last_output_bytes > inline_budget_bytes).then_some(
                            PipelineEvent::prompt_input_oversize_detected(
                                PipelinePhase::Planning,
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
                let scope_key = PromptScopeKey::for_planning(
                    iteration,
                    RetryMode::Xsd {
                        count: continuation_state.xsd_retry_count,
                    },
                    self.state.recovery_epoch,
                );
                let prompt_key = scope_key.to_string();

                let prompt_content_id = planning_xsd_retry_prompt_content_id(
                    &content_id_sha256,
                    &consumer_signature_sha256,
                );
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

                let rendered_log = if was_replayed {
                    None
                } else {
                    let rendered = crate::prompts::prompt_planning_xsd_retry_with_context_files_and_log(
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
                        .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                            AgentRole::Developer,
                            "planning_xsd_retry".to_string(),
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

                (
                    prompt,
                    "planning_xsd_retry",
                    Some(prompt_key),
                    was_replayed,
                    true,
                    rendered_log,
                    Some(prompt_content_id),
                    xsd_retry_events,
                )
            }
            PromptMode::SameAgentRetry => {
                // Read prior prompt/input metadata, call pure preamble combinator, then persist retry prompt.
                let retry_preamble =
                    super::retry_guidance::same_agent_retry_preamble(continuation_state);
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
                    RetryMode::SameAgent {
                        count: continuation_state.same_agent_retry_count,
                    },
                    self.state.recovery_epoch,
                );
                let prompt_key = scope_key.to_string();

                let prompt_content_id = planning_prompt_content_id(
                    "planning_same_agent_retry",
                    &inputs.prompt.content_id_sha256,
                    &inputs.prompt.consumer_signature_sha256,
                );

                let (prompt, was_replayed, should_validate) =
                    get_stored_or_generate_prompt_with_validation(
                        &scope_key,
                        &self.state.prompt_history,
                        Some(&prompt_content_id),
                        || {
                            let (base_prompt, local_should_validate) = ctx
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
                                            super::retry_guidance::strip_existing_same_agent_retry_preamble(
                                                &previous_prompt,
                                            )
                                            .to_string(),
                                            false,
                                        )
                                    },
                                );
                            (
                                apply_same_agent_retry_preamble(&retry_preamble, &base_prompt),
                                local_should_validate,
                            )
                        },
                    );

                let rendered_log = if should_validate && !was_replayed {
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
                        .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                            AgentRole::Developer,
                            "planning_xml".to_string(),
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
                (
                    prompt,
                    "planning_xml",
                    Some(prompt_key),
                    was_replayed,
                    should_validate,
                    rendered_log,
                    Some(prompt_content_id),
                    None,
                )
            }
            PromptMode::Normal => {
                // Read materialized prompt reference, call pure content-id builder, then render and persist prompt text.
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
                        .with_additional_event(PipelineEvent::agent_template_variables_invalid(
                            AgentRole::Developer,
                            "planning_xml".to_string(),
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

                (
                    prompt,
                    "planning_xml",
                    Some(prompt_key),
                    was_replayed,
                    true,
                    rendered_log,
                    Some(prompt_content_id),
                    None,
                )
            }
            PromptMode::Continuation => {
                return Err(ErrorEvent::PlanningContinuationNotSupported.into());
            }
        };

        let replay_key = prompt_key.as_deref().map(|k| (k.to_string(), was_replayed));
        let prompt_captured_event = prompt_key.as_deref().and_then(|prompt_key_str| {
            if was_replayed {
                None
            } else {
                Some(PipelineEvent::PromptInput(
                    PromptInputEvent::PromptCaptured {
                        key: prompt_key_str.to_string(),
                        content: prompt.clone(),
                        content_id: prompt_content_id.clone(),
                    },
                ))
            }
        });

        // Write generated prompt for agent invocation.
        if let Err(err) = ctx
            .workspace
            .write(Path::new(PLANNING_PROMPT_PATH), &prompt)
        {
            ctx.logger.warn(&format!(
                "Failed to write planning prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
            ));
        }

        let result = EffectResult::event(PipelineEvent::planning_prompt_prepared(iteration));
        let result = if let Some((key, replayed)) = replay_key {
            result.with_ui_event(UIEvent::PromptReplayHit {
                key,
                was_replayed: replayed,
            })
        } else {
            result
        };
        let result = if let Some(event) = prompt_captured_event {
            result.with_additional_event(event)
        } else {
            result
        };
        let result = xsd_retry_events
            .into_iter()
            .flatten()
            .fold(result, |r, ev| r.with_additional_event(ev));
        let result = if let Some(log) = rendered_log {
            result.with_additional_event(PipelineEvent::template_rendered(
                PipelinePhase::Planning,
                template_name.to_string(),
                log,
            ))
        } else {
            result
        };

        Ok(result)
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
                    .with_additional_event(PipelineEvent::planning_agent_invoked(iteration))
            }
        } else {
            result
        };
        Ok(result)
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
        // Map validity flag to transition UI event and completion event.
        let ui_events: Vec<_> = valid
            .then(|| self.phase_transition_ui(PipelinePhase::Development))
            .into_iter()
            .collect();
        EffectResult::with_ui(
            PipelineEvent::plan_generation_completed(iteration, valid),
            ui_events,
        )
    }
}
