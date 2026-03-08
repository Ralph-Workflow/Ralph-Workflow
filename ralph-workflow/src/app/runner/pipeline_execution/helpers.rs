// Helper functions for pipeline execution.
//
// This module contains:
// - validate_prompt_and_setup_backup: Validate PROMPT.md and set up backup/protection
// - setup_prompt_monitor: Set up PROMPT.md monitoring for deletion detection
// - print_review_guidelines: Print review guidelines if detected
// - create_phase_context_with_config: Create the phase context with a modified config
// - print_pipeline_info_with_config: Print pipeline info with a specific config
// - save_start_commit_or_warn: Save starting commit or warn if it fails
// - check_prompt_restoration: Check for PROMPT.md restoration after a phase
// - handle_rebase_only: Handle --rebase-only flag

/// Validate PROMPT.md and set up backup/protection.
fn validate_prompt_and_setup_backup(ctx: &PipelineContext) -> anyhow::Result<()> {
    let prompt_validation = validate_prompt_md_with_workspace(
        &*ctx.workspace,
        ctx.config.behavior.strict_validation,
        ctx.args.interactive,
    );
    for err in &prompt_validation.errors {
        ctx.logger.error(err);
    }
    for warn in &prompt_validation.warnings {
        ctx.logger.warn(warn);
    }
    if !prompt_validation.is_valid() {
        anyhow::bail!("PROMPT.md validation errors");
    }

    // Create a backup of PROMPT.md to protect against accidental deletion.
    match create_prompt_backup_with_workspace(&*ctx.workspace) {
        Ok(None) => {}
        Ok(Some(warning)) => {
            ctx.logger.warn(&format!(
                "PROMPT.md backup created but: {warning}. Continuing anyway."
            ));
        }
        Err(e) => {
            ctx.logger.warn(&format!(
                "Failed to create PROMPT.md backup: {e}. Continuing anyway."
            ));
        }
    }

    // Permission locking is now handled by the reducer via LockPromptPermissions effect.
    // The runner no longer directly manipulates file permissions.

    Ok(())
}

/// Set up PROMPT.md monitoring for deletion detection.
fn setup_prompt_monitor(ctx: &PipelineContext) -> Option<PromptMonitor> {
    match PromptMonitor::new() {
        Ok(mut monitor) => {
            if let Err(e) = monitor.start() {
                ctx.logger.warn(&format!(
                    "Failed to start PROMPT.md monitoring: {e}. Continuing anyway."
                ));
                None
            } else {
                if ctx.config.verbosity.is_debug() {
                    ctx.logger.info("Started real-time PROMPT.md monitoring");
                }
                Some(monitor)
            }
        }
        Err(e) => {
            ctx.logger.warn(&format!(
                "Failed to create PROMPT.md monitor: {e}. Continuing anyway."
            ));
            None
        }
    }
}

/// Print review guidelines if detected.
fn print_review_guidelines(
    ctx: &PipelineContext,
    review_guidelines: Option<&crate::guidelines::ReviewGuidelines>,
) {
    if let Some(guidelines) = review_guidelines {
        ctx.logger.info(&format!(
            "Review guidelines: {}{}{}",
            ctx.colors.dim(),
            guidelines.summary(),
            ctx.colors.reset()
        ));
    }
}

/// Create the phase context with a modified config (for resume restoration).
///
/// When resuming from a checkpoint, this function enforces the configured
/// `execution_history_limit` by using `clone_bounded()` to drop oldest entries
/// beyond the limit. This prevents legacy checkpoints with oversized history
/// from reintroducing unbounded memory growth.
fn create_phase_context_with_config<'ctx>(
    ctx: &'ctx PipelineContext,
    config: &'ctx crate::config::Config,
    timer: &'ctx mut Timer,
    review_guidelines: Option<&'ctx crate::guidelines::ReviewGuidelines>,
    run_context: &'ctx crate::checkpoint::RunContext,
    resume_checkpoint: Option<&PipelineCheckpoint>,
    cloud_reporter: &'ctx dyn crate::cloud::CloudReporter,
) -> PhaseContext<'ctx> {
    // Restore execution history and prompt history from checkpoint if available.
    // IMPORTANT: When loading from checkpoint, we MUST enforce the configured
    // execution_history_limit using clone_bounded() to prevent oversized legacy
    // checkpoints from loading arbitrarily large history into memory.
    let execution_history = resume_checkpoint.map_or_else(
        crate::checkpoint::execution_history::ExecutionHistory::new,
        |checkpoint| {
            checkpoint
                .execution_history
                .as_ref()
                .map_or_else(
                    crate::checkpoint::execution_history::ExecutionHistory::new,
                    |h| h.clone_bounded(config.execution_history_limit),
                )
        },
    );

    PhaseContext {
        config,
        registry: &ctx.registry,
        logger: &ctx.logger,
        colors: &ctx.colors,
        timer,
        developer_agent: &ctx.developer_agent,
        reviewer_agent: &ctx.reviewer_agent,
        review_guidelines,
        template_context: &ctx.template_context,
        run_context: run_context.clone(),
        execution_history,
        executor: &*ctx.executor,
        executor_arc: std::sync::Arc::clone(&ctx.executor),
        repo_root: &ctx.repo_root,
        workspace: &*ctx.workspace,
        workspace_arc: std::sync::Arc::clone(&ctx.workspace),
        run_log_context: &ctx.run_log_context,
        cloud_reporter: if config.cloud.enabled {
            Some(cloud_reporter)
        } else {
            None
        },
        cloud: &config.cloud,
    }
}

/// Print pipeline info with a specific config.
fn print_pipeline_info_with_config(ctx: &PipelineContext, _config: &crate::config::Config) {
    ctx.logger.info(&format!(
        "Working directory: {}{}{}",
        ctx.colors.cyan(),
        ctx.repo_root.display(),
        ctx.colors.reset()
    ));
}

/// Save starting commit or warn if it fails.
///
/// This is best-effort: failures here must not terminate the pipeline.
fn save_start_commit_or_warn(ctx: &PipelineContext) {
    match crate::git_helpers::save_start_commit() {
        Ok(()) => {
            if ctx.config.verbosity.is_debug() {
                ctx.logger
                    .info("Saved starting commit for incremental diff generation");
            }
        }
        Err(e) => {
            ctx.logger.warn(&format!(
                "Failed to save starting commit: {e}. \
                 Incremental diffs may be unavailable as a result."
            ));
            ctx.logger.info(
                "To fix this issue, ensure .agent directory is writable and you have a valid HEAD commit.",
            );
        }
    }

    // Display start commit information to user
    match crate::git_helpers::get_start_commit_summary() {
        Ok(summary) => {
            if ctx.config.verbosity.is_debug() || summary.commits_since > 5 || summary.is_stale {
                ctx.logger.info(&summary.format_compact());
                if summary.is_stale {
                    ctx.logger.warn(
                        "Start commit is stale. Consider running: ralph --reset-start-commit",
                    );
                } else if summary.commits_since > 5 {
                    ctx.logger
                        .info("Tip: Run 'ralph --show-baseline' for more details");
                }
            }
        }
        Err(e) => {
            // Only show error in debug mode since this is informational
            if ctx.config.verbosity.is_debug() {
                ctx.logger
                    .warn(&format!("Failed to get start commit summary: {e}"));
            }
        }
    }
}

/// Check for PROMPT.md restoration after a phase.
fn check_prompt_restoration(
    ctx: &PipelineContext,
    prompt_monitor: &mut Option<PromptMonitor>,
    phase: &str,
) {
    if let Some(ref mut monitor) = prompt_monitor {
        for warning in monitor.drain_warnings() {
            ctx.logger
                .warn(&format!("PROMPT.md monitor warning: {warning}"));
        }
        if monitor.check_and_restore() {
            ctx.logger.warn(&format!(
                "PROMPT.md was deleted and restored during {phase} phase"
            ));
        }
    }
}

/// Handle --rebase-only flag.
///
/// This function performs a rebase to the default branch with AI conflict resolution and exits,
/// without running the full pipeline.
pub fn handle_rebase_only(
    _args: &Args,
    config: &crate::config::Config,
    template_context: &TemplateContext,
    logger: &Logger,
    colors: Colors,
    executor: &std::sync::Arc<dyn ProcessExecutor>,
    repo_root: &std::path::Path,
) -> anyhow::Result<()> {
    // Check if we're on main/master branch
    if is_main_or_master_branch()? {
        logger.warn("Already on main/master branch - rebasing on main is not recommended");
        logger.info("Tip: Use git worktrees to work on feature branches in parallel:");
        logger.info("  git worktree add ../feature-branch feature-branch");
        logger.info("This allows multiple AI agents to work on different features simultaneously.");
        logger.info("Proceeding with rebase anyway as requested...");
    }

    logger.header("Rebase to default branch", Colors::cyan);

    match run_rebase_to_default(logger, colors, &**executor) {
        Ok(RebaseResult::Success) => {
            logger.success("Rebase completed successfully");
            Ok(())
        }
        Ok(RebaseResult::NoOp { reason }) => {
            logger.info(&format!("No rebase needed: {reason}"));
            Ok(())
        }
        Ok(RebaseResult::Failed(err)) => {
            logger.error(&format!("Rebase failed: {err}"));
            anyhow::bail!("Rebase failed: {err}")
        }
        Ok(RebaseResult::Conflicts(_conflicts)) => {
            // Get the actual conflicted files
            let conflicted_files = get_conflicted_files()?;
            if conflicted_files.is_empty() {
                logger.warn("Rebase reported conflicts but no conflicted files found");
                let _ = abort_rebase(&**executor);
                return Ok(());
            }

            logger.warn(&format!(
                "Rebase resulted in {} conflict(s), attempting AI resolution",
                conflicted_files.len()
            ));

            // For --rebase-only, we don't have a full PhaseContext, so we use a wrapper
            match try_resolve_conflicts_without_phase_ctx(
                &conflicted_files,
                config,
                template_context,
                logger,
                colors,
                executor,
                repo_root,
            ) {
                Ok(true) => {
                    // Conflicts resolved, continue the rebase
                    logger.info("Continuing rebase after conflict resolution");
                    match continue_rebase(&**executor) {
                        Ok(()) => {
                            logger.success("Rebase completed successfully after AI resolution");
                            Ok(())
                        }
                        Err(e) => {
                            logger.error(&format!("Failed to continue rebase: {e}"));
                            let _ = abort_rebase(&**executor);
                            anyhow::bail!("Rebase failed after conflict resolution")
                        }
                    }
                }
                Ok(false) => {
                    // AI resolution failed
                    logger.error("AI conflict resolution failed, aborting rebase");
                    let _ = abort_rebase(&**executor);
                    anyhow::bail!("Rebase conflicts could not be resolved by AI")
                }
                Err(e) => {
                    logger.error(&format!("Conflict resolution error: {e}"));
                    let _ = abort_rebase(&**executor);
                    anyhow::bail!("Rebase conflict resolution failed: {e}")
                }
            }
        }
        Err(e) => {
            logger.error(&format!("Rebase failed: {e}"));
            anyhow::bail!("Rebase failed: {e}")
        }
    }
}

const fn should_write_complete_checkpoint(final_phase: crate::reducer::event::PipelinePhase) -> bool {
    matches!(final_phase, crate::reducer::event::PipelinePhase::Complete)
}

#[cfg(test)]
mod helpers_tests {
    use super::should_write_complete_checkpoint;
    use crate::reducer::event::PipelinePhase;

    #[test]
    fn test_should_write_complete_checkpoint_only_on_complete_phase() {
        assert!(should_write_complete_checkpoint(PipelinePhase::Complete));
        assert!(!should_write_complete_checkpoint(
            PipelinePhase::Interrupted
        ));
        assert!(!should_write_complete_checkpoint(
            PipelinePhase::AwaitingDevFix
        ));
    }
}
