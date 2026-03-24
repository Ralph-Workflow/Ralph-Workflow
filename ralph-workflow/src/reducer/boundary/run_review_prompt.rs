use super::MainEffectHandler;
use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::phases::review::boundary_domain::{
    build_review_prompt_content_id, build_review_xsd_retry_prompt_content_id,
};
use crate::phases::PhaseContext;
use crate::prompts::content_builder::PromptContentReferences;
use crate::prompts::content_reference::{
    DiffContentReference, PlanContentReference, MAX_INLINE_CONTENT_SIZE,
};
use crate::prompts::SessionCapabilities;
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

        let xsd_retry_events = collect_xsd_retry_events(self, ctx, pass, prompt_mode)?;
        let baseline_oid = read_baseline_oid_for_prompts(ctx)?;
        let (
            prompt_key,
            review_prompt_xml,
            was_replayed,
            template_name,
            prompt_content_id,
            rendered_log,
        ) = match dispatch_prompt_mode(
            self,
            ctx,
            pass,
            prompt_mode,
            &baseline_oid,
            xsd_retry_events.as_ref(),
        ) {
            Ok(tuple) => tuple,
            Err(DispatchError::EarlyReturn(early)) => return Ok(*early),
            Err(DispatchError::Other(e)) => return Err(e),
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
        Ok(attach_xsd_retry_events(result, xsd_retry_events))
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

fn collect_xsd_retry_events(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    pass: u32,
    prompt_mode: PromptMode,
) -> Result<Option<Vec<PipelineEvent>>> {
    if matches!(prompt_mode, PromptMode::XsdRetry) {
        handler
            .materialize_xsd_retry_last_output(ctx, pass)
            .map(Some)
    } else {
        Ok(None)
    }
}

fn dispatch_prompt_mode(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    pass: u32,
    prompt_mode: PromptMode,
    baseline_oid: &str,
    xsd_retry_events: Option<&Vec<PipelineEvent>>,
) -> std::result::Result<BranchResult, DispatchError> {
    let materialized_inputs = handler
        .state
        .prompt_inputs
        .review
        .as_ref()
        .filter(|p| p.pass == pass);
    let continuation_state = &handler.state.continuation;
    match prompt_mode {
        PromptMode::XsdRetry => {
            build_xsd_retry_prompt(handler, ctx, pass, continuation_state, xsd_retry_events)
                .map_err(DispatchError::EarlyReturn)
        }
        PromptMode::SameAgentRetry => {
            let Some(inputs) = materialized_inputs else {
                return Err(DispatchError::Other(
                    ErrorEvent::ReviewInputsNotMaterialized { pass }.into(),
                ));
            };
            let plan_inline = resolve_plan_inline(ctx, &inputs.plan.representation)
                .map_err(DispatchError::Other)?;
            let diff_inline = resolve_diff_inline(ctx, &inputs.diff.representation, baseline_oid)
                .map_err(DispatchError::Other)?;
            build_same_agent_retry_prompt(
                handler,
                ctx,
                pass,
                continuation_state,
                inputs,
                ReviewInlineContent {
                    plan_inline,
                    diff_inline,
                    baseline_oid,
                },
            )
            .map_err(DispatchError::EarlyReturn)
        }
        PromptMode::Normal => {
            let Some(inputs) = materialized_inputs else {
                return Err(DispatchError::Other(
                    ErrorEvent::ReviewInputsNotMaterialized { pass }.into(),
                ));
            };
            let plan_inline = resolve_plan_inline(ctx, &inputs.plan.representation)
                .map_err(DispatchError::Other)?;
            let diff_inline = resolve_diff_inline(ctx, &inputs.diff.representation, baseline_oid)
                .map_err(DispatchError::Other)?;
            build_normal_prompt(
                handler,
                ctx,
                pass,
                inputs,
                plan_inline,
                diff_inline,
                baseline_oid,
            )
            .map_err(DispatchError::EarlyReturn)
        }
        PromptMode::Continuation => Err(DispatchError::Other(
            ErrorEvent::ReviewContinuationNotSupported.into(),
        )),
    }
}

enum DispatchError {
    EarlyReturn(Box<EffectResult>),
    Other(anyhow::Error),
}

fn read_workspace_file_or_fallback(
    ctx: &PhaseContext<'_>,
    path: &str,
    fallback: String,
) -> Result<String> {
    match ctx.workspace.read(Path::new(path)) {
        Ok(content) => Ok(content),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(fallback),
        Err(err) => Err(ErrorEvent::WorkspaceReadFailed {
            path: path.to_string(),
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
        PromptInputRepresentation::Inline => read_workspace_file_or_fallback(
            ctx,
            ".agent/PLAN.md",
            MainEffectHandler::sentinel_plan_content(ctx.config.isolation_mode),
        )
        .map(Some),
        PromptInputRepresentation::FileReference { .. } => Ok(None),
    }
}

fn resolve_diff_inline(
    ctx: &PhaseContext<'_>,
    representation: &PromptInputRepresentation,
    baseline_oid: &str,
) -> Result<Option<String>> {
    match representation {
        PromptInputRepresentation::Inline => read_workspace_file_or_fallback(
            ctx,
            ".agent/DIFF.backup",
            MainEffectHandler::fallback_diff_instructions(baseline_oid),
        )
        .map(Some),
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
    if was_replayed {
        return Ok(None);
    }
    render_and_check_xsd_retry_log(ctx, xsd_error, prompt_key, was_replayed, xsd_retry_events)
}

fn render_and_check_xsd_retry_log(
    ctx: &PhaseContext<'_>,
    xsd_error: &str,
    prompt_key: &str,
    was_replayed: bool,
    xsd_retry_events: Option<&Vec<PipelineEvent>>,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    use crate::prompts::prompt_review_xsd_retry_with_context_files_and_log;
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let rendered = prompt_review_xsd_retry_with_context_files_and_log(
        ctx.template_context,
        xsd_error,
        ctx.workspace,
        "review_xsd_retry",
        SessionCapabilities::new(&capabilities, &policy_flags),
    );
    if rendered.log.is_complete() {
        return Ok(Some(rendered.log));
    }
    let result = build_incomplete_template_result(
        rendered.log,
        "review_xsd_retry",
        prompt_key,
        was_replayed,
    );
    Err(Box::new(fold_xsd_retry_events(result, xsd_retry_events)))
}

fn fold_xsd_retry_events(
    result: EffectResult,
    xsd_retry_events: Option<&Vec<PipelineEvent>>,
) -> EffectResult {
    xsd_retry_events
        .into_iter()
        .flatten()
        .cloned()
        .fold(result, |r, ev| r.with_additional_event(ev))
}

type BranchResult = (
    String,
    String,
    bool,
    &'static str,
    Option<String>,
    Option<crate::prompts::SubstitutionLog>,
);

fn build_incomplete_template_result(
    log: crate::prompts::SubstitutionLog,
    template_name: &str,
    prompt_key: &str,
    was_replayed: bool,
) -> EffectResult {
    use crate::agents::AgentRole;
    let missing = log.unsubstituted.clone();
    EffectResult::event(PipelineEvent::template_rendered(
        crate::reducer::event::PipelinePhase::Review,
        template_name.to_string(),
        log,
    ))
    .with_ui_event(UIEvent::PromptReplayHit {
        key: prompt_key.to_string(),
        was_replayed,
    })
    .with_additional_event(PipelineEvent::agent_template_variables_invalid(
        AgentRole::Reviewer,
        template_name.to_string(),
        missing,
        Vec::new(),
    ))
}

fn validate_review_xml_template(
    ctx: &PhaseContext<'_>,
    refs: &PromptContentReferences,
    prompt_key: &str,
    was_replayed: bool,
    should_validate: bool,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    if !should_validate {
        return Ok(None);
    }
    render_and_check_review_xml_log(ctx, refs, prompt_key, was_replayed)
}

fn render_and_check_review_xml_log(
    ctx: &PhaseContext<'_>,
    refs: &PromptContentReferences,
    prompt_key: &str,
    was_replayed: bool,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let rendered = crate::prompts::prompt_review_xml_with_references_and_log(
        ctx.template_context,
        refs,
        ctx.workspace,
        "review_xml",
        SessionCapabilities::new(&capabilities, &policy_flags),
    );
    if rendered.log.is_complete() {
        return Ok(Some(rendered.log));
    }
    Err(Box::new(build_incomplete_template_result(
        rendered.log,
        "review_xml",
        prompt_key,
        was_replayed,
    )))
}

struct ReviewInlineContent<'a> {
    plan_inline: Option<String>,
    diff_inline: Option<String>,
    baseline_oid: &'a str,
}

fn resolve_xsd_error_text(continuation_state: &crate::reducer::state::ContinuationState) -> &str {
    continuation_state
        .last_review_xsd_error
        .as_deref()
        .filter(|s| !s.trim().is_empty())
        .unwrap_or("XML output failed validation. Provide valid XML output.")
}

fn compute_xsd_last_output_id(ctx: &PhaseContext<'_>) -> String {
    let (last_output, last_output_id_seed) = read_last_output_for_xsd_retry(ctx);
    last_output_id_seed.map_or_else(
        || sha256_hex_str(&last_output),
        |seed| sha256_hex_str(&seed),
    )
}

fn build_xsd_retry_scope_key(
    pass: u32,
    invalid_output_attempts: u32,
    recovery_epoch: u32,
) -> crate::prompts::PromptScopeKey {
    use crate::prompts::{PromptScopeKey, RetryMode};
    PromptScopeKey::for_review(
        pass,
        RetryMode::Xsd {
            count: invalid_output_attempts,
        },
        recovery_epoch,
    )
}

fn generate_xsd_retry_prompt_text(ctx: &PhaseContext<'_>, xsd_error: &str) -> String {
    use crate::prompts::prompt_review_xsd_retry_with_context_files_and_log;
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let rendered = prompt_review_xsd_retry_with_context_files_and_log(
        ctx.template_context,
        xsd_error,
        ctx.workspace,
        "review_xsd_retry",
        SessionCapabilities::new(&capabilities, &policy_flags),
    );
    rendered.content
}

fn get_xsd_retry_prompt_and_content_id(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    scope_key: &crate::prompts::PromptScopeKey,
    xsd_error: &str,
) -> (String, bool, String) {
    use crate::prompts::get_stored_or_generate_prompt;
    let current_prompt_content_id =
        build_review_xsd_retry_prompt_content_id(xsd_error, &compute_xsd_last_output_id(ctx));
    let (prompt, was_replayed) = get_stored_or_generate_prompt(
        scope_key,
        &handler.state.prompt_history,
        Some(&current_prompt_content_id),
        || generate_xsd_retry_prompt_text(ctx, xsd_error),
    );
    (prompt, was_replayed, current_prompt_content_id)
}

fn build_xsd_retry_prompt(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    pass: u32,
    continuation_state: &crate::reducer::state::ContinuationState,
    xsd_retry_events: Option<&Vec<PipelineEvent>>,
) -> std::result::Result<BranchResult, Box<EffectResult>> {
    let scope_key = build_xsd_retry_scope_key(
        pass,
        continuation_state.invalid_output_attempts,
        handler.state.recovery_epoch,
    );
    let xsd_error = resolve_xsd_error_text(continuation_state);
    let (prompt_key, prompt, was_replayed, content_id) =
        get_xsd_retry_keyed_prompt(handler, ctx, scope_key, xsd_error);
    let rendered_log =
        build_xsd_retry_rendered_log(ctx, xsd_error, &prompt_key, was_replayed, xsd_retry_events)?;
    Ok((
        prompt_key,
        prompt,
        was_replayed,
        "review_xsd_retry",
        Some(content_id),
        rendered_log,
    ))
}

fn get_xsd_retry_keyed_prompt(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    scope_key: crate::prompts::PromptScopeKey,
    xsd_error: &str,
) -> (String, String, bool, String) {
    let prompt_key = scope_key.to_string();
    let (prompt, was_replayed, content_id) =
        get_xsd_retry_prompt_and_content_id(handler, ctx, &scope_key, xsd_error);
    (prompt_key, prompt, was_replayed, content_id)
}

// --- Same-agent retry prompt branch ---

fn build_same_agent_content_refs(
    ctx: &PhaseContext<'_>,
    inputs: &crate::reducer::state::MaterializedReviewInputs,
    inline: ReviewInlineContent<'_>,
) -> PromptContentReferences {
    let plan_ref = build_plan_ref(inputs, inline.plan_inline, ctx.config.isolation_mode);
    let diff_ref = build_diff_ref(inputs, inline.diff_inline, inline.baseline_oid);
    PromptContentReferences {
        prompt: None,
        plan: Some(plan_ref),
        diff: Some(diff_ref),
    }
}

fn build_same_agent_scope_and_content_id(
    handler: &MainEffectHandler,
    pass: u32,
    continuation_state: &crate::reducer::state::ContinuationState,
    inputs: &crate::reducer::state::MaterializedReviewInputs,
    baseline_oid: &str,
) -> (crate::prompts::PromptScopeKey, String, String) {
    use crate::prompts::{PromptScopeKey, RetryMode};
    let scope_key = PromptScopeKey::for_review(
        pass,
        RetryMode::SameAgent {
            count: continuation_state.same_agent_retry_count,
        },
        handler.state.recovery_epoch,
    );
    let prompt_key = scope_key.to_string();
    let content_id = build_review_prompt_content_id(
        "review_same_agent_retry",
        &inputs.plan.content_id_sha256,
        &inputs.diff.content_id_sha256,
        baseline_oid,
        &handler.state.agent_chain.consumer_signature_sha256(),
    );
    (scope_key, prompt_key, content_id)
}

fn resolve_same_agent_base_prompt(
    ctx: &PhaseContext<'_>,
    refs: &PromptContentReferences,
) -> (String, bool) {
    use crate::prompts::prompt_review_xml_with_references;
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    ctx.workspace
        .read(Path::new(".agent/tmp/review_prompt.txt"))
        .map_or_else(
            |_| {
                (
                    prompt_review_xml_with_references(ctx.template_context, refs, ctx.workspace, SessionCapabilities::new(&capabilities, &policy_flags)),
                    true,
                )
            },
            |previous_prompt| {
                let previous_base =
                    crate::reducer::boundary::retry_guidance::strip_existing_same_agent_retry_preamble(
                        &previous_prompt,
                    )
                    .to_string();
                let freshly_rendered =
                    prompt_review_xml_with_references(ctx.template_context, refs, ctx.workspace, SessionCapabilities::new(&capabilities, &policy_flags));
                if previous_base == freshly_rendered {
                    (previous_base, false)
                } else {
                    (freshly_rendered, true)
                }
            },
        )
}

fn get_same_agent_prompt_and_validate_flag(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    scope_key: &crate::prompts::PromptScopeKey,
    content_id: &str,
    refs: &PromptContentReferences,
    retry_preamble: &str,
) -> (String, bool, bool) {
    use crate::prompts::get_stored_or_generate_prompt;
    let local_should_validate = std::cell::Cell::new(true);
    let retry_preamble = retry_preamble.to_string();
    let (prompt, was_replayed) = get_stored_or_generate_prompt(
        scope_key,
        &handler.state.prompt_history,
        Some(content_id),
        || {
            let (base_prompt, should_val) = resolve_same_agent_base_prompt(ctx, refs);
            local_should_validate.set(should_val);
            format!("{retry_preamble}\n{base_prompt}")
        },
    );
    let should_validate = !was_replayed && local_should_validate.get();
    (prompt, was_replayed, should_validate)
}

fn build_same_agent_retry_prompt(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
    pass: u32,
    continuation_state: &crate::reducer::state::ContinuationState,
    inputs: &crate::reducer::state::MaterializedReviewInputs,
    inline: ReviewInlineContent<'_>,
) -> std::result::Result<BranchResult, Box<EffectResult>> {
    let retry_preamble =
        crate::reducer::boundary::retry_guidance::same_agent_retry_preamble(continuation_state);
    let (scope_key, prompt_key, content_id) = build_same_agent_scope_and_content_id(
        handler,
        pass,
        continuation_state,
        inputs,
        inline.baseline_oid,
    );
    let refs = build_same_agent_content_refs(ctx, inputs, inline);
    let (prompt, was_replayed, should_validate) = get_same_agent_prompt_and_validate_flag(
        handler,
        ctx,
        &scope_key,
        &content_id,
        &refs,
        &retry_preamble,
    );
    let rendered_log =
        validate_review_xml_template(ctx, &refs, &prompt_key, was_replayed, should_validate)?;
    Ok((
        prompt_key,
        prompt,
        was_replayed,
        "review_xml",
        Some(content_id),
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
            let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
            let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
            let rendered = crate::prompts::prompt_review_xml_with_references_and_log(
                ctx.template_context,
                &refs,
                ctx.workspace,
                "review_xml",
                SessionCapabilities::new(&capabilities, &policy_flags),
            );
            rendered.content
        },
    );

    let refs = PromptContentReferences {
        prompt: None,
        plan: Some(plan_ref),
        diff: Some(diff_ref),
    };
    let rendered_log =
        validate_review_xml_template(ctx, &refs, &prompt_key, was_replayed, !was_replayed)?;

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

fn build_prompt_captured_event(
    was_replayed: bool,
    prompt_key: &str,
    review_prompt_xml: String,
    prompt_content_id: Option<String>,
) -> Option<PipelineEvent> {
    (!was_replayed).then(|| {
        crate::reducer::event::PipelineEvent::PromptInput(
            crate::reducer::event::PromptInputEvent::PromptCaptured {
                key: prompt_key.to_string(),
                content: review_prompt_xml,
                content_id: prompt_content_id,
            },
        )
    })
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
    let captured_event = build_prompt_captured_event(
        was_replayed,
        &prompt_key,
        review_prompt_xml,
        prompt_content_id,
    );
    let template_event = rendered_log.map(|log| {
        PipelineEvent::template_rendered(
            crate::reducer::event::PipelinePhase::Review,
            template_name.to_string(),
            log,
        )
    });
    [captured_event, template_event].into_iter().flatten().fold(
        EffectResult::event(PipelineEvent::review_prompt_prepared(pass)).with_ui_event(
            UIEvent::PromptReplayHit {
                key: prompt_key,
                was_replayed,
            },
        ),
        |r, ev| r.with_additional_event(ev),
    )
}
