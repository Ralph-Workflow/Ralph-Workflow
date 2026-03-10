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
}
