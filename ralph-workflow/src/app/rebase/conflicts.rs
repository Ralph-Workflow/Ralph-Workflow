use super::types::{ConflictResolutionContext, ConflictResolutionResult};
use crate::executor::ProcessExecutor;
use crate::logger::{Colors, Logger};
use crate::phases::PhaseContext;
use crate::prompts::template_context::TemplateContext;
use anyhow::Context;

/// Attempt to resolve rebase conflicts with AI.
///
/// This function accepts `PhaseContext` to capture prompts and track
/// execution history for hardened resume functionality.
pub fn try_resolve_conflicts(
    conflicted_files: &[String],
    ctx: &ConflictResolutionContext<'_>,
    phase_ctx: &mut PhaseContext<'_>,
    phase: &str,
    executor: &dyn ProcessExecutor,
) -> anyhow::Result<bool> {
    if conflicted_files.is_empty() {
        return Ok(false);
    }

    ctx.logger.info(&format!(
        "Attempting AI conflict resolution for {} file(s)",
        conflicted_files.len()
    ));

    let conflicts = collect_conflict_info_or_error(conflicted_files, ctx.workspace, ctx.logger)?;

    // Use stored_or_generate pattern for hardened resume.
    // The rebase conflict key is a dynamic string (not a PromptScopeKey) because
    // it is phase-name-based and outside the main pipeline phase hierarchy.
    let prompt_key = format!("{}_conflict_resolution", phase.to_lowercase());
    let (resolution_prompt, was_replayed) = phase_ctx.prompt_history.get(&prompt_key).map_or_else(
        || {
            (
                build_resolution_prompt(&conflicts, ctx.template_context, ctx.workspace),
                false,
            )
        },
        |stored| (stored.clone(), true),
    );

    // Capture the resolution prompt for deterministic resume (only if newly generated)
    if was_replayed {
        ctx.logger.info(&format!(
            "Using stored prompt from checkpoint for determinism: {prompt_key}"
        ));
    } else {
        phase_ctx.capture_prompt(&prompt_key, &resolution_prompt);
    }

    match run_ai_conflict_resolution(&resolution_prompt, ctx) {
        Ok(ConflictResolutionResult::FileEditsOnly) => handle_file_edits_resolution(ctx.logger),
        Ok(ConflictResolutionResult::Failed) => Ok(handle_failed_resolution(ctx.logger, executor)),
        Err(e) => Ok(handle_error_resolution(ctx.logger, executor, &e)),
    }
}

fn handle_file_edits_resolution(logger: &Logger) -> anyhow::Result<bool> {
    logger.info("Agent resolved conflicts via file edits (no JSON output)");

    let remaining_conflicts = crate::git_helpers::get_conflicted_files()?;
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

fn handle_failed_resolution(logger: &Logger, executor: &dyn ProcessExecutor) -> bool {
    logger.warn("AI conflict resolution failed");
    logger.info("Attempting to continue rebase anyway...");

    match crate::git_helpers::continue_rebase(executor) {
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

fn handle_error_resolution(
    logger: &Logger,
    executor: &dyn ProcessExecutor,
    e: &anyhow::Error,
) -> bool {
    logger.warn(&format!("AI conflict resolution failed: {e}"));
    logger.info("Attempting to continue rebase anyway...");

    match crate::git_helpers::continue_rebase(executor) {
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

fn collect_conflict_info_or_error(
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

fn build_resolution_prompt(
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

fn run_ai_conflict_resolution(
    resolution_prompt: &str,
    ctx: &ConflictResolutionContext<'_>,
) -> anyhow::Result<ConflictResolutionResult> {
    use crate::pipeline::{run_with_prompt, PipelineRuntime, PromptCommand};
    use std::path::Path;

    let log_dir = ".agent/logs/rebase_conflict_resolution";
    let reviewer_agent = ctx.config.reviewer_agent.as_deref().unwrap_or("codex");

    let executor_ref: &dyn crate::executor::ProcessExecutor = &*ctx.executor_arc;
    let mut runtime = PipelineRuntime {
        timer: &mut crate::pipeline::Timer::new(),
        logger: ctx.logger,
        colors: &ctx.colors,
        config: ctx.config,
        executor: executor_ref,
        executor_arc: std::sync::Arc::clone(&ctx.executor_arc),
        workspace: ctx.workspace,
        workspace_arc: std::sync::Arc::clone(&ctx.workspace_arc),
    };

    ctx.workspace.create_dir_all(Path::new(log_dir))?;

    let agent_config = ctx
        .registry
        .resolve_config(reviewer_agent)
        .ok_or_else(|| anyhow::anyhow!("Agent not found: {reviewer_agent}"))?;
    let cmd_str = agent_config.build_cmd_with_model(true, true, true, None);

    let log_prefix = format!("{log_dir}/conflict_resolution");
    let model_index = 0usize;
    let attempt = crate::pipeline::logfile::next_logfile_attempt_index(
        Path::new(&log_prefix),
        reviewer_agent,
        model_index,
        ctx.workspace,
    );
    let logfile = crate::pipeline::logfile::build_logfile_path_with_attempt(
        &log_prefix,
        reviewer_agent,
        model_index,
        attempt,
    );

    let prompt_cmd = PromptCommand {
        label: reviewer_agent,
        display_name: reviewer_agent,
        cmd_str: &cmd_str,
        prompt: resolution_prompt,
        log_prefix: &log_prefix,
        model_index: Some(model_index),
        attempt: Some(attempt),
        logfile: &logfile,
        parser_type: agent_config.json_parser,
        env_vars: &agent_config.env_vars,
    };

    let result = run_with_prompt(&prompt_cmd, &mut runtime)?;
    if result.exit_code != 0 {
        return Ok(ConflictResolutionResult::Failed);
    }

    let remaining_conflicts = crate::git_helpers::get_conflicted_files()?;
    if remaining_conflicts.is_empty() {
        Ok(ConflictResolutionResult::FileEditsOnly)
    } else {
        Ok(ConflictResolutionResult::Failed)
    }
}

/// Wrapper for conflict resolution without `PhaseContext`.
///
/// This is used for --rebase-only mode where we don't have a full pipeline context.
pub fn try_resolve_conflicts_without_phase_ctx(
    conflicted_files: &[String],
    config: &crate::config::Config,
    template_context: &TemplateContext,
    logger: &Logger,
    colors: Colors,
    executor: &std::sync::Arc<dyn ProcessExecutor>,
    repo_root: &std::path::Path,
) -> anyhow::Result<bool> {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::execution_history::ExecutionHistory;
    use crate::logging::RunLogContext;
    use crate::pipeline::Timer;

    let registry = AgentRegistry::new()?;
    let mut timer = Timer::new();

    let workspace = crate::workspace::WorkspaceFs::new(repo_root.to_path_buf());
    let workspace_arc: std::sync::Arc<dyn crate::workspace::Workspace> =
        std::sync::Arc::new(workspace.clone());

    let reviewer_agent = config.reviewer_agent.as_deref().unwrap_or("codex");
    let developer_agent = config.developer_agent.as_deref().unwrap_or("codex");

    let executor_arc = std::sync::Arc::clone(executor);

    // Create run log context for per-run logging
    let run_log_context = RunLogContext::new(&workspace)
        .context("Failed to create run log context for conflict resolution")?;

    let mut phase_ctx = PhaseContext {
        config,
        registry: &registry,
        logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent,
        reviewer_agent,
        review_guidelines: None,
        template_context,
        run_context: crate::checkpoint::RunContext::new(),
        execution_history: ExecutionHistory::new(),
        prompt_history: std::collections::HashMap::new(),
        executor: &**executor,
        executor_arc: std::sync::Arc::clone(&executor_arc),
        repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::clone(&workspace_arc),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &config.cloud,
    };

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

    try_resolve_conflicts(
        conflicted_files,
        &ctx,
        &mut phase_ctx,
        "RebaseOnly",
        &**executor,
    )
}

#[cfg(test)]
#[path = "conflicts/tests.rs"]
mod tests;
