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
        excluded_files: &[crate::reducer::state::pipeline::ExcludedFile],
    ) -> Result<EffectResult> {
        use crate::git_helpers::{
            ensure_local_excludes, git_add_all_in_repo, git_add_specific_in_repo,
            git_commit_in_repo,
        };
        use crate::reducer::state::pipeline::ExcludedFileReason;

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

        // Add internal-ignore files to .git/info/exclude so agent artifacts do not
        // reappear as dirty files in subsequent status checks.
        let internal_ignore_paths: Vec<&str> = excluded_files
            .iter()
            .filter(|f| matches!(f.reason, ExcludedFileReason::InternalIgnore))
            .map(|f| f.path.as_str())
            .collect();
        if !internal_ignore_paths.is_empty() {
            if let Err(e) = ensure_local_excludes(ctx.repo_root, &internal_ignore_paths) {
                ctx.logger.warn(&format!(
                    "Failed to update .git/info/exclude for internal-ignore files: {e}"
                ));
            }
        }

        match git_commit_in_repo(ctx.repo_root, &message, None, None, Some(ctx.executor)) {
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

        let files: Vec<String> = status
            .lines()
            .filter_map(|line| {
                // git status --porcelain lines: "XY path" (first 3 chars are status + space)
                let path = line.get(3..).unwrap_or("").trim();
                if path.is_empty() {
                    None
                } else {
                    Some(path.to_string())
                }
            })
            .collect();

        ctx.logger.warn(&format!(
            "Residual files check (pass {pass}): {} uncommitted file(s) remain after selective commit.",
            files.len()
        ));

        Ok(EffectResult::event(PipelineEvent::residual_files_found(
            files, pass,
        )))
    }
}
