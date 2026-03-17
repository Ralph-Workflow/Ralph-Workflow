// Checkpoint validation logic for resume functionality.
// This module handles verifying checkpoint integrity and file system state validation.

/// Result of file system validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ValidationOutcome {
    /// Validation passed, safe to resume
    Passed,
    /// Validation failed, cannot resume
    Failed(String),
}

/// Validate file system state when resuming.
///
/// This function validates that the current file system state matches
/// the state captured in the checkpoint. This is part of the hardened
/// resume feature that ensures idempotent recovery.
///
/// Returns a `ValidationOutcome` indicating whether validation passed
/// or failed with a reason.
pub(crate) fn validate_file_system_state(
    file_system_state: &FileSystemState,
    logger: &Logger,
    strategy: crate::checkpoint::recovery::RecoveryStrategy,
    workspace: &dyn Workspace,
) -> ValidationOutcome {
    let errors = file_system_state.validate_with_workspace(workspace, None);

    if errors.is_empty() {
        logger.info("File system state validation passed.");
        return ValidationOutcome::Passed;
    }

    logger.warn("File system state validation detected changes:");

    errors.iter().for_each(|error| {
        let (problem, commands) = error.recovery_commands();
        logger.warn(&format!("  - {error}"));
        logger.info(&format!("    What's wrong: {problem}"));
        logger.info("    How to fix:");
        commands.iter().for_each(|cmd| {
            logger.info(&format!("      {cmd}"));
        });
    });

    // Handle based on the recovery strategy
    match strategy {
        crate::checkpoint::recovery::RecoveryStrategy::Fail => {
            logger.error("File system state validation failed (strategy: fail).");
            logger.info("Use --recovery-strategy=auto to attempt automatic recovery.");
            logger.info("Use --recovery-strategy=force to proceed anyway (not recommended).");
            ValidationOutcome::Failed(
                "File system state changed - see errors above or use --recovery-strategy=force to proceed anyway".to_string()
            )
        }
        crate::checkpoint::recovery::RecoveryStrategy::Force => {
            logger.warn("Proceeding with resume despite file changes (strategy: force).");
            logger.info("Note: Pipeline behavior may be unpredictable.");
            ValidationOutcome::Passed
        }
        crate::checkpoint::recovery::RecoveryStrategy::Auto => {
            // Attempt automatic recovery for recoverable errors
            let (_recovered, remaining) =
                attempt_auto_recovery(file_system_state, &errors, logger, workspace);

            if remaining.is_empty() {
                logger.success("Automatic recovery completed successfully.");
            } else {
                logger.warn("Some issues could not be automatically recovered:");
                remaining.iter().for_each(|error| {
                    logger.warn(&format!("  - {error}"));
                });
                logger.warn("Proceeding with resume despite unrecovered issues (strategy: auto).");
                logger.info("Note: Pipeline behavior may be unpredictable.");
            }
            ValidationOutcome::Passed
        }
    }
}

/// Attempt automatic recovery from file system state changes.
///
/// This function attempts to automatically fix recoverable issues:
/// - Restores small files from content stored in snapshot
/// - Warns about unrecoverable issues (large files, git changes)
///
/// # Arguments
///
/// * `file_system_state` - The file system state from checkpoint
/// * `errors` - Validation errors that were detected
/// * `logger` - Logger for output
///
/// # Returns
///
/// A tuple of (number of issues recovered, remaining errors)
fn attempt_auto_recovery(
    file_system_state: &FileSystemState,
    errors: &[ValidationError],
    logger: &Logger,
    workspace: &dyn Workspace,
) -> (usize, Vec<ValidationError>) {
    let results: Vec<Result<(), String>> = errors
        .iter()
        .map(|error| attempt_recovery_for_error(file_system_state, error, logger, workspace))
        .collect();

    let recovered = results.iter().filter(|r| r.is_ok()).count();
    let remaining: Vec<ValidationError> = results
        .iter()
        .enumerate()
        .filter_map(|(i, r)| r.as_ref().err().map(|_| errors[i].clone()))
        .collect();

    errors
        .iter()
        .zip(results.iter())
        .for_each(|(error, result)| match result {
            Ok(()) => logger.success(&format!("Recovered: {error}")),
            Err(e) => logger.warn(&format!("Could not recover: {error} - {e}")),
        });

    (recovered, remaining)
}

/// Attempt to recover from a single validation error.
///
/// # Returns
///
/// `Ok(())` if recovery succeeded, `Err(reason)` if it failed.
fn attempt_recovery_for_error(
    file_system_state: &FileSystemState,
    error: &ValidationError,
    logger: &Logger,
    workspace: &dyn Workspace,
) -> Result<(), String> {
    match error {
        ValidationError::FileContentChanged { path } => {
            // Try to restore from snapshot if content is available
            if let Some(snapshot) = file_system_state.files.get(path) {
                if let Some(content) = snapshot.get_content() {
                    workspace
                        .write(Path::new(path), &content)
                        .map_err(|e| format!("Failed to write file: {e}"))?;
                    logger.info(&format!("Restored {path} from checkpoint content."));
                    return Ok(());
                }
            }
            Err("No content available in snapshot".to_string())
        }
        ValidationError::GitHeadChanged { .. } => {
            // Git state changes are not automatically recoverable
            // They require user intervention to reset or accept the new state
            Err("Git HEAD changes require manual intervention".to_string())
        }
        ValidationError::GitStateInvalid { .. } => {
            Err("Git state validation requires manual intervention".to_string())
        }
        ValidationError::GitWorkingTreeChanged { .. } => {
            // Working tree changes are not automatically recoverable
            Err("Git working tree changes require manual intervention".to_string())
        }
        ValidationError::FileMissing { path } => {
            // Can't recover a missing file unless we have content
            if let Some(snapshot) = file_system_state.files.get(path) {
                if let Some(content) = snapshot.get_content() {
                    workspace
                        .write(Path::new(path), &content)
                        .map_err(|e| format!("Failed to write file: {e}"))?;
                    logger.info(&format!("Restored missing {path} from checkpoint."));
                    return Ok(());
                }
            }
            Err(format!("Cannot recover missing file {path}"))
        }
        ValidationError::FileUnexpectedlyExists { path } => {
            // Unexpected files should be removed by user
            Err(format!(
                "File {path} should not exist - requires manual removal"
            ))
        }
    }
}

/// Check for in-progress git rebase when resuming.
///
/// This function detects if a git rebase is in progress and provides
/// appropriate guidance to the user.
pub(crate) fn check_rebase_state_on_resume(checkpoint: &PipelineCheckpoint, logger: &Logger) {
    // Only check for rebase if we're resuming from a rebase-related phase
    let is_rebase_phase = matches!(
        checkpoint.phase,
        PipelinePhase::PreRebase
            | PipelinePhase::PreRebaseConflict
            | PipelinePhase::PostRebase
            | PipelinePhase::PostRebaseConflict
    );

    if !is_rebase_phase {
        return;
    }

    match rebase_in_progress() {
        Ok(true) => {
            logger.warn("A git rebase is currently in progress.");
            logger.info("The checkpoint indicates you were in a rebase phase.");
            logger.info("Options:");
            logger.info("  - Continue: Let ralph complete the rebase process");
            logger.info("  - Abort manually: Run 'git rebase --abort' then use --resume");
        }
        Ok(false) => {
            // No rebase in progress - this is expected if rebase completed
            // but checkpoint wasn't cleared (e.g., pipeline was interrupted)
            logger.info("No git rebase is currently in progress.");
        }
        Err(e) => {
            logger.warn(&format!("Could not check rebase state: {e}"));
        }
    }
}
