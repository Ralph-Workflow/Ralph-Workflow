use super::MainEffectHandler;
use crate::agents::AgentDrain;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{
    CommitEvent, DevelopmentEvent, ErrorEvent, PipelineEvent, PipelinePhase, PlanningEvent,
    ReviewEvent, WorkspaceIoErrorKind,
};
use anyhow::Result;
use std::path::Path;

impl MainEffectHandler {
    /// Unified cleanup handler for required files.
    ///
    /// Deletes the specified files from the workspace and emits the appropriate
    /// event based on the current phase. This consolidates the five per-phase
    /// cleanup effects into a single unified handler.
    ///
    /// # Phase-to-Event Mapping
    ///
    /// - Planning: emits `PlanningEvent::PlanXmlCleaned`
    /// - Development: emits `DevelopmentEvent::XmlCleaned`
    /// - Review (issues): emits `ReviewEvent::IssuesXmlCleaned`
    /// - Review (fix): emits `ReviewEvent::FixResultXmlCleaned`
    /// - Commit: emits `CommitEvent::XmlCleaned`
    pub(super) fn cleanup_required_files(
        &self,
        ctx: &PhaseContext<'_>,
        files: &[String],
    ) -> EffectResult {
        files.iter().for_each(|file_path| {
            let path = Path::new(file_path);
            if ctx.workspace.exists(path) {
                let _ = ctx.workspace.remove_if_exists(path);
            }
        });

        cleanup_required_files_event(self, ctx)
    }

    pub(super) fn validate_final_state(&self, _ctx: &mut PhaseContext<'_>) -> EffectResult {
        // Transition to Finalizing phase to restore PROMPT.md permissions
        // via the effect system before marking the pipeline complete
        let event = PipelineEvent::finalizing_started();

        // Emit phase transition UI event
        let ui_event = self.phase_transition_ui(PipelinePhase::Finalizing);

        EffectResult::with_ui(event, vec![ui_event])
    }

    pub(super) fn cleanup_context(ctx: &PhaseContext<'_>) -> Result<EffectResult> {
        ctx.logger
            .info("Cleaning up context files to prevent pollution...");

        let plan_cleaned = remove_file_if_exists(ctx, Path::new(".agent/PLAN.md"))?;
        let issues_cleaned = remove_file_if_exists(ctx, Path::new(".agent/ISSUES.md"))?;
        let xml_cleaned = remove_xml_files_from_tmp(ctx)?;
        cleanup_continuation_context_file(ctx)?;

        let cleaned_count = plan_cleaned as usize + issues_cleaned as usize + xml_cleaned;

        if cleaned_count > 0 {
            ctx.logger.success(&format!(
                "Context cleanup complete: {cleaned_count} files deleted"
            ));
        } else {
            ctx.logger.info("No context files to clean up");
        }

        Ok(EffectResult::event(PipelineEvent::context_cleaned()))
    }

    pub(super) fn restore_prompt_permissions(&self, ctx: &PhaseContext<'_>) -> EffectResult {
        use crate::files::make_prompt_writable_with_workspace;

        ctx.logger.info("Restoring PROMPT.md write permissions...");

        let warning = make_prompt_writable_with_workspace(ctx.workspace);

        if let Some(ref msg) = warning {
            ctx.logger.warn(msg);
        }

        self.apply_phase_transition_to_restore_result(build_restore_permissions_result(warning))
    }

    fn apply_phase_transition_to_restore_result(&self, result: EffectResult) -> EffectResult {
        if self.state.phase == PipelinePhase::Finalizing {
            result.with_ui_event(self.phase_transition_ui(PipelinePhase::Complete))
        } else {
            result
        }
    }

    pub(super) fn lock_prompt_permissions(ctx: &PhaseContext<'_>) -> EffectResult {
        use crate::files::make_prompt_read_only_with_workspace;

        ctx.logger
            .info("Locking PROMPT.md (read-only protection during execution)...");

        let warning = make_prompt_read_only_with_workspace(ctx.workspace);

        if let Some(ref msg) = warning {
            ctx.logger.warn(&format!("{msg}. Continuing anyway."));
        }

        let event = PipelineEvent::prompt_permissions_locked(warning);

        EffectResult::event(event)
    }

    pub(super) fn cleanup_continuation_context(ctx: &PhaseContext<'_>) -> Result<EffectResult> {
        cleanup_continuation_context_file(ctx)?;
        Ok(EffectResult::event(
            PipelineEvent::development_continuation_context_cleaned(),
        ))
    }

    /// Write timeout context to a temp file for session-less agent retry.
    ///
    /// When a timeout occurs with meaningful partial output but the agent doesn't
    /// support session IDs, this handler extracts the context from the logfile
    /// and writes it to a temp file that the retry prompt can reference.
    pub(super) fn write_timeout_context(
        ctx: &PhaseContext<'_>,
        role: crate::agents::AgentRole,
        logfile_path: &str,
        context_path: &str,
    ) -> Result<EffectResult> {
        ctx.logger.info(&format!(
            "Preserving timeout context for session-less agent retry: {context_path}"
        ));

        // Read the logfile content
        let logfile = Path::new(logfile_path);
        let content =
            ctx.workspace
                .read(logfile)
                .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                    path: logfile_path.to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                })?;

        // Write to the context file
        let context_file = Path::new(context_path);
        ctx.workspace.write(context_file, &content).map_err(|err| {
            ErrorEvent::WorkspaceWriteFailed {
                path: context_path.to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            }
        })?;

        ctx.logger.success(&format!(
            "Timeout context preserved ({} bytes)",
            content.len()
        ));

        Ok(EffectResult::event(
            PipelineEvent::agent_timeout_context_written(
                role,
                logfile_path.to_string(),
                context_path.to_string(),
            ),
        ))
    }

    pub(super) fn trigger_loop_recovery(
        ctx: &PhaseContext<'_>,
        detected_loop: &str,
        loop_count: u32,
    ) -> EffectResult {
        ctx.logger.warn(&format!(
            "⚠️  LOOP DETECTED: Same effect repeated {loop_count} times: {detected_loop}"
        ));
        ctx.logger
            .info("Triggering mandatory loop recovery to break the cycle...");
        ctx.logger
            .info("Emitting loop recovery event (state cleanup will occur in reducer)");

        // Note: The actual state cleanup (XSD retry reset, session clear, loop counter reset)
        // happens in the reducer when LoopRecoveryTriggered event is reduced.
        // This handler only emits the event to trigger that cleanup.

        ctx.logger
            .success("Loop recovery triggered. Pipeline will resume with fresh state.");

        EffectResult::event(PipelineEvent::loop_recovery_triggered(
            detected_loop.to_owned(),
            loop_count,
        ))
    }

    pub(super) fn emit_recovery_reset(
        &self,
        ctx: &PhaseContext<'_>,
        reset_type: &crate::reducer::effect::RecoveryResetType,
        target_phase: crate::reducer::event::PipelinePhase,
    ) -> EffectResult {
        use crate::reducer::event::AwaitingDevFixEvent;

        // Log the recovery reset for observability
        ctx.logger.info(&format!(
            "Recovery escalation: {reset_type:?} reset to phase {target_phase:?}"
        ));

        // Emit RecoveryAttempted event to signal transition back to work
        EffectResult::event(PipelineEvent::AwaitingDevFix(
            AwaitingDevFixEvent::RecoveryAttempted {
                level: match reset_type {
                    crate::reducer::effect::RecoveryResetType::PhaseStart => 2,
                    crate::reducer::effect::RecoveryResetType::IterationReset => 3,
                    crate::reducer::effect::RecoveryResetType::CompleteReset => 4,
                },
                attempt_count: self.state.dev_fix_attempt_count,
                target_phase,
            },
        ))
    }

    pub(super) fn attempt_recovery(
        &self,
        ctx: &PhaseContext<'_>,
        level: u32,
        attempt_count: u32,
    ) -> EffectResult {
        use crate::reducer::event::AwaitingDevFixEvent;

        let target_phase = self
            .state
            .failed_phase_for_recovery
            .or(self.state.previous_phase)
            .unwrap_or(PipelinePhase::Development);
        let target_phase = if target_phase == PipelinePhase::AwaitingDevFix {
            PipelinePhase::Development
        } else {
            target_phase
        };

        ctx.logger.info(&format!(
            "Attempting recovery level {level} (attempt {attempt_count})"
        ));

        // Emit RecoveryAttempted event to transition back to failed phase
        EffectResult::event(PipelineEvent::AwaitingDevFix(
            AwaitingDevFixEvent::RecoveryAttempted {
                level,
                attempt_count,
                target_phase,
            },
        ))
    }

    pub(super) fn emit_recovery_success(
        ctx: &PhaseContext<'_>,
        level: u32,
        total_attempts: u32,
    ) -> EffectResult {
        use crate::reducer::event::AwaitingDevFixEvent;

        ctx.logger.info(&format!(
            "Recovery succeeded at level {level} after {total_attempts} attempts"
        ));

        // Emit RecoverySucceeded event to clear recovery state
        EffectResult::event(PipelineEvent::AwaitingDevFix(
            AwaitingDevFixEvent::RecoverySucceeded {
                level,
                total_attempts,
            },
        ))
    }

    pub(super) fn ensure_gitignore_entries(ctx: &PhaseContext<'_>) -> EffectResult {
        ctx.logger
            .info("Ensuring .gitignore contains agent artifact entries...");

        let gitignore_path = Path::new(".gitignore");
        let required_entries = vec!["/PROMPT*", ".agent/"];
        let file_created = !ctx.workspace.exists(gitignore_path);
        let existing_content = ctx.workspace.read(gitignore_path).unwrap_or_default();

        let (already_present, entries_added): (Vec<_>, Vec<_>) = required_entries
            .into_iter()
            .partition(|pattern| entry_exists(&existing_content, pattern));

        let entries_added: Vec<String> = entries_added.into_iter().map(String::from).collect();
        let already_present: Vec<String> = already_present.into_iter().map(String::from).collect();

        if entries_added.is_empty() {
            ctx.logger
                .info("All required .gitignore entries already present");
            return EffectResult::event(PipelineEvent::gitignore_entries_ensured(
                entries_added,
                already_present,
                file_created,
            ));
        }

        write_gitignore_entries(
            ctx,
            gitignore_path,
            &existing_content,
            &entries_added,
            already_present,
            file_created,
        )
    }
}

fn build_gitignore_new_content(existing_content: &str, entries_added: &[String]) -> String {
    let suffix = if existing_content.is_empty() || existing_content.ends_with('\n') {
        String::new()
    } else {
        "\n".to_string()
    };
    let entries_str = format!(
        "# Ralph-workflow artifacts (auto-generated)\n{}\n",
        entries_added.join("\n")
    );
    format!("{existing_content}{suffix}{entries_str}")
}

fn write_gitignore_entries(
    ctx: &PhaseContext<'_>,
    gitignore_path: &Path,
    existing_content: &str,
    entries_added: &[String],
    already_present: Vec<String>,
    file_created: bool,
) -> EffectResult {
    let new_content = build_gitignore_new_content(existing_content, entries_added);
    if ctx.workspace.write(gitignore_path, &new_content).is_ok() {
        ctx.logger.success(&format!(
            "Added {} entries to .gitignore: {}",
            entries_added.len(),
            entries_added.join(", ")
        ));
        EffectResult::event(PipelineEvent::gitignore_entries_ensured(
            entries_added.to_vec(),
            already_present,
            file_created,
        ))
    } else {
        ctx.logger
            .warn("Failed to write .gitignore (continuing anyway)");
        EffectResult::event(PipelineEvent::gitignore_entries_ensured(
            Vec::new(),
            already_present,
            file_created,
        ))
    }
}

fn build_restore_permissions_result(warning: Option<String>) -> EffectResult {
    let event = PipelineEvent::prompt_permissions_restored();
    EffectResult::event(event)
        .maybe_with_additional_event(warning.map(PipelineEvent::prompt_permissions_restore_warning))
}

fn remove_file_if_exists(ctx: &PhaseContext<'_>, path: &Path) -> Result<bool> {
    if ctx.workspace.exists(path) {
        ctx.workspace
            .remove(path)
            .map_err(|err| ErrorEvent::WorkspaceRemoveFailed {
                path: path.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;
        Ok(true)
    } else {
        Ok(false)
    }
}

fn remove_xml_files_from_tmp(ctx: &PhaseContext<'_>) -> std::result::Result<usize, ErrorEvent> {
    let tmp_dir = Path::new(".agent/tmp");
    if !ctx.workspace.exists(tmp_dir) {
        return Ok(0);
    }
    let entries =
        ctx.workspace
            .read_dir(tmp_dir)
            .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                path: tmp_dir.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;
    entries
        .into_iter()
        .filter(|entry| entry.path().extension().and_then(|s| s.to_str()) == Some("xml"))
        .try_fold(0usize, |count, entry| {
            let path = entry.path();
            ctx.workspace
                .remove(path)
                .map_err(|err| ErrorEvent::WorkspaceRemoveFailed {
                    path: path.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                })
                .map(|()| count + 1)
        })
}

fn cleanup_required_files_event(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
) -> EffectResult {
    match handler.state.phase {
        PipelinePhase::Planning => cleanup_planning_phase_xml_event(handler),
        PipelinePhase::Development => cleanup_development_phase_xml_event(handler),
        PipelinePhase::Review => cleanup_review_phase_event(handler),
        PipelinePhase::CommitMessage => cleanup_commit_phase_xml_event(handler),
        _ => cleanup_unexpected_phase_event(handler, ctx),
    }
}

fn cleanup_planning_phase_xml_event(handler: &MainEffectHandler) -> EffectResult {
    EffectResult::event(PipelineEvent::Planning(PlanningEvent::PlanXmlCleaned {
        iteration: handler.state.iteration,
    }))
}

fn cleanup_development_phase_xml_event(handler: &MainEffectHandler) -> EffectResult {
    EffectResult::event(PipelineEvent::Development(DevelopmentEvent::XmlCleaned {
        iteration: handler.state.iteration,
    }))
}

fn cleanup_commit_phase_xml_event(handler: &MainEffectHandler) -> EffectResult {
    let attempt = match &handler.state.commit {
        crate::reducer::state::CommitState::Generating { attempt, .. } => *attempt,
        _ => 1,
    };
    EffectResult::event(PipelineEvent::Commit(CommitEvent::CommitXmlCleaned {
        attempt,
    }))
}

fn cleanup_unexpected_phase_event(
    handler: &MainEffectHandler,
    ctx: &PhaseContext<'_>,
) -> EffectResult {
    ctx.logger.warn(&format!(
        "CleanupRequiredFiles emitted in unexpected phase: {:?}",
        handler.state.phase
    ));
    EffectResult::event(PipelineEvent::context_cleaned())
}

fn cleanup_review_phase_event(handler: &MainEffectHandler) -> EffectResult {
    // Cleanup routing in review phase is drain-owned. The review flag tracks
    // discovered issues, but explicit fix continuation/retry flows can keep the
    // fix drain active even after that compatibility flag is cleared.
    let pass = handler.state.reviewer_pass;
    if handler.state.runtime_drain() == AgentDrain::Fix {
        EffectResult::event(PipelineEvent::Review(ReviewEvent::FixResultXmlCleaned {
            pass,
        }))
    } else {
        EffectResult::event(PipelineEvent::Review(ReviewEvent::IssuesXmlCleaned {
            pass,
        }))
    }
}

fn cleanup_continuation_context_file(ctx: &PhaseContext<'_>) -> anyhow::Result<()> {
    let path = Path::new(".agent/tmp/continuation_context.md");
    if ctx.workspace.exists(path) {
        ctx.workspace
            .remove(path)
            .map_err(|err| ErrorEvent::WorkspaceRemoveFailed {
                path: path.display().to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;
    }
    Ok(())
}

/// Check if a gitignore pattern exists in the content.
///
/// Matches exact pattern on its own line (ignoring comments and whitespace).
fn entry_exists(content: &str, pattern: &str) -> bool {
    content
        .lines()
        .map(str::trim)
        .filter(|line| !line.starts_with('#') && !line.is_empty())
        .any(|line| line == pattern)
}
