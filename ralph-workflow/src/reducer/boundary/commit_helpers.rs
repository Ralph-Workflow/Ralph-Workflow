//! Commit phase helper functions.
//!
//! Pure helper functions extracted from commit.rs to keep file size under the
//! 1000-line boundary module limit. These functions are called by the
//! `MainEffectHandler` implementation in commit.rs.

use crate::agents::session::SessionDrain;
use crate::phases::commit;
use crate::phases::PhaseContext;
use crate::prompts::SessionCapabilities;
use crate::prompts::{
    get_stored_or_generate_prompt, prompt_generate_commit_message_with_diff_with_log,
    RenderedTemplate,
};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{PipelineEvent, PipelinePhase};
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::PromptInputKind;
use crate::reducer::ui_event::UIEvent;
use std::path::Path;

// ---------------------------------------------------------------------------
// Logging helpers
// ---------------------------------------------------------------------------

fn log_truncated_for_model_budget(
    ctx: &PhaseContext<'_>,
    original_bytes: u64,
    model_budget_bytes: u64,
    final_bytes: u64,
    model_safe_path: &Path,
) {
    ctx.logger.warn(&format!(
        "Diff size ({} KB) exceeds model budget ({} KB). Truncated to {} KB at: {}",
        original_bytes / 1024,
        model_budget_bytes / 1024,
        final_bytes / 1024,
        model_safe_path.display()
    ));
}

fn log_oversize_inline(
    ctx: &PhaseContext<'_>,
    final_bytes: u64,
    inline_budget_bytes: u64,
    model_safe_path: &Path,
) {
    ctx.logger.warn(&format!(
        "Diff size ({} KB) exceeds inline limit ({} KB). Referencing: {}",
        final_bytes / 1024,
        inline_budget_bytes / 1024,
        model_safe_path.display()
    ));
}

/// Log warnings about diff size (truncation or oversize inline).
pub(in crate::reducer::boundary) fn log_diff_size_warnings(
    ctx: &PhaseContext<'_>,
    truncated_for_model_budget: bool,
    original_bytes: u64,
    model_budget_bytes: u64,
    final_bytes: u64,
    inline_budget_bytes: u64,
    model_safe_path: &Path,
) {
    if truncated_for_model_budget {
        log_truncated_for_model_budget(
            ctx,
            original_bytes,
            model_budget_bytes,
            final_bytes,
            model_safe_path,
        );
    } else if final_bytes > inline_budget_bytes {
        log_oversize_inline(ctx, final_bytes, inline_budget_bytes, model_safe_path);
    }
}

// ---------------------------------------------------------------------------
// XSD retry helpers
// ---------------------------------------------------------------------------

/// Data produced by the xsd-retry prompt generation step.
pub(in crate::reducer::boundary) struct XsdRetryPromptData {
    pub prompt_key: String,
    pub prompt: String,
    pub was_replayed: bool,
    pub prompt_content_id: String,
    pub rendered_log: Option<crate::prompts::SubstitutionLog>,
}

fn resolve_xsd_error_message(handler: &crate::reducer::boundary::MainEffectHandler) -> String {
    handler
        .state
        .continuation
        .last_xsd_error
        .clone()
        .unwrap_or_else(|| "XML output failed validation. Provide valid XML output.".to_string())
}

pub(in crate::reducer::boundary) fn build_xsd_retry_prompt_data(
    handler: &crate::reducer::boundary::MainEffectHandler,
    ctx: &PhaseContext<'_>,
    attempt: u32,
    xsd_error: &str,
) -> std::result::Result<XsdRetryPromptData, Box<EffectResult>> {
    let (scope_key, prompt_content_id) =
        super::io_commit::build_xsd_retry_scope_and_content_id(handler, ctx, xsd_error, attempt);
    let prompt_key = scope_key.to_string();
    let (prompt, was_replayed) = get_stored_or_generate_prompt(
        &scope_key,
        &handler.state.prompt_history,
        Some(&prompt_content_id),
        || super::io_commit::gen_xsd_retry_prompt_content(ctx, xsd_error),
    );
    let rendered_log =
        super::io_commit::validate_xsd_retry_log(ctx, xsd_error, &prompt_key, was_replayed)?;
    Ok(XsdRetryPromptData {
        prompt_key,
        prompt,
        was_replayed,
        prompt_content_id,
        rendered_log,
    })
}

/// Generate and validate an xsd-retry commit prompt.
pub(in crate::reducer::boundary) fn gen_xsd_retry_commit_prompt(
    handler: &crate::reducer::boundary::MainEffectHandler,
    ctx: &PhaseContext<'_>,
    attempt: u32,
) -> std::result::Result<XsdRetryPromptData, Box<EffectResult>> {
    let xsd_error = resolve_xsd_error_message(handler);
    build_xsd_retry_prompt_data(handler, ctx, attempt, &xsd_error)
}

// ---------------------------------------------------------------------------
// Prompt data structures
// ---------------------------------------------------------------------------

/// Data produced by the per-mode prompt generation step.
pub(in crate::reducer::boundary) struct CommitPromptGenerated {
    pub prompt_key: String,
    pub prompt: String,
    pub was_replayed: bool,
    pub prompt_content_id: String,
    pub should_validate: bool,
}

/// Precondition check: commit prompt mode must never be Continuation.
pub(in crate::reducer::boundary) fn debug_assert_not_continuation(
    prompt_mode: crate::reducer::state::PromptMode,
) {
    debug_assert!(
        !matches!(prompt_mode, crate::reducer::state::PromptMode::Continuation),
        "Orchestrator must filter Continuation mode before deriving PrepareCommitPrompt effect"
    );
}

// ---------------------------------------------------------------------------
// Prompt generation helpers
// ---------------------------------------------------------------------------

/// Compute the content-id string for the commit prompt.
pub(in crate::reducer::boundary) fn compute_commit_prompt_content_id(
    handler: &crate::reducer::boundary::MainEffectHandler,
    diff_for_prompt: &str,
) -> String {
    let diff_content_id = handler
        .state
        .commit_diff_content_id_sha256
        .clone()
        .unwrap_or_else(|| sha256_hex_str(diff_for_prompt));
    let consumer_sig = handler.state.agent_chain.consumer_signature_sha256();
    commit::commit_prompt_content_id(
        &diff_content_id,
        &consumer_sig,
        &handler.state.commit_residual_files,
    )
}

/// Validate the commit message template log; return early EffectResult if incomplete.
pub(in crate::reducer::boundary) fn validate_commit_message_template(
    ctx: &PhaseContext<'_>,
    diff_for_prompt: &str,
    gen: &CommitPromptGenerated,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    if !needs_commit_template_validation(gen) {
        return Ok(None);
    }
    let rendered = render_commit_template(ctx, diff_for_prompt);
    check_template_completeness(rendered, gen)
}

fn needs_commit_template_validation(gen: &CommitPromptGenerated) -> bool {
    gen.should_validate && !gen.was_replayed
}

fn render_commit_template(ctx: &PhaseContext<'_>, diff_for_prompt: &str) -> RenderedTemplate {
    let (capabilities, policy_flags) = SessionCapabilities::from_drain(SessionDrain::Commit);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    prompt_generate_commit_message_with_diff_with_log(
        ctx.template_context,
        diff_for_prompt,
        ctx.workspace,
        "commit_message_xml",
        session_caps,
    )
}

fn check_template_completeness(
    rendered: RenderedTemplate,
    gen: &CommitPromptGenerated,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    match rendered.log.is_complete() {
        true => Ok(Some(rendered.log)),
        false => Err(Box::new(build_commit_template_invalid_result(
            rendered.log,
            &gen.prompt_key,
            gen.was_replayed,
        ))),
    }
}

pub(in crate::reducer::boundary) fn build_commit_template_invalid_result(
    log: crate::prompts::SubstitutionLog,
    prompt_key: &str,
    was_replayed: bool,
) -> EffectResult {
    use crate::agents::AgentRole;
    let missing = log.unsubstituted.clone();
    EffectResult::event(PipelineEvent::template_rendered(
        PipelinePhase::CommitMessage,
        "commit_message_xml".to_string(),
        log,
    ))
    .with_additional_event(PipelineEvent::agent_template_variables_invalid(
        AgentRole::Commit,
        "commit_message_xml".to_string(),
        missing,
        Vec::new(),
    ))
    .with_ui_event(UIEvent::PromptReplayHit {
        key: prompt_key.to_string(),
        was_replayed,
    })
}

/// Assemble the final EffectResult for an xsd-retry commit prompt.
pub(in crate::reducer::boundary) fn assemble_commit_xsd_retry_result(
    handler: &crate::reducer::boundary::MainEffectHandler,
    attempt: u32,
    prompt_key: String,
    prompt: String,
    prompt_content_id: String,
    was_replayed: bool,
    rendered_log: Option<crate::prompts::SubstitutionLog>,
) -> EffectResult {
    let prompt_captured_event =
        commit::prompt_captured_event(&prompt_key, &prompt, &prompt_content_id, was_replayed);
    commit::commit_prompt_prepared_result(
        attempt,
        handler.state.phase,
        prompt_key,
        was_replayed,
        prompt_captured_event,
        rendered_log,
        "commit_xsd_retry",
    )
}

/// Generate the prompt text for a same-agent-retry commit prompt.
pub(in crate::reducer::boundary) fn gen_same_agent_retry_prompt_text(
    ctx: &PhaseContext<'_>,
    diff_for_prompt: &str,
    retry_preamble: &str,
) -> String {
    let previous_prompt = ctx
        .workspace
        .read(Path::new(".agent/tmp/commit_prompt.txt"))
        .ok();
    let (capabilities, policy_flags) = SessionCapabilities::from_drain(SessionDrain::Commit);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let generated_base_prompt = prompt_generate_commit_message_with_diff_with_log(
        ctx.template_context,
        diff_for_prompt,
        ctx.workspace,
        "commit_message_xml",
        session_caps,
    )
    .content;
    let (base_prompt, _) = commit::base_prompt_for_same_agent_retry(
        previous_prompt.as_deref(),
        &generated_base_prompt,
    );
    format!("{retry_preamble}\n{base_prompt}")
}

/// Generate the prompt text for a normal commit prompt.
pub(in crate::reducer::boundary) fn gen_normal_commit_prompt_text(
    ctx: &PhaseContext<'_>,
    diff_for_prompt: &str,
    residual_files: &[String],
) -> String {
    let (capabilities, policy_flags) = SessionCapabilities::from_drain(SessionDrain::Commit);
    let session_caps = SessionCapabilities::new(&capabilities, &policy_flags);
    let rendered = prompt_generate_commit_message_with_diff_with_log(
        ctx.template_context,
        diff_for_prompt,
        ctx.workspace,
        "commit_message_xml",
        session_caps,
    );
    commit::prepend_residual_files_context(&rendered.content, residual_files)
}

// ---------------------------------------------------------------------------
// Event attachment helpers
// ---------------------------------------------------------------------------

/// Attach model-budget-truncation events to a materialized result if truncated.
pub(in crate::reducer::boundary) fn attach_truncated_budget_events(
    result: EffectResult,
    truncated: bool,
    content_id: &str,
    original_bytes: u64,
    final_bytes: u64,
    model_budget_bytes: u64,
) -> EffectResult {
    if !truncated {
        return result;
    }
    result
        .with_ui_event(UIEvent::AgentActivity {
            agent: "pipeline".to_string(),
            message: format!(
                "Truncated DIFF for model budget: {} KB -> {} KB (budget {} KB)",
                original_bytes / 1024,
                final_bytes / 1024,
                model_budget_bytes / 1024
            ),
        })
        .with_additional_event(PipelineEvent::prompt_input_oversize_detected(
            PipelinePhase::CommitMessage,
            PromptInputKind::Diff,
            content_id.to_string(),
            original_bytes,
            model_budget_bytes,
            "model-context".to_string(),
        ))
}

/// Attach oversize-inline events to a materialized result if over the inline budget.
pub(in crate::reducer::boundary) fn attach_oversize_inline_events(
    result: EffectResult,
    final_bytes: u64,
    inline_budget_bytes: u64,
    content_id: String,
) -> EffectResult {
    if final_bytes <= inline_budget_bytes {
        return result;
    }
    result
        .with_ui_event(UIEvent::AgentActivity {
            agent: "pipeline".to_string(),
            message: format!(
                "Oversize DIFF: {} KB > {} KB; using file reference",
                final_bytes / 1024,
                inline_budget_bytes / 1024
            ),
        })
        .with_additional_event(PipelineEvent::prompt_input_oversize_detected(
            PipelinePhase::CommitMessage,
            PromptInputKind::Diff,
            content_id,
            final_bytes,
            inline_budget_bytes,
            "inline-embedding".to_string(),
        ))
}
