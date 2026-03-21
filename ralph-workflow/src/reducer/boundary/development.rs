use super::MainEffectHandler;
use crate::agents::AgentRole;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::development::boundary_domain::{
    build_continuation_context_markdown, derive_development_status, parse_files_changed_lines,
    select_representation_by_inline_budget,
};
use crate::phases::PhaseContext;
use crate::prompts::content_reference::MAX_INLINE_CONTENT_SIZE;
use crate::reducer::effect::{ContinuationContextData, EffectResult};
use crate::reducer::event::{AgentEvent, ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::{MaterializedPromptInput, PromptInputKind};
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
