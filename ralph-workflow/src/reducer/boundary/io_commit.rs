// reducer/boundary/io_commit.rs — boundary module for commit I/O operations.
// File stem is `io_commit` — recognized as boundary module by forbid_io_effects lint.

use crate::phases::PhaseContext;
use crate::prompts::content_reference::{DiffContentReference, MAX_INLINE_CONTENT_SIZE};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::WorkspaceIoErrorKind;
use crate::reducer::prompt_inputs::sha256_hex_str;
use crate::reducer::state::PromptInputRepresentation;
use anyhow::Result;
use std::path::Path;

pub(super) fn resolve_commit_diff_content_id(
    stored: Option<String>,
    ctx: &PhaseContext<'_>,
) -> String {
    stored
        .or_else(|| {
            let model_safe_path = Path::new(".agent/tmp/commit_diff.model_safe.txt");
            ctx.workspace
                .read(model_safe_path)
                .ok()
                .map(|diff| sha256_hex_str(&diff))
        })
        .unwrap_or_else(|| "missing_commit_diff_content_id".to_string())
}

pub(super) fn ensure_commit_tmp_dir(ctx: &PhaseContext<'_>) -> Result<()> {
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

pub(super) fn write_commit_prompt_file(ctx: &PhaseContext<'_>, prompt: &str) {
    if let Err(err) = ctx
        .workspace
        .write(Path::new(".agent/tmp/commit_prompt.txt"), prompt)
    {
        ctx.logger.warn(&format!(
            "Failed to write commit prompt file: {err}. Pipeline will continue (loop recovery will handle convergence)."
        ));
    }
}

pub(super) fn load_commit_diff_for_prompt(
    ctx: &PhaseContext<'_>,
    inputs: &crate::reducer::state::MaterializedCommitInputs,
    model_safe_path: &Path,
) -> std::result::Result<String, Box<EffectResult>> {
    match &inputs.diff.representation {
        PromptInputRepresentation::Inline => load_inline_commit_diff(ctx, model_safe_path),
        PromptInputRepresentation::FileReference { path } => {
            load_file_ref_commit_diff(ctx, path, inputs.diff.final_bytes)
        }
    }
}

fn load_inline_commit_diff(
    ctx: &PhaseContext<'_>,
    model_safe_path: &Path,
) -> std::result::Result<String, Box<EffectResult>> {
    match ctx.workspace.read(model_safe_path) {
        Ok(diff) => Ok(diff),
        Err(err) => {
            ctx.logger.warn(&format!(
                "Missing/unreadable materialized commit diff at {} ({err}); invalidating commit inputs to rematerialize",
                model_safe_path.display()
            ));
            Err(Box::new(EffectResult::event(
                PipelineEvent::commit_diff_invalidated(
                    "Missing/unreadable .agent/tmp/commit_diff.model_safe.txt".to_string(),
                ),
            )))
        }
    }
}

fn load_file_ref_commit_diff(
    ctx: &PhaseContext<'_>,
    path: &Path,
    final_bytes: u64,
) -> std::result::Result<String, Box<EffectResult>> {
    if !ctx.workspace.exists(path) {
        ctx.logger.warn(&format!(
            "Missing materialized commit diff reference at {}; invalidating commit inputs to rematerialize",
            path.display()
        ));
        return Err(Box::new(EffectResult::event(
            PipelineEvent::commit_diff_invalidated(
                "Missing materialized commit diff reference".to_string(),
            ),
        )));
    }
    Ok(DiffContentReference::ReadFromFile {
        path: path.to_path_buf(),
        start_commit: String::new(),
        description: format!(
            "Diff is {} bytes (exceeds {} limit)",
            final_bytes, MAX_INLINE_CONTENT_SIZE
        ),
    }
    .render_for_template())
}

pub(super) fn build_xsd_retry_scope_and_content_id(
    handler: &crate::reducer::boundary::MainEffectHandler,
    ctx: &PhaseContext<'_>,
    xsd_error: &str,
    attempt: u32,
) -> (crate::prompts::PromptScopeKey, String) {
    let consumer_sig = handler.state.agent_chain.consumer_signature_sha256();
    let diff_content_id =
        resolve_commit_diff_content_id(handler.state.commit_diff_content_id_sha256.clone(), ctx);
    let prompt_content_id = crate::phases::commit::commit_xsd_retry_prompt_content_id(
        &diff_content_id,
        xsd_error,
        &consumer_sig,
    );
    let scope_key = crate::prompts::PromptScopeKey::for_commit(
        handler.state.iteration,
        attempt,
        crate::prompts::RetryMode::Xsd {
            count: handler.state.continuation.xsd_retry_count,
        },
        handler.state.recovery_epoch,
    );
    (scope_key, prompt_content_id)
}

pub(super) fn gen_xsd_retry_prompt_content(ctx: &PhaseContext<'_>, xsd_error: &str) -> String {
    use std::io::Write;
    let rendered = crate::prompts::prompt_commit_xsd_retry_with_log(
        ctx.template_context,
        xsd_error,
        ctx.workspace,
        "commit_xsd_retry",
    );
    if !rendered.log.is_complete() {
        let _ = writeln!(
            std::io::stderr(),
            "Warning: Template rendering produced incomplete substitution log: {:?}",
            rendered.log.unsubstituted
        );
    }
    rendered.content
}

pub(super) fn validate_xsd_retry_log(
    ctx: &PhaseContext<'_>,
    xsd_error: &str,
    prompt_key: &str,
    was_replayed: bool,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    if was_replayed {
        return Ok(None);
    }
    validate_xsd_retry_rendered_log(ctx, xsd_error, prompt_key, was_replayed)
}

fn validate_xsd_retry_rendered_log(
    ctx: &PhaseContext<'_>,
    xsd_error: &str,
    prompt_key: &str,
    was_replayed: bool,
) -> std::result::Result<Option<crate::prompts::SubstitutionLog>, Box<EffectResult>> {
    let rendered = crate::prompts::prompt_commit_xsd_retry_with_log(
        ctx.template_context,
        xsd_error,
        ctx.workspace,
        "commit_xsd_retry",
    );
    match rendered.log.is_complete() {
        true => Ok(Some(rendered.log)),
        false => {
            let missing = rendered.log.unsubstituted.clone();
            Err(Box::new(build_xsd_retry_invalid_template_result(
                rendered.log,
                missing,
                prompt_key,
                was_replayed,
            )))
        }
    }
}

fn build_xsd_retry_invalid_template_result(
    log: crate::prompts::SubstitutionLog,
    missing: Vec<String>,
    prompt_key: &str,
    was_replayed: bool,
) -> EffectResult {
    use crate::agents::AgentRole;
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::ui_event::UIEvent;
    EffectResult::event(PipelineEvent::template_rendered(
        PipelinePhase::CommitMessage,
        "commit_xsd_retry".to_string(),
        log,
    ))
    .with_additional_event(PipelineEvent::agent_template_variables_invalid(
        AgentRole::Commit,
        "commit_xsd_retry".to_string(),
        missing,
        Vec::new(),
    ))
    .with_ui_event(UIEvent::PromptReplayHit {
        key: prompt_key.to_string(),
        was_replayed,
    })
}
