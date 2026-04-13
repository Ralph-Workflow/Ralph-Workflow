use super::MainEffectHandler;
use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::agents::AgentRole;
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
    prompt_developer_iteration_xml_with_references_and_log, PromptScopeKey, RetryMode,
    SessionCapabilities,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::{MaterializedPromptInput, PromptInputRepresentation, PromptMode};
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
        let mode_result = self.execute_development_prompt_mode(ctx, iteration, execution_path)?;
        finalize_development_prompt(ctx, iteration, mode_result)
    }

    fn execute_development_prompt_mode(
        &self,
        ctx: &PhaseContext<'_>,
        iteration: u32,
        execution_path: DevelopmentPromptExecutionPath,
    ) -> Result<PromptModeResult> {
        match execution_path {
            DevelopmentPromptExecutionPath::Continuation => {
                Ok(self.prompt_mode_continuation(ctx, iteration))
            }
            DevelopmentPromptExecutionPath::SameAgentRetry => {
                self.prompt_mode_same_agent_retry(ctx, iteration)
            }
            DevelopmentPromptExecutionPath::Normal => self.prompt_mode_normal(ctx, iteration),
        }
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
                    SessionCapabilities::new(
                        &CapabilitySet::defaults_for_drain(SessionDrain::Development),
                        &PolicyFlagSet::defaults_for_drain(SessionDrain::Development),
                    ),
                )
            },
        );
        let rendered_log_result =
            build_continuation_rendered_log(ctx, continuation_state, &prompt_key, was_replayed);
        let rendered_log = match rendered_log_result {
            Ok(log) => log,
            Err(early) => return PromptModeResult::EarlyReturn(*early),
        };
        PromptModeResult::Data(PromptModeData {
            prompt,
            template_name: "developer_iteration_continuation",
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
            rendered_log,
            additional_events: Vec::new(),
        })
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

        let prompt_ref = build_prompt_content_ref(ctx, &inputs.prompt)?;
        let plan_ref = build_plan_content_ref(ctx, &inputs.plan)?;
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
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                let base_prompt = build_same_agent_base_prompt(ctx, &refs);
                format!("{retry_preamble}\n{base_prompt}")
            },
        );

        Ok(PromptModeResult::Data(PromptModeData {
            prompt,
            template_name: "developer_iteration",
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
            rendered_log: None,
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

        let prompt_ref = build_prompt_content_ref(ctx, &inputs.prompt)?;
        let plan_ref = build_plan_content_ref(ctx, &inputs.plan)?;

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
        let refs = PromptContentReferences {
            prompt: Some(prompt_ref.clone()),
            plan: Some(plan_ref.clone()),
            diff: None,
        };
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                prompt_developer_iteration_xml_with_references_and_log(
                    ctx.template_context,
                    &refs,
                    ctx.workspace,
                    "developer_iteration",
                    SessionCapabilities::new(
                        &CapabilitySet::defaults_for_drain(SessionDrain::Development),
                        &PolicyFlagSet::defaults_for_drain(SessionDrain::Development),
                    ),
                )
                .content
            },
        );

        let rendered_log = match build_normal_rendered_log(ctx, &refs, &prompt_key, was_replayed) {
            Ok(log) => log,
            Err(early) => return Ok(PromptModeResult::EarlyReturn(*early)),
        };

        Ok(PromptModeResult::Data(PromptModeData {
            prompt,
            template_name: "developer_iteration",
            prompt_key: Some(prompt_key),
            was_replayed,
            prompt_content_id: Some(prompt_content_id),
            rendered_log,
            additional_events: Vec::new(),
        }))
    }
}

fn build_normal_rendered_log(
    ctx: &PhaseContext<'_>,
    refs: &PromptContentReferences,
    prompt_key: &str,
    was_replayed: bool,
) -> Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    if was_replayed {
        return Ok(None);
    }
    let rendered = prompt_developer_iteration_xml_with_references_and_log(
        ctx.template_context,
        refs,
        ctx.workspace,
        "developer_iteration",
        SessionCapabilities::new(
            &CapabilitySet::defaults_for_drain(SessionDrain::Development),
            &PolicyFlagSet::defaults_for_drain(SessionDrain::Development),
        ),
    );
    check_template_log_complete(
        rendered.log,
        AgentRole::Developer,
        "developer_iteration",
        prompt_key,
        was_replayed,
    )
}

fn build_continuation_rendered_log(
    ctx: &PhaseContext<'_>,
    continuation_state: &crate::reducer::state::ContinuationState,
    prompt_key: &str,
    was_replayed: bool,
) -> Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    if was_replayed {
        return Ok(None);
    }
    let rendered = prompt_developer_iteration_continuation_xml_with_log(
        ctx.template_context,
        continuation_state,
        ctx.workspace,
        "developer_iteration_continuation",
        SessionCapabilities::new(
            &CapabilitySet::defaults_for_drain(SessionDrain::Development),
            &PolicyFlagSet::defaults_for_drain(SessionDrain::Development),
        ),
    );
    check_template_log_complete(
        rendered.log,
        AgentRole::Developer,
        "developer_iteration_continuation",
        prompt_key,
        was_replayed,
    )
}

fn finalize_development_prompt(
    ctx: &PhaseContext<'_>,
    iteration: u32,
    mode_result: PromptModeResult,
) -> Result<EffectResult> {
    let data = match mode_result {
        PromptModeResult::EarlyReturn(result) => return Ok(result),
        PromptModeResult::Data(data) => data,
    };
    write_development_prompt_to_workspace(ctx, &data.prompt)?;
    Ok(assemble_development_prompt_result(iteration, data))
}

fn write_development_prompt_to_workspace(ctx: &PhaseContext<'_>, dev_prompt: &str) -> Result<()> {
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
        .write(Path::new(".agent/tmp/development_prompt.txt"), dev_prompt)
    {
        ctx.logger.warn(&format!(
            "Failed to write development prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
        ));
    }
    Ok(())
}

fn assemble_development_prompt_result(iteration: u32, data: PromptModeData) -> EffectResult {
    let PromptModeData {
        prompt: dev_prompt,
        template_name,
        prompt_key,
        was_replayed,
        prompt_content_id,
        rendered_log,
        additional_events,
    } = data;
    let replay_key = prompt_key.as_deref().map(|k| (k.to_string(), was_replayed));
    let prompt_captured_event = prompt_key.as_deref().and_then(|k| {
        (!was_replayed).then(|| {
            crate::reducer::event::PipelineEvent::PromptInput(
                crate::reducer::event::PromptInputEvent::PromptCaptured {
                    key: k.to_string(),
                    content: dev_prompt.clone(),
                    content_id: prompt_content_id.clone(),
                },
            )
        })
    });
    let result = EffectResult::event(PipelineEvent::development_prompt_prepared(iteration));
    let result = replay_key.map_or(result.clone(), |(key, replayed)| {
        result.with_ui_event(UIEvent::PromptReplayHit {
            key,
            was_replayed: replayed,
        })
    });
    let result =
        prompt_captured_event.map_or(result.clone(), |ev| result.with_additional_event(ev));
    let result = additional_events
        .into_iter()
        .fold(result, |r, ev| r.with_additional_event(ev));
    rendered_log.map_or(result.clone(), |log| {
        result.with_additional_event(PipelineEvent::template_rendered(
            crate::reducer::event::PipelinePhase::Development,
            template_name.to_string(),
            log,
        ))
    })
}

fn check_template_log_complete(
    log: crate::prompts::SubstitutionLog,
    role: AgentRole,
    template_name: &str,
    prompt_key: &str,
    was_replayed: bool,
) -> Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    if let Some(early) =
        template_incomplete_early_return(&log, role, template_name, prompt_key, was_replayed)
    {
        return Err(Box::new(early));
    }
    Ok(Some(log))
}

fn template_incomplete_early_return(
    log: &crate::prompts::SubstitutionLog,
    role: AgentRole,
    template_name: &str,
    prompt_key: &str,
    was_replayed: bool,
) -> Option<EffectResult> {
    if log.is_complete() {
        return None;
    }
    let missing = log.unsubstituted.clone();
    let result = EffectResult::event(PipelineEvent::template_rendered(
        crate::reducer::event::PipelinePhase::Development,
        template_name.to_string(),
        log.clone(),
    ))
    .with_additional_event(PipelineEvent::agent_template_variables_invalid(
        role,
        template_name.to_string(),
        missing,
        Vec::new(),
    ));
    Some(result.with_ui_event(UIEvent::PromptReplayHit {
        key: prompt_key.to_string(),
        was_replayed,
    }))
}

fn build_prompt_content_ref(
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

fn build_plan_content_ref(
    ctx: &PhaseContext<'_>,
    input: &MaterializedPromptInput,
) -> Result<PlanContentReference> {
    match &input.representation {
        PromptInputRepresentation::Inline => {
            let plan_md = ctx
                .workspace
                .read(Path::new(".agent/PLAN.md"))
                .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/PLAN.md".to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                })?;
            Ok(PlanContentReference::Inline(plan_md))
        }
        PromptInputRepresentation::FileReference { path } => {
            Ok(PlanContentReference::ReadFromFile {
                primary_path: path.clone(),
                fallback_path: Some(Path::new(".agent/tmp/plan.xml").to_path_buf()),
                description: format!(
                    "Plan is {} bytes (exceeds {} limit)",
                    input.final_bytes, MAX_INLINE_CONTENT_SIZE
                ),
            })
        }
    }
}

fn build_same_agent_base_prompt(ctx: &PhaseContext<'_>, refs: &PromptContentReferences) -> String {
    ctx.workspace
        .read(Path::new(".agent/tmp/development_prompt.txt"))
        .map_or_else(
            |_| {
                prompt_developer_iteration_xml_with_references(
                    ctx.template_context,
                    refs,
                    ctx.workspace,
                    SessionCapabilities::new(
                        &CapabilitySet::defaults_for_drain(SessionDrain::Development),
                        &PolicyFlagSet::defaults_for_drain(SessionDrain::Development),
                    ),
                )
            },
            |previous_prompt| {
                super::retry_guidance::strip_existing_same_agent_retry_preamble(&previous_prompt)
                    .to_string()
            },
        )
}
