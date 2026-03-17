// Display summary functions for checkpoint resume.
// This module handles user-friendly summary output for checkpoint information.

fn log_checkpoint_header_and_elapsed(checkpoint: &PipelineCheckpoint, logger: &Logger) -> bool {
    use chrono::Local;

    // Display phase with stable indicator (ASCII only)
    let phase_indicator = get_phase_indicator(checkpoint.phase);
    logger.info(&format!("{} {}", phase_indicator, checkpoint.description()));

    // Calculate and display time elapsed
    // Parse the timestamp string which is in "YYYY-MM-DD HH:MM:SS" format
    let Some(checkpoint_time) = parse_checkpoint_timestamp_as_local(&checkpoint.timestamp) else {
        // If parsing fails, just show the timestamp string
        logger.info(&format!(
            "Session was interrupted at: {}",
            checkpoint.timestamp
        ));
        return false;
    };
    let now = Local::now();
    let duration = now.signed_duration_since(checkpoint_time);

    logger.info(&format!(
        "Session was interrupted: {}",
        format_time_ago(duration)
    ));
    true
}

fn log_rebase_conflict_summary(checkpoint: &PipelineCheckpoint, logger: &Logger) {
    if !matches!(
        checkpoint.rebase_state,
        crate::checkpoint::RebaseState::HasConflicts { .. }
    ) {
        return;
    }

    if let crate::checkpoint::RebaseState::HasConflicts { files } = &checkpoint.rebase_state {
        logger.warn(&format!(
            "Rebase conflicts detected in {} file(s)",
            files.len()
        ));
        // Show up to 5 conflicted files
        let display_files: Vec<_> = files.iter().take(5).cloned().collect();
        for file in display_files {
            logger.info(&format!("  - {file}"));
        }
        if files.len() > 5 {
            logger.info(&format!(
                "  ... and {} more",
                files.len().saturating_sub(5)
            ));
        }
    }
}

fn log_progress_summary(checkpoint: &PipelineCheckpoint, logger: &Logger) {
    // Show progress with visual bar
    if checkpoint.total_iterations > 0 {
        let progress_bar = create_progress_bar(
            checkpoint.actual_developer_runs,
            checkpoint.total_iterations,
        );
        logger.info(&format!(
            "Development: {} {}/{} completed",
            progress_bar, checkpoint.actual_developer_runs, checkpoint.total_iterations
        ));
    }
    if checkpoint.total_reviewer_passes > 0 {
        let progress_bar = create_progress_bar(
            checkpoint.actual_reviewer_runs,
            checkpoint.total_reviewer_passes,
        );
        logger.info(&format!(
            "Review: {} {}/{} completed",
            progress_bar, checkpoint.actual_reviewer_runs, checkpoint.total_reviewer_passes
        ));
    }
}

fn log_agent_and_command_summary(checkpoint: &PipelineCheckpoint, logger: &Logger) {
    // Show resume count if this is a resumed session
    if checkpoint.resume_count > 0 {
        logger.info(&format!(
            "This session has been resumed {} time(s) before",
            checkpoint.resume_count
        ));
    }

    // Show the reconstructed command that was used
    if let Some(reconstructed_command) = reconstruct_command(checkpoint) {
        logger.info(&format!("Original command: {reconstructed_command}"));
    }

    // Show agent configuration details
    logger.info(&format!("Developer agent: {}", checkpoint.developer_agent));
    logger.info(&format!("Reviewer agent: {}", checkpoint.reviewer_agent));

    // Show model overrides if present
    if let Some(ref model) = checkpoint.developer_agent_config.model_override {
        logger.info(&format!("Developer model: {model}"));
    }
    if let Some(ref model) = checkpoint.reviewer_agent_config.model_override {
        logger.info(&format!("Reviewer model: {model}"));
    }

    // Show provider overrides if present
    if let Some(ref provider) = checkpoint.developer_agent_config.provider_override {
        logger.info(&format!("Developer provider: {provider}"));
    }
    if let Some(ref provider) = checkpoint.reviewer_agent_config.provider_override {
        logger.info(&format!("Reviewer provider: {provider}"));
    }
}

fn log_recent_activity_step(step: &crate::checkpoint::execution_history::ExecutionStep, logger: &Logger) {
    // ASCII-only outcome markers (stable across non-UTF8 terminals)
    let outcome_marker = outcome_marker_ascii(&step.outcome);

    logger.info(&format!(
        "  {:<4} {} ({})",
        outcome_marker, step.step_type, step.phase
    ));

    // Add files modified count if available
    if let Some(ref detail) = step.modified_files_detail {
        let added_count = detail.added.as_ref().map_or(0, |v| v.len());
        let modified_count = detail.modified.as_ref().map_or(0, |v| v.len());
        let deleted_count = detail.deleted.as_ref().map_or(0, |v| v.len());
        let total_files = added_count
            .saturating_add(modified_count)
            .saturating_add(deleted_count);
        if total_files > 0 {
            let mut file_summary = String::from("    Files: ");
            let mut parts = Vec::new();
            if added_count > 0 {
                parts.push(format!("{added_count} added"));
            }
            if modified_count > 0 {
                parts.push(format!("{modified_count} modified"));
            }
            if deleted_count > 0 {
                parts.push(format!("{deleted_count} deleted"));
            }
            file_summary.push_str(&parts.join(", "));
            logger.info(&file_summary);
        }
    }

    // Add issues summary if available
    if let Some(ref issues) = step.issues_summary {
        if issues.found > 0 || issues.fixed > 0 {
            logger.info(&format!(
                "    Issues: {} found, {} fixed",
                issues.found, issues.fixed
            ));
        }
    }

    // Add git commit if available (shortened)
    if let Some(ref oid) = step.git_commit_oid {
        let short_oid = if oid.len() > SHORT_OID_LENGTH {
            &oid[..SHORT_OID_LENGTH]
        } else {
            oid
        };
        logger.info(&format!("    Commit: {short_oid}"));
    }
}

fn log_recent_activity(history: &crate::checkpoint::execution_history::ExecutionHistory, logger: &Logger) {
    if history.steps.is_empty() {
        return;
    }

    logger.info(&format!(
        "Execution history: {} step(s) recorded",
        history.steps.len()
    ));

    // Show recent activity (last 5 steps) with user-friendly details
    let recent_steps: Vec<_> = history
        .steps
        .iter()
        .rev()
        .take(5)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();

    logger.info("");
    logger.info("Recent Activity:");

    for step in &recent_steps {
        log_recent_activity_step(step, logger);
    }
}

fn log_resume_next_steps(checkpoint: &PipelineCheckpoint, logger: &Logger) {
    // Show helpful next step based on current phase
    let next_step = suggest_next_step(checkpoint);
    logger.info("");
    logger.info(&format!("Next: {next_step}"));

    // Show example commands for inspecting state
    logger.info("");
    logger.info("To inspect the current state, you can run:");
    logger.info("  git status        - See current changes");
    logger.info("  git log --oneline -5 - See recent commits");
}

/// Display a user-friendly checkpoint summary with time elapsed.
fn display_user_friendly_checkpoint_summary(checkpoint: &PipelineCheckpoint, logger: &Logger) {
    if !log_checkpoint_header_and_elapsed(checkpoint, logger) {
        return;
    }

    log_rebase_conflict_summary(checkpoint, logger);
    log_progress_summary(checkpoint, logger);
    log_agent_and_command_summary(checkpoint, logger);

    if let Some(ref history) = checkpoint.execution_history {
        log_recent_activity(history, logger);
    }

    log_resume_next_steps(checkpoint, logger);
}

/// Display a summary of the checkpoint being loaded.
fn display_checkpoint_summary(checkpoint: &PipelineCheckpoint, logger: &Logger) {
    logger.info(&format!("Resuming from: {}", checkpoint.description()));
    logger.info(&format!("Checkpoint saved at: {}", checkpoint.timestamp));
    logger.info(&format!("Checkpoint version: {}", checkpoint.version));

    // Show run ID and resume count
    logger.info(&format!("Run ID: {}", checkpoint.run_id));
    if checkpoint.resume_count > 0 {
        logger.info(&format!(
            "Resume count: {} (this is resume #{} of this session)",
            checkpoint.resume_count,
            checkpoint.resume_count.saturating_add(1)
        ));
    }
    if let Some(ref parent_id) = checkpoint.parent_run_id {
        logger.info(&format!("Parent run ID: {parent_id}"));
    }

    // Show actual execution counts vs configured counts
    logger.info(&format!(
        "Development: {} iteration(s) configured, {} completed",
        checkpoint.total_iterations, checkpoint.actual_developer_runs
    ));
    logger.info(&format!(
        "Review: {} pass(es) configured, {} completed",
        checkpoint.total_reviewer_passes, checkpoint.actual_reviewer_runs
    ));

    // Show iteration progress
    if checkpoint.total_iterations > 0 {
        logger.info(&format!(
            "Current position: iteration {}/{}",
            checkpoint.iteration, checkpoint.total_iterations
        ));
    }
    if checkpoint.total_reviewer_passes > 0 {
        logger.info(&format!(
            "Current position: pass {}/{}",
            checkpoint.reviewer_pass, checkpoint.total_reviewer_passes
        ));
    }

    // Show CLI args if available
    let cli = &checkpoint.cli_args;
    if cli.developer_iters > 0 || cli.reviewer_reviews > 0 {
        logger.info(&format!(
            "Original config: -D {} -R {}",
            cli.developer_iters, cli.reviewer_reviews
        ));
    }

    // Show agent configs
    logger.info(&format!("Developer agent: {}", checkpoint.developer_agent));
    logger.info(&format!("Reviewer agent: {}", checkpoint.reviewer_agent));

    // Show model overrides if present
    if let Some(ref model) = checkpoint.developer_agent_config.model_override {
        logger.info(&format!("Developer model override: {model}"));
    }
    if let Some(ref model) = checkpoint.reviewer_agent_config.model_override {
        logger.info(&format!("Reviewer model override: {model}"));
    }

    // Show provider overrides if present
    if let Some(ref provider) = checkpoint.developer_agent_config.provider_override {
        logger.info(&format!("Developer provider: {provider}"));
    }
    if let Some(ref provider) = checkpoint.reviewer_agent_config.provider_override {
        logger.info(&format!("Reviewer provider: {provider}"));
    }

    // Show rebase state if applicable
    match &checkpoint.rebase_state {
        crate::checkpoint::RebaseState::PreRebaseInProgress { upstream_branch } => {
            logger.warn(&format!("Pre-rebase in progress to: {upstream_branch}"));
        }
        crate::checkpoint::RebaseState::HasConflicts { files } => {
            logger.warn(&format!("Rebase has conflicts in {} files", files.len()));
            for file in files.iter().take(3) {
                logger.warn(&format!("  - {file}"));
            }
            if files.len() > 3 {
                logger.warn(&format!(
                    "  ... and {} more",
                    files.len().saturating_sub(3)
                ));
            }
        }
        _ => {}
    }
}
