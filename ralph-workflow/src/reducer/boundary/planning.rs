//! Planning phase handler.
//!
//! Handles all effects for the Planning phase:
//! - Input materialization (PROMPT.md size handling)
//! - Prompt preparation (normal, XSD retry, same-agent retry modes)
//! - Agent invocation and XML cleanup
//! - XML extraction and validation
//! - Output processing (PLAN.md writing, archiving)

use super::planning_helpers::{
    self, ensure_planning_tmp_dir, gen_planning_normal_rendered_log,
    gen_planning_xsd_retry_rendered_log, get_planning_inputs,
    materialize_planning_xsd_retry_last_output, maybe_add_planning_invoked_event, planning_outcome,
    read_planning_xsd_retry_last_output, write_planning_prompt, XsdRetryLastOutputParams,
};
use super::MainEffectHandler;
use crate::agents::session::parallel::{
    ParallelPlan as AgentParallelPlan, RestrictedEditArea, WorkUnit,
};
use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::agents::AgentRole;
use crate::files::llm_output_extraction::archive_xml_file_with_workspace;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::files::llm_output_extraction::validate_plan_xml;
use crate::phases::development::format_plan_as_markdown;
use crate::phases::planning::{planning_prompt_content_id, planning_xsd_retry_prompt_content_id};
use crate::phases::PhaseContext;
use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;
use crate::prompts::{
    get_stored_or_generate_prompt, PromptScopeKey, RetryMode, SessionCapabilities,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{AgentEvent, ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
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

/// Convert `ParallelPlanElements` (from XSD parsing) to `AgentParallelPlan` (for state).
fn convert_parallel_plan_elements(
    elements: &crate::files::llm_output_extraction::xsd_validation_plan::ParallelPlanElements,
) -> AgentParallelPlan {
    use crate::files::llm_output_extraction::xsd_validation_plan::WorkUnitElements;

    let work_units: Vec<WorkUnit> = elements
        .work_units
        .iter()
        .map(|wu: &WorkUnitElements| WorkUnit {
            unit_id: wu.unit_id.clone(),
            description: wu.description.clone(),
            edit_area: RestrictedEditArea {
                allowed_paths: wu.edit_area.paths.clone(),
                allowed_directories: wu.edit_area.directories.clone(),
            },
            dependencies: wu.dependencies.clone(),
        })
        .collect();

    AgentParallelPlan {
        parent_plan_id: "planning".to_string(), // Planning phase doesn't have a parent plan ID
        work_units,
    }
}

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
        Ok(planning_helpers::apply_planning_oversize_events(
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
                    SessionCapabilities::new(
                        &CapabilitySet::defaults_for_drain(SessionDrain::Planning),
                        &PolicyFlagSet::defaults_for_drain(SessionDrain::Planning),
                    ),
                )
                .content
            },
        );

        let rendered_log = match gen_planning_xsd_retry_rendered_log(ctx, was_replayed, &prompt_key)
        {
            Ok(log) => log,
            Err(early) => return Ok(*early),
        };

        let capture = planning_helpers::PlanningPromptCapture {
            prompt,
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
        };
        write_planning_prompt(ctx, &capture.prompt);
        Ok(planning_helpers::assemble_planning_prompt_result(
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
        let prompt_ref = planning_helpers::build_planning_prompt_ref(ctx, &inputs.prompt)?;
        let (capture, rendered_log) = match planning_helpers::build_same_agent_retry_capture(
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
        Ok(planning_helpers::assemble_planning_prompt_result(
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

        let prompt_ref = planning_helpers::build_planning_prompt_ref(ctx, &inputs.prompt)?;

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
                    SessionCapabilities::new(
                        &CapabilitySet::defaults_for_drain(SessionDrain::Planning),
                        &PolicyFlagSet::defaults_for_drain(SessionDrain::Planning),
                    ),
                );
                rendered.content
            },
        );

        let rendered_log = match gen_planning_normal_rendered_log(
            ctx,
            &prompt_ref,
            planning_helpers::prompt_needs_validation(true, was_replayed),
            &prompt_key,
            was_replayed,
        ) {
            Ok(log) => log,
            Err(early) => return Ok(*early),
        };

        let capture = planning_helpers::PlanningPromptCapture {
            prompt,
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
        };
        write_planning_prompt(ctx, &capture.prompt);
        Ok(planning_helpers::assemble_planning_prompt_result(
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
        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Planning);

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
            |session: &crate::agents::session::AgentSession| {
                let _session_vars =
                    crate::prompts::capability_template_variables_from_session(session);
                prompt.clone()
            },
        )?;
        Ok(maybe_add_planning_invoked_event(result, iteration))
    }

    pub(in crate::reducer::boundary) fn extract_planning_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
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
        let Ok(plan_xml) = ctx.workspace.read(Path::new(xml_paths::PLAN_XML)) else {
            return Ok(EffectResult::event(
                PipelineEvent::planning_output_validation_failed(
                    iteration,
                    self.state.continuation.invalid_output_attempts,
                ),
            ));
        };

        // Validate the XML and get structured elements
        let elements = match validate_plan_xml(&plan_xml) {
            Ok(elements) => elements,
            Err(_) => {
                return Ok(EffectResult::event(
                    PipelineEvent::planning_output_validation_failed(
                        iteration,
                        self.state.continuation.invalid_output_attempts,
                    ),
                ));
            }
        };

        // Convert PlanElements to markdown
        let markdown = format_plan_as_markdown(&elements);

        // Start with the planning_xml_validated event
        let mut result = EffectResult::with_ui(
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
        );

        // If parallel plan is present, emit ParallelPlanProduced event as additional event
        if let Some(parallel_elements) = &elements.parallel_plan {
            let parallel_plan = convert_parallel_plan_elements(parallel_elements);
            result = result.with_additional_event(PipelineEvent::Agent(
                AgentEvent::ParallelPlanProduced {
                    plan: parallel_plan,
                },
            ));
        }

        Ok(result)
    }

    pub(in crate::reducer::boundary) fn write_planning_markdown(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> Result<EffectResult> {
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
