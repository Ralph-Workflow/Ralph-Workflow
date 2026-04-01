//! Plumbing commands for low-level git operations.
//!
//! This module handles plumbing commands that operate directly on
//! commit messages and git state without running the full pipeline:
//! - `--show-commit-msg`: Display the stored commit message
//! - `--apply-commit`: Stage and commit using the stored message
//! - `--generate-commit-msg`: Generate a commit message for staged changes
//!
//! # Workspace Support
//!
//! Plumbing commands have two variants:
//! - Direct functions (e.g., `handle_show_commit_msg`) - use real filesystem
//! - Workspace-aware functions (e.g., `handle_show_commit_msg_with_workspace`) - use injected workspace
//!
//! Tests should use the workspace-aware variants with `MemoryWorkspace` for isolation.

use crate::agents::{AgentDrain, AgentRegistry};
use crate::app::effect::{AppEffect, AppEffectHandler, AppEffectResult};
use crate::config::Config;
use crate::files::{
    delete_commit_message_file_with_workspace, read_commit_message_file_with_workspace,
    write_commit_message_file_with_workspace,
};
use crate::logger::Colors;
use crate::logger::Logger;

use crate::prompts::TemplateContext;
use crate::workspace::Workspace;
use crate::ProcessExecutor;
use std::sync::Arc;

/// Configuration for commit message generation in plumbing commands.
///
/// Groups related parameters for `handle_generate_commit_msg` to avoid
/// excessive function arguments.
pub struct CommitGenerationConfig<'a> {
    /// The pipeline configuration.
    pub config: &'a Config,
    /// Template context for prompt expansion.
    pub template_context: &'a TemplateContext,
    /// Workspace for file operations (trait object for DI).
    pub workspace: &'a dyn crate::workspace::Workspace,
    /// Arc-wrapped workspace for spawning into threads.
    pub workspace_arc: Arc<dyn crate::workspace::Workspace>,
    /// Agent registry for accessing configured agents.
    pub registry: &'a AgentRegistry,
    /// Logger for info/warning messages.
    pub logger: &'a Logger,
    /// Color configuration for output.
    pub colors: Colors,
    /// Name of the developer agent to use for commit generation.
    pub developer_agent: &'a str,
    /// Name of the reviewer agent (not used, kept for API compatibility).
    pub reviewer_agent: &'a str,
    /// Process executor for external command execution.
    pub executor: Arc<dyn ProcessExecutor>,
}

fn resolve_commit_message_agents(registry: &AgentRegistry, reviewer_agent: &str) -> Vec<String> {
    if let Some(commit_binding) = registry.resolved_drain(AgentDrain::Commit) {
        return commit_binding.agents.clone();
    }

    let review_chain = registry
        .resolved_drain(AgentDrain::Review)
        .map_or(&[] as &[String], |binding| binding.agents.as_slice());
    if !review_chain.is_empty() {
        return review_chain.to_vec();
    }

    vec![reviewer_agent.to_string()]
}

#[cfg(any(test, feature = "test-utils"))]
#[must_use]
pub fn resolve_commit_message_agents_for_testing(
    config: &CommitGenerationConfig<'_>,
) -> Vec<String> {
    resolve_commit_message_agents(config.registry, config.reviewer_agent)
}

/// Handles the `--show-commit-msg` command using workspace abstraction.
///
/// This is a testable version that uses `Workspace` for file I/O,
/// enabling tests to use `MemoryWorkspace` for isolation.
///
/// # Arguments
///
/// * `workspace` - The workspace to read from
///
/// # Returns
///
/// Returns the commit message string on success.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn get_commit_message_from_workspace(workspace: &dyn Workspace) -> anyhow::Result<String> {
    read_commit_message_file_with_workspace(workspace).map_err(anyhow::Error::from)
}

/// Handles the `--apply-commit` command using effect handler abstraction.
///
/// This is a testable version that uses `AppEffectHandler` for git operations
/// and `Workspace` for file I/O, enabling tests to use mocks for isolation.
///
/// # Arguments
///
/// * `workspace` - The workspace for file operations
/// * `handler` - The effect handler for git operations
/// * `logger` - Logger for info/warning messages
/// * `colors` - Color configuration for output
///
/// # Returns
///
/// Returns `Ok(())` on success or an error if commit fails.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_apply_commit_with_handler<H: AppEffectHandler>(
    workspace: &dyn Workspace,
    handler: &mut H,
    logger: &Logger,
    colors: Colors,
) -> anyhow::Result<()> {
    let commit_msg = read_commit_message_file_with_workspace(workspace)?;

    logger.info("Staging all changes...");

    // Stage all changes via effect
    // Mock returns Bool(true) to indicate staged changes exist, production returns Ok
    match handler.execute(AppEffect::GitAddAll) {
        AppEffectResult::Ok | AppEffectResult::Bool(true | false) => {
            // No changes to stage if Bool(false)
        }
        AppEffectResult::Error(e) => anyhow::bail!("Failed to stage changes: {e}"),
        other => anyhow::bail!("Unexpected result from GitAddAll: {other:?}"),
    }

    logger.info(&format!(
        "Commit message: {}{}{}",
        colors.cyan(),
        commit_msg,
        colors.reset()
    ));

    logger.info("Creating commit...");

    // Create commit via effect
    // Note: Plumbing commands don't have access to config, so we use None
    // for git identity, falling back to git config (via repo.signature())
    match handler.execute(AppEffect::GitCommit {
        message: commit_msg,
        user_name: None,
        user_email: None,
    }) {
        AppEffectResult::String(oid)
        | AppEffectResult::Commit(crate::app::effect::CommitResult::Success(oid)) => {
            logger.success(&format!("Commit created successfully: {oid}"));
            // Clean up the commit message file
            if let Err(err) = delete_commit_message_file_with_workspace(workspace) {
                logger.warn(&format!("Failed to delete commit-message.txt: {err}"));
            }
            Ok(())
        }
        AppEffectResult::Commit(crate::app::effect::CommitResult::NoChanges)
        | AppEffectResult::Ok => {
            // No changes to commit (clean working tree)
            logger.warn("Nothing to commit (working tree clean)");
            Ok(())
        }
        AppEffectResult::Error(e) => anyhow::bail!("Failed to create commit: {e}"),
        other => anyhow::bail!("Unexpected result from GitCommit: {other:?}"),
    }
}

/// Handles the `--generate-commit-msg` command.
///
/// Generates a commit message for current changes using the standard pipeline.
/// Uses the same `generate_commit_message()` function as the main workflow,
/// ensuring consistent behavior with reducer-driven validation.
///
/// # Arguments
///
/// * `config` - The pipeline configuration
/// * `registry` - The agent registry
/// * `logger` - Logger for info/warning messages
/// * `colors` - Color configuration for output
/// * `developer_agent` - Name of the developer agent to use (for commit generation)
/// * `_reviewer_agent` - Name of the reviewer agent (not used, kept for API compatibility)
///
/// # Returns
///
/// Returns `Ok(())` on success or an error if generation fails.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn handle_generate_commit_msg(
    config: &CommitGenerationConfig<'_>,
    app_handler: &mut dyn AppEffectHandler,
) -> anyhow::Result<()> {
    config.logger.info("Generating commit message...");

    // Generate the commit message using standard pipeline
    let diff = match app_handler.execute(AppEffect::GitDiff) {
        AppEffectResult::String(diff) => diff,
        AppEffectResult::Error(err) => {
            anyhow::bail!("Failed to get git diff: {err}");
        }
        other => anyhow::bail!("Unexpected result from GitDiff: {other:?}"),
    };
    if diff.trim().is_empty() {
        config
            .logger
            .warn("No changes detected to generate a commit message for");
        anyhow::bail!("No changes to commit");
    }

    let agents = resolve_commit_message_agents(config.registry, config.reviewer_agent);

    // Use the chain-aware commit message generation from phases/commit.rs.
    let result = crate::app::plumbing_boundary::generate_commit_message_for_plumbing(
        config, &diff, &agents,
    )?;
    let commit_message = match result.outcome {
        crate::phases::commit::CommitMessageOutcome::Message(message) => message,
        crate::phases::commit::CommitMessageOutcome::Skipped { reason } => {
            config.logger.warn(&format!(
                "No commit needed (agent requested skip): {reason}"
            ));
            return Ok(());
        }
    };

    config.logger.success("Commit message generated:");
    config.logger.info(&format!(
        "{}{}{}",
        config.colors.cyan(),
        commit_message,
        config.colors.reset()
    ));

    // Write the message to file for use with --apply-commit (using workspace)
    write_commit_message_file_with_workspace(config.workspace, &commit_message)?;

    config
        .logger
        .info("Message saved to .agent/commit-message.txt");
    config
        .logger
        .info("Run 'ralph --apply-commit' to create the commit");

    Ok(())
}
