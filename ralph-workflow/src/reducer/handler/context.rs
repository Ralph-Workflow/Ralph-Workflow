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
        // Delete all specified files
        files.iter().for_each(|file_path| {
            let path = Path::new(file_path);
            if ctx.workspace.exists(path) {
                let _ = ctx.workspace.remove_if_exists(path);
            }
        });

        // Emit the appropriate event based on current phase
        match self.state.phase {
            PipelinePhase::Planning => {
                let iteration = self.state.iteration;
                EffectResult::event(PipelineEvent::Planning(PlanningEvent::PlanXmlCleaned {
                    iteration,
                }))
            }
            PipelinePhase::Development => {
                let iteration = self.state.iteration;
                EffectResult::event(PipelineEvent::Development(DevelopmentEvent::XmlCleaned {
                    iteration,
                }))
            }
            PipelinePhase::Review => {
                // Cleanup routing in review phase is drain-owned. The review flag tracks
                // discovered issues, but explicit fix continuation/retry flows can keep the
                // fix drain active even after that compatibility flag is cleared.
                let pass = self.state.reviewer_pass;
                if self.state.runtime_drain() == AgentDrain::Fix {
                    EffectResult::event(PipelineEvent::Review(ReviewEvent::FixResultXmlCleaned {
                        pass,
                    }))
                } else {
                    EffectResult::event(PipelineEvent::Review(ReviewEvent::IssuesXmlCleaned {
                        pass,
                    }))
                }
            }
            PipelinePhase::CommitMessage => {
                // Get current attempt from commit state
                let attempt = match &self.state.commit {
                    crate::reducer::state::CommitState::Generating { attempt, .. } => *attempt,
                    _ => 1,
                };
                EffectResult::event(PipelineEvent::Commit(CommitEvent::CommitXmlCleaned {
                    attempt,
                }))
            }
            _ => {
                // Fallback: should not happen in normal operation
                ctx.logger.warn(&format!(
                    "CleanupRequiredFiles emitted in unexpected phase: {:?}",
                    self.state.phase
                ));
                // Return a generic event to avoid crashing
                EffectResult::event(PipelineEvent::context_cleaned())
            }
        }
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

        let mut cleaned_count: usize = 0;

        // Delete PLAN.md via workspace
        let plan_path = Path::new(".agent/PLAN.md");
        if ctx.workspace.exists(plan_path) {
            ctx.workspace
                .remove(plan_path)
                .map_err(|err| ErrorEvent::WorkspaceRemoveFailed {
                    path: plan_path.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                })?;
            cleaned_count = cleaned_count.saturating_add(1);
        }

        // Delete ISSUES.md (may not exist if in isolation mode) via workspace
        let issues_path = Path::new(".agent/ISSUES.md");
        if ctx.workspace.exists(issues_path) {
            ctx.workspace
                .remove(issues_path)
                .map_err(|err| ErrorEvent::WorkspaceRemoveFailed {
                    path: issues_path.display().to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                })?;
            cleaned_count = cleaned_count.saturating_add(1);
        }

        // Delete ALL .xml files in .agent/tmp/ to prevent context pollution via workspace
        let tmp_dir = Path::new(".agent/tmp");
        if ctx.workspace.exists(tmp_dir) {
            let entries =
                ctx.workspace
                    .read_dir(tmp_dir)
                    .map_err(|err| ErrorEvent::WorkspaceReadFailed {
                        path: tmp_dir.display().to_string(),
                        kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                    })?;

            for entry in entries {
                let path = entry.path();
                if path.extension().and_then(|s| s.to_str()) == Some("xml") {
                    ctx.workspace.remove(path).map_err(|err| {
                        ErrorEvent::WorkspaceRemoveFailed {
                            path: path.display().to_string(),
                            kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                        }
                    })?;
                    cleaned_count = cleaned_count.saturating_add(1);
                }
            }
        }

        // Delete continuation context file (if present) via workspace
        cleanup_continuation_context_file(ctx)?;

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

        let event = PipelineEvent::prompt_permissions_restored();
        let warning = make_prompt_writable_with_workspace(ctx.workspace);

        if let Some(ref msg) = warning {
            ctx.logger.warn(msg);
        }

        let result = EffectResult::event(event).maybe_with_additional_event(
            warning
                .as_ref()
                .map(|msg| PipelineEvent::prompt_permissions_restore_warning(msg.clone())),
        );

        if self.state.phase == PipelinePhase::Finalizing {
            return result.with_ui_event(self.phase_transition_ui(PipelinePhase::Complete));
        }

        result
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

        // Capture file_created status BEFORE any file operations to avoid race condition
        let file_created = !ctx.workspace.exists(gitignore_path);

        // Read existing .gitignore content (or empty string if doesn't exist)
        let existing_content = if ctx.workspace.exists(gitignore_path) {
            ctx.workspace
                .read(gitignore_path)
                .unwrap_or_else(|_| String::new())
        } else {
            String::new()
        };

        let (already_present, entries_added): (Vec<_>, Vec<_>) =
            required_entries.partition(|pattern| entry_exists(&existing_content, pattern));

        let entries_added: Vec<String> = entries_added.into_iter().map(String::from).collect();

        if entries_added.is_empty() {
            ctx.logger
                .info("All required .gitignore entries already present");
        } else {
            let suffix = if existing_content.is_empty() || existing_content.ends_with('\n') {
                String::new()
            } else {
                "\n".to_string()
            };
            let entries_str = format!(
                "# Ralph-workflow artifacts (auto-generated)\n{}\n",
                entries_added.join("\n")
            );
            let new_content = format!("{existing_content}{suffix}{entries_str}");

            let written = ctx.workspace.write(gitignore_path, &new_content).is_ok();

            if written {
                ctx.logger.success(&format!(
                    "Added {} entries to .gitignore: {}",
                    entries_added.len(),
                    entries_added.join(", ")
                ));
            } else {
                ctx.logger
                    .warn(&format!("Failed to write .gitignore (continuing anyway)"));
            }
        }

        EffectResult::event(PipelineEvent::gitignore_entries_ensured(
            entries_added,
            already_present,
            file_created,
        ))
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
