// Interactive resume prompts for checkpoint recovery.
// This module handles user interaction for resume decisions.

/// Offer interactive resume prompt when checkpoint exists without --resume flag.
///
/// This function checks if a checkpoint exists when the user did NOT specify
/// the --resume flag, and if so, offers to resume via an interactive prompt.
/// This provides a better user experience by detecting incomplete runs and
/// offering to continue them.
///
/// # Arguments
///
/// * `args` - CLI arguments (to check if --resume was already specified)
/// * `config` - Current configuration (for validation comparison)
/// * `registry` - Agent registry (for agent validation)
/// * `logger` - Logger for output
/// * `developer_agent` - Current developer agent name
/// * `reviewer_agent` - Current reviewer agent name
/// * `workspace` - Workspace for explicit file operations
///
/// # Returns
///
/// `Some(ResumeResult)` if user chose to resume from a valid checkpoint,
/// `None` if no checkpoint exists, not in a TTY, user declined, or validation failed.
pub fn offer_resume_if_checkpoint_exists(
    args: &crate::cli::Args,
    config: &Config,
    registry: &AgentRegistry,
    logger: &Logger,
    developer_agent: &str,
    reviewer_agent: &str,
    workspace: &dyn Workspace,
) -> Option<ResumeResult> {
    // Skip if --resume flag was already specified (handled by handle_resume_with_validation)
    if args.recovery.resume {
        return None;
    }

    // Skip if --no-resume flag is specified
    if args.recovery.no_resume {
        return None;
    }

    // Skip if RALPH_NO_RESUME_PROMPT env var is set (for CI/automation)
    if crate::app::io::env_access::check_no_resume_prompt() {
        return None;
    }

    // Skip if not in a TTY (can't prompt user)
    if !can_prompt_user() {
        return None;
    }

    // Check if checkpoint exists
    if !checkpoint_exists_with_workspace(workspace) {
        return None;
    }

    // Load checkpoint to display summary
    let checkpoint = match load_checkpoint_with_workspace(workspace) {
        Ok(Some(cp)) => cp,
        Ok(None) => return None,
        Err(e) => {
            logger.warn(&format!("Checkpoint exists but failed to load: {e}"));
            return None;
        }
    };

    // Display user-friendly checkpoint summary with time elapsed
    logger.header("FOUND PREVIOUS RUN", crate::logger::Colors::cyan);
    display_user_friendly_checkpoint_summary(&checkpoint, logger);

    // Prompt user to resume
    if !prompt_user_to_resume(logger) {
        // User declined - delete checkpoint and start fresh
        logger.info("Deleting checkpoint and starting fresh...");
        let _ = crate::checkpoint::clear_checkpoint_with_workspace(workspace);
        return None;
    }

    // User chose to resume - validate and proceed
    logger.header("RESUME: Loading Checkpoint", crate::logger::Colors::yellow);

    let validation = validate_checkpoint(&checkpoint, config, registry, workspace);

    validation.warnings.iter().for_each(|warning| {
        logger.warn(warning);
    });
    validation.errors.iter().for_each(|error| {
        logger.error(error);
    });

    if !validation.is_valid {
        logger.error("Checkpoint validation failed. Cannot resume.");
        logger.info("Delete .agent/checkpoint.json and start fresh, or fix the issues above.");
        return None;
    }

    if checkpoint.developer_agent != developer_agent {
        logger.warn(&format!(
            "Developer agent changed: {} -> {}",
            checkpoint.developer_agent, developer_agent
        ));
    }
    if checkpoint.reviewer_agent != reviewer_agent {
        logger.warn(&format!(
            "Reviewer agent changed: {} -> {}",
            checkpoint.reviewer_agent, reviewer_agent
        ));
    }

    check_rebase_state_on_resume(&checkpoint, logger);

    let validation_outcome = checkpoint.file_system_state.as_ref().map_or(
        ValidationOutcome::Passed,
        |file_system_state| {
            validate_file_system_state(
                file_system_state,
                logger,
                args.recovery.recovery_strategy.into(),
                workspace,
            )
        },
    );

    if matches!(validation_outcome, ValidationOutcome::Failed(_)) {
        return None;
    }

    Some(ResumeResult { checkpoint })
}

/// Check if we can prompt the user (stdin/stdout is a TTY).
fn can_prompt_user() -> bool {
    crate::app::io::env_access::is_terminal_io()
}

/// Prompt user to decide whether to resume or start fresh.
///
/// Returns `true` if user wants to resume, `false` if they want to start fresh.
fn prompt_user_to_resume(logger: &Logger) -> bool {
    crate::app::io::terminal::prompt_yes_no(
        logger,
        "Would you like to resume from where you left off?",
    )
}
