// Helper functions for pipeline execution.
//
// This module contains:
// - command_requires_prompt_setup: Classify commands by PROMPT.md dependency
// - handle_repo_commands_without_prompt_setup: Early repo commands that bypass PROMPT.md
// - prepare_agent_phase_for_workspace: Shared agent-phase setup for pipeline and repo commands
// - validate_prompt_and_setup_backup: Validate PROMPT.md and set up backup/protection
// - setup_prompt_monitor: Set up PROMPT.md monitoring for deletion detection
// - print_review_guidelines: Print review guidelines if detected
// - create_phase_context_with_config: Create the phase context with a modified config
// - print_pipeline_info_with_config: Print pipeline info with a specific config
// - save_start_commit_or_warn: Save starting commit or warn if it fails
// - check_prompt_restoration: Check for PROMPT.md restoration after a phase
// - handle_rebase_only: Handle --rebase-only flag

use crate::app::context::PipelineContext;
use crate::app::pipeline_setup::RepoCommandBoundaryParams;
use crate::app::rebase::conflicts::try_resolve_conflicts_without_phase_ctx;
use crate::app::rebase::orchestration::run_rebase_to_default;
use crate::checkpoint::PipelineCheckpoint;
use crate::files::protection::monitoring::PromptMonitor;
use crate::files::{create_prompt_backup_with_workspace, validate_prompt_md_with_workspace};
use crate::git_helpers::{
    abort_rebase, continue_rebase, get_conflicted_files, is_main_or_master_branch, RebaseResult,
};
use crate::agents::session::AuditTrail;
use crate::phases::PhaseContext;
use crate::pipeline::Timer;

pub(crate) const fn command_requires_prompt_setup(args: &Args) -> bool {
    !args.recovery.dry_run
        && !args.recovery.inspect_checkpoint
        && !args.rebase_flags.rebase_only
        && !args.commit_plumbing.generate_commit_msg
        && !args.commit_plumbing.apply_commit
        && !args.commit_display.show_commit_msg
        && !args.commit_display.reset_start_commit
        && !args.commit_display.show_baseline
}

pub struct CommandExitCleanupGuard<'a> {
    logger: &'a Logger,
    workspace: &'a dyn crate::workspace::Workspace,
    owns_cleanup: bool,
    restore_prompt_permissions: bool,
}

impl<'a> CommandExitCleanupGuard<'a> {
    pub const fn new(
        logger: &'a Logger,
        workspace: &'a dyn crate::workspace::Workspace,
        restore_prompt_permissions: bool,
    ) -> Self {
        Self {
            logger,
            workspace,
            owns_cleanup: false,
            restore_prompt_permissions,
        }
    }

    pub(crate) const fn mark_owned(&mut self) {
        self.owns_cleanup = true;
    }
}

impl Drop for CommandExitCleanupGuard<'_> {
    fn drop(&mut self) {
        if !self.owns_cleanup {
            return;
        }
        if self.restore_prompt_permissions {
            if let Some(warning) = crate::files::make_prompt_writable_with_workspace(self.workspace)
            {
                self.logger.warn(&format!(
                    "PROMPT.md permission restore during command cleanup: {warning}"
                ));
            }
        }
        crate::git_helpers::cleanup_agent_phase_protections_silent_at(self.workspace.root());
    }
}

pub(crate) fn prepare_agent_phase_for_workspace(
    repo_root: &std::path::Path,
    workspace: &dyn crate::workspace::Workspace,
    logger: &Logger,
    git_helpers: &mut crate::git_helpers::GitHelpers,
    restore_prompt_permissions: bool,
) {
    if let Err(err) = crate::git_helpers::cleanup_orphaned_marker_with_workspace(workspace, logger)
    {
        logger.warn(&format!("Failed to cleanup orphaned marker: {err}"));
    }

    if restore_prompt_permissions {
        if let Some(warning) = crate::files::make_prompt_writable_with_workspace(workspace) {
            logger.warn(&format!(
                "PROMPT.md permission restore on startup: {warning}"
            ));
        }
    }

    if let Err(err) = crate::git_helpers::create_marker_with_workspace(workspace) {
        logger.warn(&format!("Failed to create agent phase marker: {err}"));
    }

    if crate::interrupt::is_user_interrupt_requested() {
        return;
    }

    crate::git_helpers::cleanup_orphaned_wrapper_at(repo_root);

    let hooks_dir = crate::git_helpers::get_hooks_dir_in_repo(repo_root);
    let ralph_hook_detected = hooks_dir.ok().is_some_and(|dir| {
        crate::git_helpers::RALPH_HOOK_NAMES.iter().any(|name| {
            crate::files::file_contains_marker(&dir.join(name), crate::git_helpers::HOOK_MARKER)
                .unwrap_or(false)
        })
    });

    if ralph_hook_detected {
        if let Err(err) = crate::git_helpers::uninstall_hooks_in_repo(repo_root, logger) {
            logger.warn(&format!("Startup hook cleanup warning: {err}"));
        }
    }

    if crate::interrupt::is_user_interrupt_requested() {
        return;
    }

    if let Err(err) = crate::git_helpers::start_agent_phase_in_repo(repo_root, git_helpers) {
        logger.warn(&format!("Failed to start agent phase: {err}"));
    }
}

#[derive(Copy, Clone)]
pub(crate) struct RepoCommandParams<'a> {
    pub(crate) args: &'a Args,
    pub(crate) config: &'a crate::config::Config,
    pub(crate) registry: &'a AgentRegistry,
    pub(crate) developer_agent: &'a str,
    pub(crate) reviewer_agent: &'a str,
    pub(crate) logger: &'a Logger,
    pub(crate) colors: Colors,
    pub(crate) executor: &'a std::sync::Arc<dyn ProcessExecutor>,
    pub(crate) repo_root: &'a std::path::Path,
    pub(crate) workspace: &'a std::sync::Arc<dyn crate::workspace::Workspace>,
}

pub(crate) fn handle_repo_commands_without_prompt_setup(
    params: RepoCommandParams<'_>,
) -> anyhow::Result<bool> {
    let RepoCommandParams {
        args,
        config,
        registry,
        developer_agent,
        reviewer_agent,
        logger,
        colors,
        executor,
        repo_root,
        workspace,
    } = params;

    crate::app::pipeline_setup::handle_repo_commands_boundary(RepoCommandBoundaryParams {
        args,
        config,
        registry,
        developer_agent,
        reviewer_agent,
        logger,
        colors,
        executor,
        repo_root,
        workspace,
    })
}

/// Validate PROMPT.md and set up backup/protection.
pub(crate) fn validate_prompt_and_setup_backup(ctx: &PipelineContext) -> anyhow::Result<()> {
    let prompt_validation = validate_prompt_md_with_workspace(
        &*ctx.workspace,
        ctx.config.behavior.strict_validation,
        ctx.args.interactive,
    );
    prompt_validation
        .errors
        .iter()
        .for_each(|err| ctx.logger.error(err));
    prompt_validation
        .warnings
        .iter()
        .for_each(|warn| ctx.logger.warn(warn));
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
pub(crate) fn setup_prompt_monitor(ctx: &PipelineContext) -> Option<PromptMonitor> {
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
pub(crate) fn print_review_guidelines(
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
pub(crate) fn create_phase_context_with_config<'ctx>(
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
            checkpoint.execution_history.as_ref().map_or_else(
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
        env: &crate::runtime::environment::RealGitEnvironment,
        active_session: None,
        audit_trail: AuditTrail::new(),
    }
}

/// Print pipeline info with a specific config.
pub(crate) fn print_pipeline_info_with_config(
    ctx: &PipelineContext,
    _config: &crate::config::Config,
) {
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
pub(crate) fn save_start_commit_or_warn(ctx: &PipelineContext) {
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
pub(crate) fn check_prompt_restoration(
    ctx: &PipelineContext,
    prompt_monitor: &mut Option<PromptMonitor>,
    phase: &str,
) {
    if let Some(ref mut monitor) = prompt_monitor {
        monitor.drain_warnings().iter().for_each(|warning| {
            ctx.logger
                .warn(&format!("PROMPT.md monitor warning: {warning}"));
        });
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

const fn should_write_complete_checkpoint(
    final_phase: crate::reducer::event::PipelinePhase,
) -> bool {
    matches!(final_phase, crate::reducer::event::PipelinePhase::Complete)
}

#[cfg(test)]
mod helpers_tests {
    use super::command_requires_prompt_setup;
    use super::should_write_complete_checkpoint;
    use super::CommandExitCleanupGuard;
    use crate::git_helpers::agent_phase_test_lock;
    use crate::reducer::event::PipelinePhase;
    use crate::workspace::WorkspaceFs;
    use clap::Parser;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

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

    #[test]
    fn test_command_requires_prompt_setup_only_for_prompt_dependent_commands() {
        let default_args = crate::cli::Args::parse_from(["ralph"]);
        assert!(command_requires_prompt_setup(&default_args));

        let generate_commit_args = crate::cli::Args::parse_from(["ralph", "--generate-commit-msg"]);
        assert!(!command_requires_prompt_setup(&generate_commit_args));

        let dry_run_args = crate::cli::Args::parse_from(["ralph", "--dry-run"]);
        assert!(!command_requires_prompt_setup(&dry_run_args));

        let rebase_only_args = crate::cli::Args::parse_from(["ralph", "--rebase-only"]);
        assert!(!command_requires_prompt_setup(&rebase_only_args));

        let apply_commit_args = crate::cli::Args::parse_from(["ralph", "--apply-commit"]);
        assert!(!command_requires_prompt_setup(&apply_commit_args));

        let inspect_checkpoint_args =
            crate::cli::Args::parse_from(["ralph", "--inspect-checkpoint"]);
        assert!(!command_requires_prompt_setup(&inspect_checkpoint_args));
    }

    #[test]
    fn test_command_cleanup_guard_without_ownership_preserves_existing_protections() {
        let _test_lock = agent_phase_test_lock().lock().unwrap();
        let tempdir = tempfile::tempdir().unwrap();
        let repo_root = tempdir.path();
        let _repo = git2::Repository::init(repo_root).unwrap();
        let logger = crate::logger::Logger::new(crate::logger::Colors::with_enabled(false));
        let workspace = WorkspaceFs::new(repo_root.to_path_buf());

        let marker_path = repo_root.join(".git/ralph/no_agent_commit");
        std::fs::create_dir_all(marker_path.parent().unwrap()).unwrap();
        std::fs::write(&marker_path, "").unwrap();

        {
            let _guard = CommandExitCleanupGuard::new(&logger, &workspace, true);
        }

        assert!(
            marker_path.exists(),
            "cleanup guard must not remove protections that this command did not create"
        );
    }

    #[test]
    fn test_command_cleanup_guard_with_ownership_removes_protections() {
        let _test_lock = agent_phase_test_lock().lock().unwrap();
        let tempdir = tempfile::tempdir().unwrap();
        let repo_root = tempdir.path();
        let _repo = git2::Repository::init(repo_root).unwrap();
        let logger = crate::logger::Logger::new(crate::logger::Colors::with_enabled(false));
        let workspace = WorkspaceFs::new(repo_root.to_path_buf());

        let marker_path = repo_root.join(".git/ralph/no_agent_commit");
        std::fs::create_dir_all(marker_path.parent().unwrap()).unwrap();
        std::fs::write(&marker_path, "").unwrap();

        {
            let mut guard = CommandExitCleanupGuard::new(&logger, &workspace, true);
            guard.mark_owned();
        }

        assert!(
            !marker_path.exists(),
            "cleanup guard must remove protections owned by this command"
        );
    }

    #[test]
    #[cfg(unix)]
    fn test_command_cleanup_guard_for_promptless_command_preserves_prompt_permissions() {
        let _test_lock = agent_phase_test_lock().lock().unwrap();
        let tempdir = tempfile::tempdir().unwrap();
        let repo_root = tempdir.path();
        let _repo = git2::Repository::init(repo_root).unwrap();
        let logger = crate::logger::Logger::new(crate::logger::Colors::with_enabled(false));
        let workspace = WorkspaceFs::new(repo_root.to_path_buf());

        let prompt_path = repo_root.join("PROMPT.md");
        std::fs::write(&prompt_path, "# locked\n").unwrap();
        std::fs::set_permissions(&prompt_path, std::fs::Permissions::from_mode(0o444)).unwrap();

        let marker_path = repo_root.join(".git/ralph/no_agent_commit");
        std::fs::create_dir_all(marker_path.parent().unwrap()).unwrap();
        std::fs::write(&marker_path, "").unwrap();

        {
            let mut guard = CommandExitCleanupGuard::new(&logger, &workspace, false);
            guard.mark_owned();
        }

        let mode = std::fs::metadata(&prompt_path)
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(
            mode, 0o444,
            "promptless commands must not unlock PROMPT.md permissions"
        );
        assert!(
            !marker_path.exists(),
            "promptless commands must still remove their owned protections"
        );
    }
}
