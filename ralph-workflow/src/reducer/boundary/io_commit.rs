// reducer/boundary/io_commit.rs — boundary module for commit I/O operations.
// File stem is `io_commit` — recognized as boundary module by forbid_io_effects lint.

use crate::phases::PhaseContext;
use crate::prompts::content_reference::{DiffContentReference, MAX_INLINE_CONTENT_SIZE};
use crate::reducer::effect::EffectResult;
use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::WorkspaceIoErrorKind;
use crate::reducer::state::PromptInputRepresentation;
use anyhow::Result;
use std::path::Path;

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
