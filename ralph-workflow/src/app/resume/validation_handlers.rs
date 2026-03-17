// Validation and handling for resume from checkpoint.
// This module handles the --resume flag and validation logic.

/// Loads and prints checkpoint inspection output without requiring pipeline setup.
///
/// # Errors
///
/// Returns an error if no checkpoint exists or checkpoint loading fails.
pub fn inspect_checkpoint(workspace: &dyn Workspace, logger: &Logger) -> anyhow::Result<()> {
    match load_checkpoint_with_workspace(workspace) {
        Ok(Some(checkpoint)) => {
            logger.header("CHECKPOINT INSPECTION", crate::logger::Colors::cyan);
            display_detailed_checkpoint_info(&checkpoint, logger);
            Ok(())
        }
        Ok(None) => anyhow::bail!("No checkpoint found to inspect."),
        Err(e) => anyhow::bail!("Failed to load checkpoint: {e}"),
    }
}

/// Handles the --resume flag and loads checkpoint if applicable.
///
/// This function loads and validates the checkpoint, providing detailed
/// feedback about what state is being restored and any configuration changes.
///
/// # Arguments
///
/// * `args` - CLI arguments
/// * `config` - Current configuration (for validation comparison)
/// * `registry` - Agent registry (for agent validation)
/// * `logger` - Logger for output
/// * `developer_agent` - Current developer agent name
/// * `reviewer_agent` - Current reviewer agent name
/// * `workspace` - Workspace for explicit file operations
///
/// # Returns
///
/// `Ok(Some(ResumeResult))` if a valid checkpoint was found and loaded,
/// `Ok(None)` if no checkpoint exists or --resume was not specified.
/// `Err(_)` if --resume was specified and checkpoint validation failed.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_resume_with_validation(
    args: &crate::cli::Args,
    config: &Config,
    registry: &AgentRegistry,
    logger: &Logger,
    developer_agent: &str,
    reviewer_agent: &str,
    workspace: &dyn Workspace,
) -> anyhow::Result<Option<ResumeResult>> {
    // Handle --inspect-checkpoint flag
    if args.recovery.inspect_checkpoint {
        match inspect_checkpoint(workspace, logger) {
            Ok(()) => std::process::exit(0),
            Err(err) => {
                logger.error(&err.to_string());
                std::process::exit(1);
            }
        }
    }

    if !args.recovery.resume {
        return Ok(None);
    }

    match load_checkpoint_with_workspace(workspace) {
        Ok(Some(checkpoint)) => {
            logger.header("RESUME: Loading Checkpoint", crate::logger::Colors::yellow);
            display_checkpoint_summary(&checkpoint, logger);

            // Validate checkpoint
            let validation = validate_checkpoint(&checkpoint, config, registry, workspace);

            // Display validation results
            validation.warnings.iter().for_each(|warning| {
                logger.warn(warning);
            });
            validation.errors.iter().for_each(|error| {
                logger.error(error);
            });

            if !validation.is_valid {
                // When --resume is explicitly specified and validation fails, return an error.
                // The user explicitly asked to resume, so failing validation is a hard error.
                logger.error("Checkpoint validation failed. Cannot resume.");
                logger.info(
                    "Delete .agent/checkpoint.json and start fresh, or fix the issues above.",
                );
                return Err(anyhow::anyhow!(
                    "Checkpoint validation failed: {}",
                    validation.errors.join("; ")
                ));
            }

            // Verify agents match (additional agent-specific warnings)
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

            // Check for in-progress git rebase
            check_rebase_state_on_resume(&checkpoint, logger);

            // Perform file system state validation
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

            if let ValidationOutcome::Failed(reason) = validation_outcome {
                return Err(anyhow::anyhow!(
                    "File system state validation failed: {reason}"
                ));
            }

            Ok(Some(ResumeResult { checkpoint }))
        }
        Ok(None) => {
            logger.warn("No checkpoint found. Starting fresh pipeline...");
            Ok(None)
        }
        Err(e) => {
            // When --resume is specified but checkpoint fails to load, that's an error
            Err(anyhow::anyhow!("Failed to load checkpoint: {e}"))
        }
    }
}
