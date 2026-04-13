use super::MainEffectHandler;
use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::phases::review::boundary_domain::{
    build_fix_continuation_prompt_content_id, build_fix_normal_prompt_content_id,
    build_fix_prompt_content_id, parse_development_result_status, render_fix_continuation_note,
};
use crate::phases::PhaseContext;
use crate::prompts::SessionCapabilities;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{AgentEvent, ErrorEvent, PipelineEvent, WorkspaceIoErrorKind};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::PromptMode;
use crate::reducer::ui_event::{UIEvent, XmlOutputContext, XmlOutputType};
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    pub(super) fn prepare_fix_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
        prompt_mode: PromptMode,
    ) -> Result<EffectResult> {
        ensure_tmp_dir(ctx)?;
        let inputs =
            read_fix_prompt_inputs(ctx, Self::sentinel_plan_content(ctx.config.isolation_mode))?;
        self.build_fix_prompt_result(ctx, pass, prompt_mode, inputs)
    }

    fn build_fix_prompt_result(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
        prompt_mode: PromptMode,
        inputs: FixPromptInputs,
    ) -> Result<EffectResult> {
        let gen = self.gen_fix_prompt_for_mode(ctx, pass, prompt_mode, &inputs);
        self.assemble_fix_prompt_result(ctx, pass, prompt_mode, inputs, gen)
    }

    fn gen_fix_prompt_for_mode(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
        prompt_mode: PromptMode,
        inputs: &FixPromptInputs,
    ) -> FixPromptGenerated {
        match prompt_mode {
            PromptMode::SameAgentRetry => self.gen_same_agent_retry_prompt(ctx, pass, inputs),
            PromptMode::Normal => self.gen_normal_fix_prompt(ctx, pass, inputs),
            PromptMode::Continuation => self.gen_continuation_fix_prompt(ctx, pass, inputs),
        }
    }

    fn gen_same_agent_retry_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
        inputs: &FixPromptInputs,
    ) -> FixPromptGenerated {
        use crate::prompts::{get_stored_or_generate_prompt, PromptScopeKey, RetryMode};
        let continuation_state = &self.state.continuation;
        let retry_preamble =
            crate::reducer::boundary::retry_guidance::same_agent_retry_preamble(continuation_state);
        let scope_key = PromptScopeKey::for_fix(
            pass,
            RetryMode::SameAgent {
                count: continuation_state.same_agent_retry_count,
            },
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let prompt_content_id = build_fix_prompt_content_id(
            &sha256_hex_str(&inputs.prompt_content),
            &sha256_hex_str(&inputs.plan_content),
            &sha256_hex_str(&inputs.issues_content),
            continuation_state.same_agent_retry_count,
        );
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || build_same_agent_retry_body(ctx, inputs, &retry_preamble),
        );
        let should_validate = !was_replayed;
        FixPromptGenerated {
            prompt_key,
            fix_prompt: prompt,
            was_replayed,
            template_name: "fix_mode",
            prompt_content_id: Some(prompt_content_id),
            should_validate,
        }
    }

    fn gen_normal_fix_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
        inputs: &FixPromptInputs,
    ) -> FixPromptGenerated {
        use crate::prompts::{get_stored_or_generate_prompt, PromptScopeKey, RetryMode};
        let scope_key = PromptScopeKey::for_fix(pass, RetryMode::Normal, self.state.recovery_epoch);
        let prompt_key = scope_key.to_string();
        let prompt_content_id = build_fix_normal_prompt_content_id(
            &sha256_hex_str(&inputs.prompt_content),
            &sha256_hex_str(&inputs.plan_content),
            &sha256_hex_str(&inputs.issues_content),
        );
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                crate::prompts::review::prompt_fix_xml_with_log(
                    ctx.template_context,
                    crate::prompts::review::FixPromptContent::new(
                        &inputs.prompt_content,
                        &inputs.plan_content,
                        &inputs.issues_content,
                    ),
                    &[],
                    ctx.workspace,
                    "fix_mode",
                    SessionCapabilities::new(
                        &CapabilitySet::defaults_for_drain(SessionDrain::Fix),
                        &PolicyFlagSet::defaults_for_drain(SessionDrain::Fix),
                    ),
                )
                .content
            },
        );
        FixPromptGenerated {
            prompt_key,
            fix_prompt: prompt,
            was_replayed,
            template_name: "fix_mode",
            prompt_content_id: Some(prompt_content_id),
            should_validate: true,
        }
    }

    fn gen_continuation_fix_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
        inputs: &FixPromptInputs,
    ) -> FixPromptGenerated {
        use crate::prompts::{
            get_stored_or_generate_prompt, prompt_fix_xml_with_context, PromptScopeKey, RetryMode,
        };
        let continuation_state = &self.state.continuation;
        let scope_key = PromptScopeKey::for_fix(pass, RetryMode::Normal, self.state.recovery_epoch);
        let prompt_key = scope_key.to_string();
        let (continuation_note, prompt_content_id) =
            build_continuation_fix_context(continuation_state, inputs);
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Fix);
                let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Fix);
                format!(
                    "{continuation_note}\n{}",
                    prompt_fix_xml_with_context(
                        ctx.template_context,
                        &inputs.prompt_content,
                        &inputs.plan_content,
                        &inputs.issues_content,
                        &[],
                        ctx.workspace,
                        SessionCapabilities::new(&capabilities, &policy_flags),
                    )
                )
            },
        );
        FixPromptGenerated {
            prompt_key,
            fix_prompt: prompt,
            was_replayed,
            template_name: "fix_mode",
            prompt_content_id: Some(prompt_content_id),
            should_validate: true,
        }
    }

    fn render_fix_validation_log(
        &self,
        ctx: &PhaseContext<'_>,
        prompt_mode: PromptMode,
        inputs: &FixPromptInputs,
        gen: &FixPromptGenerated,
    ) -> std::result::Result<crate::prompts::SubstitutionLog, Box<EffectResult>> {
        let continuation_state = &self.state.continuation;
        let rendered =
            render_fix_template_for_validation(ctx, prompt_mode, inputs, gen, continuation_state);
        if rendered.log.is_complete() {
            return Ok(rendered.log);
        }
        Err(Box::new(build_incomplete_template_result(
            rendered.log,
            gen,
        )))
    }

    fn assemble_fix_prompt_result(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
        prompt_mode: PromptMode,
        inputs: FixPromptInputs,
        gen: FixPromptGenerated,
    ) -> Result<EffectResult> {
        let rendered_log = match self.maybe_validate_fix_template(ctx, prompt_mode, &inputs, &gen) {
            Ok(log) => log,
            Err(early) => return Ok(*early),
        };
        write_fix_prompt_file(ctx, &gen.fix_prompt);
        Ok(build_fix_prompt_effect_result(pass, gen, rendered_log))
    }

    fn maybe_validate_fix_template(
        &self,
        ctx: &PhaseContext<'_>,
        prompt_mode: PromptMode,
        inputs: &FixPromptInputs,
        gen: &FixPromptGenerated,
    ) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
        if !gen.should_validate || gen.was_replayed {
            return Ok(None);
        }
        match self.render_fix_validation_log(ctx, prompt_mode, inputs, gen) {
            Ok(log) => Ok(Some(log)),
            Err(incomplete) => Err(incomplete),
        }
    }

    pub(super) fn invoke_fix_agent(
        &mut self,
        ctx: &mut PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        use crate::agents::AgentRole;

        self.normalize_agent_chain_for_invocation(ctx, crate::agents::AgentDrain::Fix);

        let prompt = match ctx.workspace.read(Path::new(".agent/tmp/fix_prompt.txt")) {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                return Err(ErrorEvent::FixPromptMissing.into());
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: ".agent/tmp/fix_prompt.txt".to_string(),
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
            .unwrap_or_else(|| ctx.reviewer_agent.to_string());

        // RFC-009: The closure receives the AgentSession created by invoke_agent.
        // In V1, session capabilities == drain defaults, so the pre-generated prompt
        // is correct. The closure still calls capability_template_variables_from_session
        // to verify the V1 invariant holds and to exercise the RFC-009 session-aware path.
        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Fix,
            AgentRole::Fix,
            &agent,
            None,
            |session: &crate::agents::session::AgentSession| {
                let _session_vars =
                    crate::prompts::capability_template_variables_from_session(session);
                prompt.clone()
            },
        )?;
        Ok(maybe_append_fix_invoked_event(result, pass))
    }

    pub(super) fn extract_fix_result_xml(&self, ctx: &PhaseContext<'_>, pass: u32) -> EffectResult {
        let is_analysis = self.state.fix_analysis_agent_invoked_pass == Some(pass);
        let invalid_attempts = self.state.continuation.invalid_output_attempts;
        if fix_json_artifact_present(ctx, is_analysis) {
            EffectResult::event(PipelineEvent::fix_result_xml_extracted(pass))
        } else {
            EffectResult::event(PipelineEvent::fix_result_xml_missing(
                pass,
                invalid_attempts,
                None,
            ))
        }
    }

    fn fix_analysis_continuation_active(&self, is_analysis: bool) -> bool {
        is_analysis
            && (self.state.continuation.fix_continuation_attempt > 0
                || self.state.continuation.fix_continue_pending)
    }

    pub(super) fn validate_fix_result_xml(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        let is_analysis = self.state.fix_analysis_agent_invoked_pass == Some(pass);
        let fix_analysis_continuation = self.fix_analysis_continuation_active(is_analysis);
        let invalid_attempts = self.state.continuation.invalid_output_attempts;
        let json_type = fix_json_artifact_type(is_analysis);
        let identity = ArtifactIdentity {
            current_drain: self.state.agent_chain.current_drain,
            run_id: &ctx.run_context.run_id,
            logger: ctx.logger,
        };
        match try_validate_fix_from_json(
            ctx,
            pass,
            is_analysis,
            fix_analysis_continuation,
            invalid_attempts,
            json_type,
            identity,
        ) {
            Some(result) => result,
            None => EffectResult::event(PipelineEvent::fix_output_validation_failed(
                pass,
                invalid_attempts,
                Some(format!(
                    "No JSON artifact found for '{json_type}'. Agent must output a valid JSON artifact."
                )),
            )),
        }
    }

    pub(super) fn apply_fix_outcome(
        &self,
        _ctx: &mut PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        self.state
            .fix_validated_outcome
            .as_ref()
            .filter(|o| o.pass == pass)
            .ok_or(ErrorEvent::ValidatedFixOutcomeMissing { pass })?;

        Ok(EffectResult::event(PipelineEvent::fix_outcome_applied(
            pass,
        )))
    }

    pub(super) fn archive_fix_result_xml(&self, ctx: &PhaseContext<'_>, pass: u32) -> EffectResult {
        crate::files::archive_json_artifact_with_workspace(ctx.workspace, "fix_result");

        if self.state.fix_analysis_agent_invoked_pass == Some(pass) {
            crate::files::archive_json_artifact_with_workspace(ctx.workspace, "development_result");
        }

        EffectResult::event(PipelineEvent::fix_result_xml_archived(pass))
    }
}

// ---------------------------------------------------------------------------
// Free helpers
// ---------------------------------------------------------------------------

struct FixPromptInputs {
    prompt_content: String,
    plan_content: String,
    issues_content: String,
}

struct FixPromptGenerated {
    prompt_key: String,
    fix_prompt: String,
    was_replayed: bool,
    template_name: &'static str,
    prompt_content_id: Option<String>,
    should_validate: bool,
}

fn build_continuation_fix_context(
    continuation_state: &crate::reducer::state::ContinuationState,
    inputs: &FixPromptInputs,
) -> (String, String) {
    let status = continuation_state
        .fix_status
        .unwrap_or(crate::reducer::state::FixStatus::IssuesRemain);
    let summary = continuation_state
        .fix_previous_summary
        .clone()
        .unwrap_or_else(|| "Continue addressing the remaining review issues.".to_string());
    let continuation_note = render_fix_continuation_note(
        continuation_state.fix_continuation_attempt,
        continuation_state.max_fix_continue_count,
        &status.to_string(),
        &summary,
    );
    let prompt_content_id = build_fix_continuation_prompt_content_id(
        continuation_state.fix_continuation_attempt,
        &status.to_string(),
        &summary,
        &sha256_hex_str(&inputs.prompt_content),
        &sha256_hex_str(&inputs.plan_content),
        &sha256_hex_str(&inputs.issues_content),
    );
    (continuation_note, prompt_content_id)
}

fn build_same_agent_retry_body(
    ctx: &PhaseContext<'_>,
    inputs: &FixPromptInputs,
    retry_preamble: &str,
) -> String {
    use crate::prompts::prompt_fix_xml_with_context;
    let fresh = || {
        let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Fix);
        let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Fix);
        prompt_fix_xml_with_context(
            ctx.template_context,
            &inputs.prompt_content,
            &inputs.plan_content,
            &inputs.issues_content,
            &[],
            ctx.workspace,
            SessionCapabilities::new(&capabilities, &policy_flags),
        )
    };
    let base_prompt = match ctx.workspace.read(Path::new(".agent/tmp/fix_prompt.txt")) {
        Ok(previous_prompt) => select_same_agent_base_prompt(previous_prompt, fresh()),
        Err(_) => fresh(),
    };
    format!("{retry_preamble}\n{base_prompt}")
}

fn select_same_agent_base_prompt(previous_prompt: String, freshly_rendered: String) -> String {
    let previous_base =
        crate::reducer::boundary::retry_guidance::strip_existing_same_agent_retry_preamble(
            &previous_prompt,
        )
        .to_string();
    if previous_base == freshly_rendered {
        previous_base
    } else {
        freshly_rendered
    }
}

fn write_fix_prompt_file(ctx: &PhaseContext<'_>, fix_prompt: &str) {
    if let Err(err) = ctx
        .workspace
        .write(Path::new(".agent/tmp/fix_prompt.txt"), fix_prompt)
    {
        ctx.logger.warn(&format!(
            "Failed to write fix prompt file: {err}. Pipeline will continue."
        ));
    }
}

fn build_fix_prompt_effect_result(
    pass: u32,
    gen: FixPromptGenerated,
    rendered_log: Option<crate::prompts::SubstitutionLog>,
) -> EffectResult {
    let prompt_captured_event = (!gen.was_replayed).then(|| {
        crate::reducer::event::PipelineEvent::PromptInput(
            crate::reducer::event::PromptInputEvent::PromptCaptured {
                key: gen.prompt_key.clone(),
                content: gen.fix_prompt.clone(),
                content_id: gen.prompt_content_id.clone(),
            },
        )
    });
    let result = EffectResult::event(PipelineEvent::fix_prompt_prepared(pass)).with_ui_event(
        UIEvent::PromptReplayHit {
            key: gen.prompt_key,
            was_replayed: gen.was_replayed,
        },
    );
    let result = prompt_captured_event.map_or(result.clone(), |e| result.with_additional_event(e));
    rendered_log.map_or(result.clone(), |log| {
        result.with_additional_event(PipelineEvent::template_rendered(
            crate::reducer::event::PipelinePhase::Review,
            gen.template_name.to_string(),
            log,
        ))
    })
}

fn render_fix_template_for_validation(
    ctx: &PhaseContext<'_>,
    prompt_mode: PromptMode,
    inputs: &FixPromptInputs,
    gen: &FixPromptGenerated,
    continuation_state: &crate::reducer::state::ContinuationState,
) -> crate::prompts::RenderedTemplate {
    match prompt_mode {
        PromptMode::Continuation => {
            render_continuation_fix_log(ctx, inputs, gen, continuation_state)
        }
        _ => crate::prompts::review::prompt_fix_xml_with_log(
            ctx.template_context,
            crate::prompts::review::FixPromptContent::new(
                &inputs.prompt_content,
                &inputs.plan_content,
                &inputs.issues_content,
            ),
            &[],
            ctx.workspace,
            gen.template_name,
            SessionCapabilities::new(
                &CapabilitySet::defaults_for_drain(SessionDrain::Fix),
                &PolicyFlagSet::defaults_for_drain(SessionDrain::Fix),
            ),
        ),
    }
}

fn build_incomplete_template_result(
    log: crate::prompts::SubstitutionLog,
    gen: &FixPromptGenerated,
) -> EffectResult {
    use crate::agents::AgentRole;
    let missing = log.unsubstituted.clone();
    EffectResult::event(PipelineEvent::template_rendered(
        crate::reducer::event::PipelinePhase::Review,
        gen.template_name.to_string(),
        log,
    ))
    .with_ui_event(UIEvent::PromptReplayHit {
        key: gen.prompt_key.clone(),
        was_replayed: gen.was_replayed,
    })
    .with_additional_event(PipelineEvent::agent_template_variables_invalid(
        AgentRole::Fix,
        gen.template_name.to_string(),
        missing,
        Vec::new(),
    ))
}

fn render_continuation_fix_log(
    ctx: &PhaseContext<'_>,
    inputs: &FixPromptInputs,
    gen: &FixPromptGenerated,
    continuation_state: &crate::reducer::state::ContinuationState,
) -> crate::prompts::RenderedTemplate {
    let status = continuation_state
        .fix_status
        .unwrap_or(crate::reducer::state::FixStatus::IssuesRemain);
    let summary = continuation_state
        .fix_previous_summary
        .clone()
        .unwrap_or_else(|| "Continue addressing the remaining review issues.".to_string());
    let continuation_note = render_fix_continuation_note(
        continuation_state.fix_continuation_attempt,
        continuation_state.max_fix_continue_count,
        &status.to_string(),
        &summary,
    );
    let rendered = crate::prompts::review::prompt_fix_xml_with_log(
        ctx.template_context,
        crate::prompts::review::FixPromptContent::new(
            &inputs.prompt_content,
            &inputs.plan_content,
            &inputs.issues_content,
        ),
        &[],
        ctx.workspace,
        gen.template_name,
        SessionCapabilities::new(
            &CapabilitySet::defaults_for_drain(SessionDrain::Fix),
            &PolicyFlagSet::defaults_for_drain(SessionDrain::Fix),
        ),
    );
    crate::prompts::RenderedTemplate {
        content: format!("{continuation_note}\n{}", rendered.content),
        log: rendered.log,
    }
}

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

fn read_fix_prompt_inputs(
    ctx: &PhaseContext<'_>,
    sentinel_plan: String,
) -> Result<FixPromptInputs> {
    Ok(FixPromptInputs {
        prompt_content: read_prompt_backup(ctx)?,
        plan_content: read_plan_content(ctx, sentinel_plan)?,
        issues_content: read_issues_content(ctx)?,
    })
}

fn read_prompt_backup(ctx: &PhaseContext<'_>) -> Result<String> {
    match ctx.workspace.read(Path::new(".agent/PROMPT.md.backup")) {
        Ok(s) => Ok(s),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            ctx.logger
                .warn("Missing .agent/PROMPT.md.backup; embedding sentinel in fix prompt input");
            Ok("[MISSING INPUT: .agent/PROMPT.md.backup]\n\nNo PROMPT backup was found. Continuing without original request context.\n".to_string())
        }
        Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
            path: ".agent/PROMPT.md.backup".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()),
    }
}

fn read_plan_content(ctx: &PhaseContext<'_>, sentinel_plan: String) -> Result<String> {
    match ctx.workspace.read(Path::new(".agent/PLAN.md")) {
        Ok(s) => Ok(s),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(sentinel_plan),
        Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
            path: ".agent/PLAN.md".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()),
    }
}

fn read_issues_content(ctx: &PhaseContext<'_>) -> Result<String> {
    match ctx.workspace.read(Path::new(".agent/ISSUES.md")) {
        Ok(s) => Ok(s),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            ctx.logger
                .warn("Missing .agent/ISSUES.md; embedding sentinel in fix prompt input");
            Ok("[MISSING INPUT: .agent/ISSUES.md]\n\nNo ISSUES.md was found. This may indicate a cleaned workspace or a skipped review pass.\n".to_string())
        }
        Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
            path: ".agent/ISSUES.md".to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()),
    }
}

include!("run_fix_validate.rs");
