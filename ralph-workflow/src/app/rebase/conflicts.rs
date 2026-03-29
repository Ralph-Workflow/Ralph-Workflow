use super::types::{ConflictResolutionContext, ConflictResolutionResult};
use crate::logger::{Colors, Logger};
use crate::prompts::template_context::TemplateContext;
use crate::prompts::{get_stored_or_generate_prompt, PromptScopeKey};
use crate::ProcessExecutor;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ConflictResolutionPromptReplay {
    pub key: String,
    pub was_replayed: bool,
    pub captured_entry: Option<crate::prompts::PromptHistoryEntry>,
}

/// Attempt to resolve rebase conflicts with AI.
///
/// This function returns the prompt_history for callers to persist as needed.
pub(super) fn try_resolve_conflicts(
    conflicted_files: &[String],
    ctx: &ConflictResolutionContext<'_>,
    phase: &str,
    executor: &dyn ProcessExecutor,
) -> anyhow::Result<(
    bool,
    ConflictResolutionPromptReplay,
    std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
)> {
    try_resolve_conflicts_with_hook(conflicted_files, ctx, phase, executor, |_replay| {
        // Default behavior: prompt history is already captured in the returned HashMap.
        // Callers that need custom checkpoint timing can use `try_resolve_conflicts_with_hook`.
        None
    })
}

/// Attempt to resolve rebase conflicts with AI, invoking a hook after prompt capture.
///
/// This is used by callers that need to checkpoint immediately after the conflict
/// resolution prompt is captured into `prompt_history` (before invoking the agent),
/// ensuring deterministic prompt replay on resume.
pub(super) fn try_resolve_conflicts_with_hook<F>(
    conflicted_files: &[String],
    ctx: &ConflictResolutionContext<'_>,
    phase: &str,
    executor: &dyn ProcessExecutor,
    mut after_prompt_capture: F,
) -> anyhow::Result<(
    bool,
    ConflictResolutionPromptReplay,
    std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
)>
where
    F: FnMut(
        &ConflictResolutionPromptReplay,
    ) -> Option<(String, crate::prompts::PromptHistoryEntry)>,
{
    if conflicted_files.is_empty() {
        return Ok((
            false,
            ConflictResolutionPromptReplay {
                key: PromptScopeKey::for_conflict_resolution(phase, 0).to_string(),
                was_replayed: false,
                captured_entry: None,
            },
            std::collections::HashMap::new(),
        ));
    }

    ctx.logger.info(&format!(
        "Attempting AI conflict resolution for {} file(s)",
        conflicted_files.len()
    ));

    let conflicts = collect_conflict_info_or_error(conflicted_files, ctx.workspace, ctx.logger)?;

    // Content-id validation for replay determinism: conflict-resolution prompts must not replay
    // across different conflict sets/content.
    let current_content_id = conflict_resolution_content_id(phase, &conflicts);

    // Use typed PromptScopeKey for conflict resolution (RFC-007 arch correction #2).
    // Display output is byte-identical to the former format!("{}_conflict_resolution", ...).
    // recovery_epoch is 0: the rebase handler owns epoch semantics via PromptCaptured events;
    // this helper function is not a reducer.
    let scope_key = PromptScopeKey::for_conflict_resolution(phase, 0);
    let prompt_key = scope_key.to_string();
    let prompt_history_cell = super::boundary::create_prompt_history_cell();
    let (resolution_prompt, was_replayed) = get_stored_or_generate_prompt(
        &scope_key,
        &prompt_history_cell.borrow(),
        Some(&current_content_id),
        || build_resolution_prompt(&conflicts, ctx.template_context, ctx.workspace),
    );

    let replay = ConflictResolutionPromptReplay {
        key: prompt_key,
        was_replayed,
        captured_entry: if was_replayed {
            None
        } else {
            Some(crate::prompts::PromptHistoryEntry::new(
                resolution_prompt.clone(),
                Some(current_content_id),
            ))
        },
    };

    let captured_entry = after_prompt_capture(&replay);

    let prompt_history: std::collections::HashMap<_, _> = {
        let base_history = prompt_history_cell.into_inner();
        base_history.into_iter().chain(captured_entry).collect()
    };

    let resolved = match run_ai_conflict_resolution(&resolution_prompt, ctx) {
        Ok(ConflictResolutionResult::FileEditsOnly) => {
            handle_file_edits_resolution(ctx.logger, ctx.workspace.root())?
        }
        Ok(ConflictResolutionResult::Failed) => {
            handle_failed_resolution(ctx.logger, ctx.workspace.root(), executor)
        }
        Err(e) => handle_error_resolution(ctx.logger, ctx.workspace.root(), executor, &e),
    };

    Ok((resolved, replay, prompt_history))
}

pub(crate) fn conflict_resolution_content_id(
    phase: &str,
    conflicts: &std::collections::HashMap<String, crate::prompts::FileConflict>,
) -> String {
    use crate::reducer::prompt_inputs::sha256_hex_str;
    use std::collections::BTreeMap;

    let sorted_keys: BTreeMap<_, ()> = conflicts.keys().map(|k| (k, ())).collect();

    let content_parts: Vec<String> = sorted_keys
        .keys()
        .filter_map(|k| {
            conflicts.get(*k).map(|c| {
                format!(
                    "{}\n{}\n{}\n{}",
                    k, c.conflict_content, c.current_content, ""
                )
            })
        })
        .collect();

    let s = format!(
        "conflict_resolution|{}\n{}\n",
        phase.to_lowercase(),
        content_parts.join("\n")
    );
    sha256_hex_str(&s)
}

pub(crate) fn handle_file_edits_resolution(
    logger: &Logger,
    repo_root: &std::path::Path,
) -> anyhow::Result<bool> {
    logger.info("Agent resolved conflicts via file edits (no JSON output)");

    // If we can't get conflicted files (e.g., in tests with mock workspace),
    // assume no conflicts remain after successful resolution.
    let remaining_conflicts =
        crate::git_helpers::get_conflicted_files_at(repo_root).unwrap_or_default();
    if remaining_conflicts.is_empty() {
        logger.success("All conflicts resolved via file edits");
        Ok(true)
    } else {
        logger.warn(&format!(
            "{} conflicts remain after AI resolution",
            remaining_conflicts.len()
        ));
        Ok(false)
    }
}

pub(crate) fn handle_failed_resolution(
    logger: &Logger,
    repo_root: &std::path::Path,
    executor: &dyn ProcessExecutor,
) -> bool {
    logger.warn("AI conflict resolution failed");
    logger.info("Attempting to continue rebase anyway...");

    match crate::git_helpers::continue_rebase_at(repo_root, executor) {
        Ok(()) => {
            logger.info("Successfully continued rebase");
            true
        }
        Err(rebase_err) => {
            logger.warn(&format!("Failed to continue rebase: {rebase_err}"));
            false
        }
    }
}

pub(crate) fn handle_error_resolution(
    logger: &Logger,
    repo_root: &std::path::Path,
    executor: &dyn ProcessExecutor,
    e: &anyhow::Error,
) -> bool {
    logger.warn(&format!("AI conflict resolution failed: {e}"));
    logger.info("Attempting to continue rebase anyway...");

    match crate::git_helpers::continue_rebase_at(repo_root, executor) {
        Ok(()) => {
            logger.info("Successfully continued rebase");
            true
        }
        Err(rebase_err) => {
            logger.warn(&format!("Failed to continue rebase: {rebase_err}"));
            false
        }
    }
}

pub(crate) fn collect_conflict_info_or_error(
    conflicted_files: &[String],
    workspace: &dyn crate::workspace::Workspace,
    logger: &Logger,
) -> anyhow::Result<std::collections::HashMap<String, crate::prompts::FileConflict>> {
    use crate::prompts::collect_conflict_info_with_workspace;

    match collect_conflict_info_with_workspace(workspace, conflicted_files) {
        Ok(c) => Ok(c),
        Err(e) => {
            logger.error(&format!("Failed to collect conflict info: {e}"));
            anyhow::bail!("Failed to collect conflict info");
        }
    }
}

pub(crate) fn build_resolution_prompt(
    conflicts: &std::collections::HashMap<String, crate::prompts::FileConflict>,
    template_context: &TemplateContext,
    workspace: &dyn crate::workspace::Workspace,
) -> String {
    let prompt =
        build_enhanced_resolution_prompt(conflicts, None::<()>, template_context, workspace);
    if prompt.trim().is_empty() {
        return format!(
            "# MERGE CONFLICT RESOLUTION\n\nEmpty prompt generated.\n\nConflicts:\n{:#?}",
            conflicts.keys().collect::<Vec<_>>()
        );
    }
    prompt
}

fn build_enhanced_resolution_prompt(
    conflicts: &std::collections::HashMap<String, crate::prompts::FileConflict>,
    _branch_info: Option<()>,
    template_context: &TemplateContext,
    workspace: &dyn crate::workspace::Workspace,
) -> String {
    use std::path::Path;

    let prompt_md_content = workspace.read(Path::new("PROMPT.md")).ok();
    let plan_content = workspace.read(Path::new(".agent/PLAN.md")).ok();

    crate::prompts::build_conflict_resolution_prompt_with_context(
        template_context,
        conflicts,
        prompt_md_content.as_deref(),
        plan_content.as_deref(),
    )
}

pub(super) fn run_ai_conflict_resolution(
    resolution_prompt: &str,
    ctx: &ConflictResolutionContext<'_>,
) -> anyhow::Result<ConflictResolutionResult> {
    let reviewer_agent = ctx.config.reviewer_agent.as_deref().unwrap_or("codex");

    crate::app::boundary::conflict_resolution::run_ai_conflict_resolution_with_runtime(
        crate::app::boundary::conflict_resolution::ConflictResolutionRuntimeParams {
            resolution_prompt,
            config: ctx.config,
            logger: ctx.logger,
            colors: ctx.colors,
            executor_arc: std::sync::Arc::clone(&ctx.executor_arc),
            workspace: ctx.workspace,
            workspace_arc: std::sync::Arc::clone(&ctx.workspace_arc),
            reviewer_agent,
            registry: ctx.registry,
        },
    )
}

/// Wrapper for conflict resolution without a prompt history map.
///
/// This is used for `--rebase-only` mode where we don't have a full pipeline context.
/// Captured prompts are not persisted since `--rebase-only` has no checkpoint mechanism.
pub(crate) fn try_resolve_conflicts_without_phase_ctx(
    conflicted_files: &[String],
    config: &crate::config::Config,
    template_context: &TemplateContext,
    logger: &Logger,
    colors: Colors,
    executor: &std::sync::Arc<dyn ProcessExecutor>,
    repo_root: &std::path::Path,
) -> anyhow::Result<bool> {
    use crate::agents::AgentRegistry;
    use anyhow::Context;

    let registry = AgentRegistry::new()?;

    let workspace = crate::workspace::WorkspaceFs::new(repo_root.to_path_buf());
    let workspace_arc: std::sync::Arc<dyn crate::workspace::Workspace> =
        std::sync::Arc::new(workspace.clone());

    let executor_arc = std::sync::Arc::clone(executor);

    let ctx = ConflictResolutionContext {
        config,
        registry: &registry,
        template_context,
        logger,
        colors,
        executor_arc,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::clone(&workspace_arc),
    };

    // Ephemeral prompt history for --rebase-only: prompts are captured for intra-run determinism
    // but not persisted (no checkpoint in this mode).
    let (resolved, _replay, _prompt_history) =
        try_resolve_conflicts(conflicted_files, &ctx, "RebaseOnly", &**executor)
            .context("Conflict resolution failed in --rebase-only mode")?;

    Ok(resolved)
}

#[cfg(test)]
#[path = "conflicts/tests.rs"]
mod tests;
