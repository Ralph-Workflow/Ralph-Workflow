//! Git commit execution and skipping.
//!
//! This module handles the final step of the commit phase:
//! - Creating git commits with generated messages
//! - Skipping commits when no changes are staged
//! - Handling commit hook failures
//!
//! ## Process
//!
//! 1. Run `git add -A` to stage all changes
//! 2. Run `git commit -m <message>` with generated commit message
//! 3. Emit success/failure events based on outcome
//!
//! ## Commit Skipping
//!
//! If `git commit` reports no changes to commit, emits `commit_skipped`
//! event instead of failure. This is not an error condition.

use super::super::MainEffectHandler;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::ErrorEvent;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::WorkspaceIoErrorKind;
use anyhow::Result;

impl MainEffectHandler {
    /// Create git commit with generated message.
    ///
    /// Stages all changes with `git add -A` and creates commit.
    ///
    /// # Events Emitted
    ///
    /// - `commit_created` - Commit successfully created with hash
    /// - `commit_skipped` - No changes to commit (not an error)
    /// - `commit_generation_failed` - Git commit command failed
    ///
    /// # Errors
    ///
    /// - `GitAddAllFailed` - Failed to stage changes
    /// - `GitAddSpecificFailed` - Failed to stage specific paths
    pub(in crate::reducer::handler) fn create_commit(
        ctx: &PhaseContext<'_>,
        message: String,
        files: &[String],
        _excluded_files: &[crate::reducer::state::pipeline::ExcludedFile],
    ) -> Result<EffectResult> {
        use crate::git_helpers::{
            git_add_all_in_repo, git_add_specific_in_repo, git_commit_in_repo,
        };
        if files.is_empty() {
            git_add_all_in_repo(ctx.repo_root).map_err(|err| ErrorEvent::GitAddAllFailed {
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;
        } else {
            let file_refs: Vec<&str> = files.iter().map(String::as_str).collect();
            git_add_specific_in_repo(ctx.repo_root, &file_refs).map_err(
                |err: std::io::Error| ErrorEvent::GitAddSpecificFailed {
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                },
            )?;
        }

        match git_commit_in_repo(
            ctx.repo_root,
            &message,
            None,
            None,
            Some(ctx.executor),
            None,
        ) {
            Ok(Some(hash)) => Ok(EffectResult::event(PipelineEvent::commit_created(
                hash.to_string(),
                message,
            ))),
            Ok(None) => Ok(EffectResult::event(PipelineEvent::commit_skipped(
                "No changes to commit".to_string(),
            ))),
            Err(e) => Ok(EffectResult::event(
                PipelineEvent::commit_generation_failed(e.to_string()),
            )),
        }
    }

    /// Skip commit with a reason.
    ///
    /// Used when the orchestrator determines a commit should be skipped
    /// (e.g., empty diff, user-requested skip).
    ///
    /// # Events Emitted
    ///
    /// - `commit_skipped` - Commit skipped with reason
    pub(in crate::reducer::handler) const fn skip_commit(
        _ctx: &mut PhaseContext<'_>,
        reason: String,
    ) -> EffectResult {
        EffectResult::event(PipelineEvent::commit_skipped(reason))
    }

    /// Check for uncommitted changes before pipeline termination.
    ///
    /// Runs `git status --porcelain` to detect any uncommitted work.
    /// If changes exist, this is a critical safety failure - the pipeline
    /// should NOT terminate with uncommitted work.
    ///
    /// # Events Emitted
    ///
    /// - `pre_termination_safety_check_passed` - No uncommitted changes found
    ///
    /// # Errors
    ///
    /// - `GitStatusFailed` - Unable to determine working directory status
    pub(in crate::reducer::handler) fn check_uncommitted_changes_before_termination(
        ctx: &PhaseContext<'_>,
    ) -> Result<EffectResult> {
        use crate::git_helpers::git_snapshot_in_repo;

        let status =
            git_snapshot_in_repo(ctx.repo_root).map_err(|err| ErrorEvent::GitStatusFailed {
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        let has_changes = !status.trim().is_empty();

        if has_changes {
            let file_count = status.lines().count();
            ctx.logger.warn(&format!(
                "Pre-termination safety check: Uncommitted changes detected ({file_count} files). \
                 This should never happen - work should be committed before termination."
            ));

            // Route back through the commit phase so unattended runs cannot lose work.
            return Ok(EffectResult::event(
                PipelineEvent::pre_termination_uncommitted_changes_detected(file_count),
            ));
        }

        ctx.logger
            .info("Pre-termination safety check: No uncommitted changes found.");

        Ok(EffectResult::event(
            PipelineEvent::pre_termination_safety_check_passed(),
        ))
    }

    /// Check for uncommitted files remaining after a selective commit pass.
    ///
    /// After a selective commit (one that committed only specific files), there may
    /// be uncommitted changes remaining. This handler checks for them and emits:
    ///
    /// - `ResidualFilesFound { files, pass }` if dirty files remain
    /// - `ResidualFilesNone` if the working tree is clean
    ///
    /// # Events Emitted
    ///
    /// - `residual_files_found` - Uncommitted files remain after selective commit
    /// - `residual_files_none` - Working tree is clean
    ///
    /// # Errors
    ///
    /// - `GitStatusFailed` - Unable to determine working directory status
    pub(in crate::reducer::handler) fn check_residual_files(
        ctx: &PhaseContext<'_>,
        pass: u8,
    ) -> Result<EffectResult> {
        use crate::git_helpers::git_snapshot_in_repo;

        let status =
            git_snapshot_in_repo(ctx.repo_root).map_err(|err| ErrorEvent::GitStatusFailed {
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        if status.trim().is_empty() {
            ctx.logger.info(&format!(
                "Residual files check (pass {pass}): Working tree is clean."
            ));
            return Ok(EffectResult::event(PipelineEvent::residual_files_none()));
        }

        let files = crate::git_helpers::parse_git_status_paths(&status);

        ctx.logger.warn(&format!(
            "Residual files check (pass {pass}): {} uncommitted file(s) remain after selective commit.",
            files.len()
        ));

        Ok(EffectResult::event(PipelineEvent::residual_files_found(
            files, pass,
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::MainEffectHandler;
    use crate::agents::AgentRegistry;
    use crate::checkpoint::execution_history::ExecutionHistory;
    use crate::checkpoint::RunContext;
    use crate::config::Config;
    use crate::executor::{MockProcessExecutor, ProcessExecutor};
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::MemoryWorkspace;
    use std::path::Path;
    use std::sync::Arc;

    #[test]
    fn test_create_commit_does_not_mutate_local_exclude_from_excluded_files_metadata() {
        use crate::reducer::state::pipeline::{ExcludedFile, ExcludedFileReason};

        let tmp = tempfile::tempdir().expect("tempdir");
        let repo_root = tmp.path();
        let _repo = git2::Repository::init(repo_root).expect("init repo");

        // Seed an existing exclude file so we can detect mutation deterministically.
        let info_dir = repo_root.join(".git").join("info");
        std::fs::create_dir_all(&info_dir).expect("create .git/info");
        let exclude_path = info_dir.join("exclude");
        std::fs::write(&exclude_path, "# existing\n").expect("write exclude");

        // Create one real, committable file.
        std::fs::create_dir_all(repo_root.join("src")).expect("create src");
        std::fs::write(repo_root.join("src").join("main.rs"), "fn main() {}\n")
            .expect("write src/main.rs");

        // Create an agent artifact that is excluded via commit XML metadata.
        std::fs::create_dir_all(repo_root.join(".agent").join("tmp")).expect("create .agent/tmp");
        std::fs::write(
            repo_root.join(".agent").join("tmp").join("trace.log"),
            "trace\n",
        )
        .expect("write .agent/tmp/trace.log");

        let excluded_files = vec![ExcludedFile {
            path: ".agent/tmp/trace.log".to_string(),
            reason: ExcludedFileReason::InternalIgnore,
        }];

        // Minimal PhaseContext setup (workspace is unused by create_commit).
        let workspace = MemoryWorkspace::new_test();
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();
        let config = Config::default();
        let registry = AgentRegistry::new().expect("registry");
        let template_context = TemplateContext::default();

        let executor = Arc::new(MockProcessExecutor::new());
        let executor_arc: Arc<dyn ProcessExecutor> = executor;

        let run_log_context = crate::logging::RunLogContext::new(&workspace).expect("run log ctx");
        let cloud = crate::config::types::CloudConfig::disabled();

        let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();
        let ctx = crate::phases::PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "claude",
            reviewer_agent: "claude",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: executor_arc.as_ref(),
            executor_arc: executor_arc.clone(),
            repo_root,
            workspace: &workspace,
            workspace_arc: Arc::new(workspace.clone()),
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &cloud,
            env: &git_env,
        };

        let _ = MainEffectHandler::create_commit(
            &ctx,
            "test: commit".to_string(),
            &[],
            &excluded_files,
        )
        .expect("create_commit should succeed");

        // Excluded-files metadata is audit-only; it must not change local ignore state.
        let content = std::fs::read_to_string(&exclude_path).expect("read exclude");
        assert_eq!(content, "# existing\n");
    }

    #[test]
    fn test_create_commit_with_empty_file_list_stages_all_changes() {
        // Sanity check: if this starts failing, the above test might be failing for the wrong reason.
        let tmp = tempfile::tempdir().expect("tempdir");
        let repo_root = tmp.path();
        let repo = git2::Repository::init(repo_root).expect("init repo");

        std::fs::create_dir_all(repo_root.join("src")).expect("create src");
        std::fs::write(repo_root.join("src").join("main.rs"), "fn main() {}\n")
            .expect("write src/main.rs");

        let workspace = MemoryWorkspace::new_test();
        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);
        let mut timer = Timer::new();
        let config = Config::default();
        let registry = AgentRegistry::new().expect("registry");
        let template_context = TemplateContext::default();

        let executor = Arc::new(MockProcessExecutor::new());
        let executor_arc: Arc<dyn ProcessExecutor> = executor;

        let run_log_context = crate::logging::RunLogContext::new(&workspace).expect("run log ctx");
        let cloud = crate::config::types::CloudConfig::disabled();

        let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();
        let ctx = crate::phases::PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "claude",
            reviewer_agent: "claude",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: executor_arc.as_ref(),
            executor_arc: executor_arc.clone(),
            repo_root,
            workspace: &workspace,
            workspace_arc: Arc::new(workspace.clone()),
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &cloud,
            env: &git_env,
        };

        let _ = MainEffectHandler::create_commit(&ctx, "test: commit".to_string(), &[], &[])
            .expect("create_commit should succeed");

        // Confirm HEAD exists now.
        let head = repo.head().expect("head");
        assert!(head.target().is_some(), "expected a new commit");

        // Confirm the committed tree contains src/main.rs.
        let commit = head.peel_to_commit().expect("commit");
        let tree = commit.tree().expect("tree");
        let entry = tree.get_path(Path::new("src/main.rs")).expect("tree entry");
        assert!(entry.id() != git2::Oid::zero());
    }
}
