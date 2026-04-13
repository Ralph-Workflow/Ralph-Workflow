use super::MainEffectHandler;
use crate::agents::session::AgentSession;
use crate::agents::AgentRole;
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
        // Read the pre-generated prompt for replay determinism
        let pre_generated_prompt = ctx
            .workspace
            .read(Path::new(".agent/tmp/development_prompt.txt"))
            .map_err(|_| ErrorEvent::DevelopmentPromptMissing { iteration })?;
        let agent = self
            .state
            .agent_chain
            .current_agent()
            .cloned()
            .unwrap_or_else(|| ctx.developer_agent.to_string());
        // Pass a closure that returns the prompt.
        //
        // RFC-009: The closure receives the AgentSession created by invoke_agent.
        // In V1, session capabilities == drain defaults (V1 invariant), so the pre-generated
        // prompt is correct. The closure still calls capability_template_variables_from_session
        // to verify the V1 invariant holds and to exercise the RFC-009 session-aware path.
        //
        // V2 Note: When session capabilities may differ from drain defaults, closures will need
        // template content + refs to re-render with session capabilities. This requires passing
        // intermediate data (not just rendered string) from prepare handlers to closures.
        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Development,
            AgentRole::Developer,
            &agent,
            None,
            |session: &AgentSession| {
                // RFC-009: Use session capabilities to compute template variables.
                // In V1 this is equivalent to defaults_for_drain, but exercises the
                // session-aware code path for RFC-009 verification.
                let _session_vars =
                    crate::prompts::capability_template_variables_from_session(session);
                pre_generated_prompt.clone()
            },
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
        crate::files::archive_json_artifact_with_workspace(ctx.workspace, "development_result");
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
        let inline_budget_bytes = MAX_INLINE_CONTENT_SIZE as u64;
        let consumer_signature_sha256 = self.state.agent_chain.consumer_signature_sha256();
        let prompt_input = materialize_development_prompt_input(
            ctx,
            inline_budget_bytes,
            &consumer_signature_sha256,
        )?;
        let plan_input = materialize_development_plan_input(
            ctx,
            inline_budget_bytes,
            consumer_signature_sha256,
        )?;
        let result = EffectResult::event(PipelineEvent::development_inputs_materialized(
            iteration,
            prompt_input.clone(),
            plan_input.clone(),
        ));
        let result = apply_oversize_prompt_events(result, &prompt_input, inline_budget_bytes);
        let result = apply_oversize_plan_events(result, &plan_input, inline_budget_bytes);
        Ok(result)
    }
}

fn materialize_development_prompt_input(
    ctx: &PhaseContext<'_>,
    inline_budget_bytes: u64,
    consumer_signature_sha256: &str,
) -> Result<MaterializedPromptInput, ErrorEvent> {
    let prompt_md = ctx.workspace.read(Path::new("PROMPT.md")).map_err(|err| {
        ErrorEvent::WorkspaceReadFailed {
            path: "PROMPT.md".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
    })?;
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
    let (representation, reason) = select_representation_by_inline_budget(
        prompt_md.len() as u64,
        inline_budget_bytes,
        prompt_backup_path,
    );
    Ok(MaterializedPromptInput {
        kind: PromptInputKind::Prompt,
        content_id_sha256: sha256_hex_str(&prompt_md),
        consumer_signature_sha256: consumer_signature_sha256.to_string(),
        original_bytes: prompt_md.len() as u64,
        final_bytes: prompt_md.len() as u64,
        model_budget_bytes: None,
        inline_budget_bytes: Some(inline_budget_bytes),
        representation,
        reason,
    })
}

fn materialize_development_plan_input(
    ctx: &PhaseContext<'_>,
    inline_budget_bytes: u64,
    consumer_signature_sha256: String,
) -> Result<MaterializedPromptInput, ErrorEvent> {
    let plan_path = Path::new(".agent/PLAN.md");
    let plan_md = ctx
        .workspace
        .read(plan_path)
        .map_err(|err| ErrorEvent::WorkspaceReadFailed {
            path: ".agent/PLAN.md".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        })?;
    if plan_md.len() as u64 > inline_budget_bytes {
        ctx.logger.warn(&format!(
            "PLAN size ({} KB) exceeds inline limit ({} KB). Referencing: {}",
            (plan_md.len() as u64) / 1024,
            inline_budget_bytes / 1024,
            plan_path.display()
        ));
    }
    let (representation, reason) = select_representation_by_inline_budget(
        plan_md.len() as u64,
        inline_budget_bytes,
        plan_path,
    );
    Ok(MaterializedPromptInput {
        kind: PromptInputKind::Plan,
        content_id_sha256: sha256_hex_str(&plan_md),
        consumer_signature_sha256,
        original_bytes: plan_md.len() as u64,
        final_bytes: plan_md.len() as u64,
        model_budget_bytes: None,
        inline_budget_bytes: Some(inline_budget_bytes),
        representation,
        reason,
    })
}

fn apply_oversize_prompt_events(
    result: EffectResult,
    prompt_input: &MaterializedPromptInput,
    inline_budget_bytes: u64,
) -> EffectResult {
    if prompt_input.original_bytes > inline_budget_bytes {
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
    }
}

fn apply_oversize_plan_events(
    result: EffectResult,
    plan_input: &MaterializedPromptInput,
    inline_budget_bytes: u64,
) -> EffectResult {
    if plan_input.original_bytes > inline_budget_bytes {
        let result = result.with_ui_event(UIEvent::AgentActivity {
            agent: "pipeline".to_string(),
            message: format!(
                "Oversize PLAN: {} KB > {} KB; using file reference",
                plan_input.original_bytes / 1024,
                inline_budget_bytes / 1024
            ),
        });
        result.with_additional_event(PipelineEvent::prompt_input_oversize_detected(
            crate::reducer::event::PipelinePhase::Development,
            PromptInputKind::Plan,
            plan_input.content_id_sha256.clone(),
            plan_input.original_bytes,
            inline_budget_bytes,
            "inline-embedding".to_string(),
        ))
    } else {
        result
    }
}

impl MainEffectHandler {
    pub(in crate::reducer::boundary) fn extract_development_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        let initial_event = UIEvent::IterationProgress {
            current: iteration,
            total: self.state.total_iterations,
        };
        let invalid_output_attempts = self.state.continuation.invalid_output_attempts;
        match ctx.workspace.read_artifact_json("development_result") {
            Ok(Some(envelope)) => {
                let display_content = serde_json::to_string_pretty(&envelope.content)
                    .unwrap_or_else(|_| "{}".to_string());
                build_development_xml_extracted_with_content(
                    iteration,
                    initial_event,
                    display_content,
                )
            }
            Ok(None) => EffectResult::with_ui(
                PipelineEvent::development_xml_missing(iteration, invalid_output_attempts),
                vec![initial_event],
            ),
            Err(_) => EffectResult::with_ui(
                PipelineEvent::development_xml_extracted(iteration),
                vec![initial_event],
            ),
        }
    }

    pub(in crate::reducer::boundary) fn validate_development_xml(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
    ) -> EffectResult {
        let is_continuation = self.state.continuation.is_continuation();
        let invalid_output_attempts = self.state.continuation.invalid_output_attempts;
        match ctx.workspace.read_artifact_json("development_result") {
            Ok(Some(envelope)) => validate_development_json_envelope(
                ctx,
                &envelope,
                iteration,
                is_continuation,
                invalid_output_attempts,
            ),
            Ok(None) | Err(_) => EffectResult::event(
                PipelineEvent::development_output_validation_failed(
                    iteration,
                    invalid_output_attempts,
                ),
            ),
        }
    }
}

fn build_development_xml_extracted_with_content(
    iteration: u32,
    initial_event: UIEvent,
    content: String,
) -> EffectResult {
    EffectResult::with_ui(
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
    )
}

fn apply_continuation_and_build_result(
    _ctx: &PhaseContext<'_>,
    elements: crate::files::result_types::DevelopmentResultElements,
    iteration: u32,
    is_continuation: bool,
    invalid_output_attempts: u32,
) -> EffectResult {
    let result = if is_continuation {
        crate::files::result_types::apply_continuation_development_result_contract(elements)
            .map_err(|_| ())
    } else {
        Ok(elements)
    };
    match result {
        Ok(elements) => {
            let status = derive_development_status(elements.is_completed(), elements.is_partial());
            let files_changed = parse_files_changed_lines(elements.files_changed.as_deref());
            EffectResult::event(PipelineEvent::development_xml_validated_with_decision(
                iteration,
                status,
                elements.analysis_decision,
                elements.summary.clone(),
                files_changed,
                elements.next_steps,
            ))
        }
        Err(()) => EffectResult::event(PipelineEvent::development_output_validation_failed(
            iteration,
            invalid_output_attempts,
        )),
    }
}

fn validate_development_json_envelope(
    ctx: &PhaseContext<'_>,
    envelope: &crate::workspace::ArtifactEnvelope,
    iteration: u32,
    is_continuation: bool,
    invalid_output_attempts: u32,
) -> EffectResult {
    match super::json_artifact::development_result_from_envelope(envelope) {
        Ok(elements) => apply_continuation_and_build_result(
            ctx,
            elements,
            iteration,
            is_continuation,
            invalid_output_attempts,
        ),
        Err(_err) => EffectResult::event(PipelineEvent::development_output_validation_failed(
            iteration,
            invalid_output_attempts,
        )),
    }
}

