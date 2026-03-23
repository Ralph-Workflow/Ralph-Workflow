use super::MainEffectHandler;
use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
use crate::phases::review::boundary_domain::{
    build_fix_continuation_prompt_content_id, build_fix_normal_prompt_content_id,
    build_fix_prompt_content_id, build_fix_xsd_retry_prompt_content_id,
    parse_development_result_status, render_fix_continuation_note,
};
use crate::phases::PhaseContext;
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
        let inputs = read_fix_prompt_inputs(
            ctx,
            prompt_mode,
            Self::sentinel_plan_content(ctx.config.isolation_mode),
        )?;
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
            PromptMode::XsdRetry => self.gen_xsd_retry_prompt(ctx, pass, inputs),
            PromptMode::SameAgentRetry => self.gen_same_agent_retry_prompt(ctx, pass, inputs),
            PromptMode::Normal => self.gen_normal_fix_prompt(ctx, pass, inputs),
            PromptMode::Continuation => self.gen_continuation_fix_prompt(ctx, pass, inputs),
        }
    }

    fn gen_xsd_retry_prompt(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
        inputs: &FixPromptInputs,
    ) -> FixPromptGenerated {
        use crate::prompts::{
            get_stored_or_generate_prompt, prompt_fix_xsd_retry_with_context, PromptScopeKey,
            RetryMode,
        };
        let continuation_state = &self.state.continuation;
        let scope_key = PromptScopeKey::for_fix(
            pass,
            RetryMode::Xsd {
                count: continuation_state.invalid_output_attempts,
            },
            self.state.recovery_epoch,
        );
        let prompt_key = scope_key.to_string();
        let xsd_error = continuation_state
            .last_fix_xsd_error
            .as_deref()
            .filter(|s| !s.trim().is_empty())
            .unwrap_or("XML output failed validation. Provide valid XML output.");
        let prompt_content_id = build_fix_xsd_retry_prompt_content_id(
            &sha256_hex_str(&inputs.prompt_content),
            &sha256_hex_str(&inputs.plan_content),
            &sha256_hex_str(&inputs.issues_content),
            xsd_error,
            &sha256_hex_str(&inputs.last_output),
        );
        let (prompt, was_replayed) = get_stored_or_generate_prompt(
            &scope_key,
            &self.state.prompt_history,
            Some(&prompt_content_id),
            || {
                prompt_fix_xsd_retry_with_context(
                    ctx.template_context,
                    &inputs.issues_content,
                    xsd_error,
                    &inputs.last_output,
                    ctx.workspace,
                )
            },
        );
        FixPromptGenerated {
            prompt_key,
            fix_prompt: prompt,
            was_replayed,
            template_name: "fix_mode_xsd_retry",
            prompt_content_id: Some(prompt_content_id),
            should_validate: true,
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
            template_name: "fix_mode_xml",
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
                    &inputs.prompt_content,
                    &inputs.plan_content,
                    &inputs.issues_content,
                    &[],
                    ctx.workspace,
                    "fix_mode_xml",
                )
                .content
            },
        );
        FixPromptGenerated {
            prompt_key,
            fix_prompt: prompt,
            was_replayed,
            template_name: "fix_mode_xml",
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
                format!(
                    "{continuation_note}\n{}",
                    prompt_fix_xml_with_context(
                        ctx.template_context,
                        &inputs.prompt_content,
                        &inputs.plan_content,
                        &inputs.issues_content,
                        &[],
                        ctx.workspace
                    )
                )
            },
        );
        FixPromptGenerated {
            prompt_key,
            fix_prompt: prompt,
            was_replayed,
            template_name: "fix_mode_xml",
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

        let result = self.invoke_agent(
            ctx,
            crate::agents::AgentDrain::Fix,
            AgentRole::Reviewer,
            &agent,
            None,
            prompt,
        )?;
        Ok(maybe_append_fix_invoked_event(result, pass))
    }

    pub(super) fn extract_fix_result_xml(&self, ctx: &PhaseContext<'_>, pass: u32) -> EffectResult {
        let is_analysis = self.state.fix_analysis_agent_invoked_pass == Some(pass);
        match read_xml_for_pass(ctx, is_analysis) {
            Ok(_) => EffectResult::event(PipelineEvent::fix_result_xml_extracted(pass)),
            Err(err) => EffectResult::event(PipelineEvent::fix_result_xml_missing(
                pass,
                self.state.continuation.invalid_output_attempts,
                xml_io_error_detail(&err),
            )),
        }
    }

    pub(super) fn validate_fix_result_xml(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        let is_analysis = self.state.fix_analysis_agent_invoked_pass == Some(pass);
        let invalid_attempts = self.state.continuation.invalid_output_attempts;
        let xml_content = match read_xml_for_pass(ctx, is_analysis) {
            Ok(s) => s,
            Err(err) => {
                return EffectResult::event(PipelineEvent::fix_output_validation_failed(
                    pass,
                    invalid_attempts,
                    xml_io_error_detail(&err),
                ))
            }
        };
        if is_analysis {
            validate_fix_analysis_xml(pass, xml_content, invalid_attempts)
        } else {
            validate_fix_normal_xml(pass, xml_content, invalid_attempts)
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
        use crate::files::llm_output_extraction::archive_xml_file_with_workspace;

        archive_xml_file_with_workspace(ctx.workspace, Path::new(xml_paths::FIX_RESULT_XML));

        if self.state.fix_analysis_agent_invoked_pass == Some(pass) {
            archive_xml_file_with_workspace(
                ctx.workspace,
                Path::new(".agent/tmp/development_result.xml"),
            );
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
    last_output: String,
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
        prompt_fix_xml_with_context(
            ctx.template_context,
            &inputs.prompt_content,
            &inputs.plan_content,
            &inputs.issues_content,
            &[],
            ctx.workspace,
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
        PromptMode::XsdRetry => render_xsd_retry_fix_log(ctx, inputs, gen, continuation_state),
        PromptMode::Continuation => {
            render_continuation_fix_log(ctx, inputs, gen, continuation_state)
        }
        _ => crate::prompts::review::prompt_fix_xml_with_log(
            ctx.template_context,
            &inputs.prompt_content,
            &inputs.plan_content,
            &inputs.issues_content,
            &[],
            ctx.workspace,
            gen.template_name,
        ),
    }
}

fn render_xsd_retry_fix_log(
    ctx: &PhaseContext<'_>,
    inputs: &FixPromptInputs,
    gen: &FixPromptGenerated,
    continuation_state: &crate::reducer::state::ContinuationState,
) -> crate::prompts::RenderedTemplate {
    let xsd_error = continuation_state
        .last_fix_xsd_error
        .as_deref()
        .filter(|s| !s.trim().is_empty())
        .unwrap_or("XML output failed validation. Provide valid XML output.");
    crate::prompts::review::prompt_fix_xsd_retry_with_log(
        ctx.template_context,
        xsd_error,
        &inputs.last_output,
        ctx.workspace,
        gen.template_name,
    )
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
        AgentRole::Reviewer,
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
        &inputs.prompt_content,
        &inputs.plan_content,
        &inputs.issues_content,
        &[],
        ctx.workspace,
        gen.template_name,
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
    prompt_mode: PromptMode,
    sentinel_plan: String,
) -> Result<FixPromptInputs> {
    Ok(FixPromptInputs {
        prompt_content: read_prompt_backup(ctx)?,
        plan_content: read_plan_content(ctx, sentinel_plan)?,
        issues_content: read_issues_content(ctx)?,
        last_output: read_last_output_for_mode(ctx, prompt_mode)?,
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

fn read_last_output_for_mode(ctx: &PhaseContext<'_>, prompt_mode: PromptMode) -> Result<String> {
    if !matches!(prompt_mode, PromptMode::XsdRetry) {
        return Ok(String::new());
    }
    read_xsd_retry_last_output(ctx)
}

fn read_xsd_retry_last_output(ctx: &PhaseContext<'_>) -> Result<String> {
    match ctx.workspace.read(Path::new(xml_paths::FIX_RESULT_XML)) {
        Ok(s) => Ok(s),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(read_processed_fallback(ctx)),
        Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
            path: xml_paths::FIX_RESULT_XML.to_string(),
            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
        }
        .into()),
    }
}

fn read_processed_fallback(ctx: &PhaseContext<'_>) -> String {
    ctx.workspace
        .read(Path::new(".agent/tmp/fix_result.xml.processed"))
        .map_or_else(
            |_| String::new(),
            |output| {
                ctx.logger
                    .info("XSD retry: using archived .processed file as last output");
                output
            },
        )
}

fn read_xml_for_pass(ctx: &PhaseContext<'_>, is_analysis: bool) -> std::io::Result<String> {
    let xml_path = if is_analysis {
        Path::new(".agent/tmp/development_result.xml")
    } else {
        Path::new(xml_paths::FIX_RESULT_XML)
    };
    ctx.workspace.read(xml_path)
}

fn xml_io_error_detail(err: &std::io::Error) -> Option<String> {
    if err.kind() == std::io::ErrorKind::NotFound {
        None
    } else {
        Some(format!(
            "{:?}: {}",
            WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            err
        ))
    }
}

fn maybe_append_fix_invoked_event(
    result: crate::reducer::effect::EffectResult,
    pass: u32,
) -> crate::reducer::effect::EffectResult {
    let succeeded = result.additional_events.iter().any(|e| {
        matches!(
            e,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        )
    });
    if succeeded {
        result.with_additional_event(PipelineEvent::fix_agent_invoked(pass))
    } else {
        result
    }
}

fn validate_fix_analysis_xml(
    pass: u32,
    xml_content: String,
    invalid_attempts: u32,
) -> crate::reducer::effect::EffectResult {
    use crate::files::llm_output_extraction::validate_development_result_xml;
    match validate_development_result_xml(&xml_content) {
        Ok(elements) => {
            let status = parse_development_result_status(&elements.status);
            crate::reducer::effect::EffectResult::with_ui(
                PipelineEvent::fix_result_xml_validated(pass, status, Some(elements.summary)),
                vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::DevelopmentResult,
                    content: xml_content,
                    context: Some(XmlOutputContext {
                        iteration: None,
                        pass: Some(pass),
                        snippets: Vec::new(),
                    }),
                }],
            )
        }
        Err(err) => crate::reducer::effect::EffectResult::event(
            PipelineEvent::fix_output_validation_failed(
                pass,
                invalid_attempts,
                Some(err.format_for_ai_retry()),
            ),
        ),
    }
}

fn validate_fix_normal_xml(
    pass: u32,
    xml_content: String,
    invalid_attempts: u32,
) -> crate::reducer::effect::EffectResult {
    use crate::files::llm_output_extraction::validate_fix_result_xml;
    match validate_fix_result_xml(&xml_content) {
        Ok(elements) => {
            let status = crate::reducer::state::FixStatus::parse(&elements.status)
                .unwrap_or(crate::reducer::state::FixStatus::Failed);
            crate::reducer::effect::EffectResult::with_ui(
                PipelineEvent::fix_result_xml_validated(pass, status, elements.summary),
                vec![UIEvent::XmlOutput {
                    xml_type: XmlOutputType::FixResult,
                    content: xml_content,
                    context: Some(XmlOutputContext {
                        iteration: None,
                        pass: Some(pass),
                        snippets: Vec::new(),
                    }),
                }],
            )
        }
        Err(err) => crate::reducer::effect::EffectResult::event(
            PipelineEvent::fix_output_validation_failed(
                pass,
                invalid_attempts,
                Some(err.format_for_ai_retry()),
            ),
        ),
    }
}
