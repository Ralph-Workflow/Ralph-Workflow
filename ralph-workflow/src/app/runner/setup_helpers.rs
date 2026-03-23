use crate::agents::AgentRegistry;
use crate::checkpoint::PipelinePhase;
use crate::cli::{create_prompt_from_template, prompt_template_selection};
use crate::logger::{Colors, Logger};
use crate::workspace::Workspace;

use super::super::effect::{AppEffect, AppEffectHandler, AppEffectResult};
use super::super::effectful;
use super::super::validation::{validate_agent_commands, validate_can_commit};

// Setup helpers for agent validation and pipeline preparation.
//
// This module contains:
// - validate_and_setup_agents: Validates agent commands and sets up git repo
// - setup_git_and_prompt_file: Creates PROMPT.md from template if needed
// - Interrupt context management for checkpoint saving
// - Configuration validation helpers

/// Parameters for agent validation and setup.
pub struct AgentSetupParams<'a> {
    pub(crate) config: &'a crate::config::Config,
    pub(crate) registry: &'a AgentRegistry,
    pub(crate) developer_agent: &'a str,
    pub(crate) reviewer_agent: &'a str,
    pub(crate) config_path: &'a std::path::Path,
    pub(crate) colors: Colors,
    pub(crate) logger: &'a Logger,
    /// If Some, use this path as the working directory without discovering the repo root
    /// or changing the global CWD. This enables test parallelism.
    pub(crate) working_dir_override: Option<&'a std::path::Path>,
}

/// Validates agent commands and workflow capability, then sets up git repo and PROMPT.md.
///
/// Returns `Some(repo_root)` if setup succeeded and should continue.
/// Returns `None` if the user declined PROMPT.md creation (to exit early).
pub fn validate_and_setup_agents<H: AppEffectHandler>(
    params: &AgentSetupParams<'_>,
    handler: &mut H,
) -> anyhow::Result<Option<std::path::PathBuf>> {
    let AgentSetupParams {
        config,
        registry,
        developer_agent,
        reviewer_agent,
        config_path,
        colors,
        logger,
        working_dir_override,
    } = params;
    // Validate agent commands exist
    validate_agent_commands(
        config,
        registry,
        developer_agent,
        reviewer_agent,
        config_path,
    )?;

    // Validate agents are workflow-capable
    validate_can_commit(
        config,
        registry,
        developer_agent,
        reviewer_agent,
        config_path,
    )?;

    // Determine repo root - use override if provided (for testing), otherwise discover
    let repo_root = if let Some(override_dir) = working_dir_override {
        // Testing mode: use provided directory and change CWD to it via handler
        let result = handler.execute(AppEffect::SetCurrentDir {
            path: override_dir.to_path_buf(),
        });
        if let AppEffectResult::Error(e) = result {
            anyhow::bail!("Failed to set working directory: {e}");
        }
        override_dir.to_path_buf()
    } else {
        // Production mode: discover repo root and change CWD via handler
        let require_result = handler.execute(AppEffect::GitRequireRepo);
        if let AppEffectResult::Error(e) = require_result {
            anyhow::bail!("Not in a git repository: {e}");
        }

        let root_result = handler.execute(AppEffect::GitGetRepoRoot);
        let root = match root_result {
            AppEffectResult::Path(p) => p,
            AppEffectResult::Error(e) => {
                anyhow::bail!("Failed to get repo root: {e}");
            }
            _ => anyhow::bail!("Unexpected result from GitGetRepoRoot"),
        };

        let set_result = handler.execute(AppEffect::SetCurrentDir { path: root.clone() });
        if let AppEffectResult::Error(e) = set_result {
            anyhow::bail!("Failed to set working directory: {e}");
        }
        root
    };

    // Set up PROMPT.md if needed (may return None to exit early)
    let should_continue = setup_git_and_prompt_file(config, *colors, logger, handler)?;
    if should_continue.is_none() {
        return Ok(None);
    }

    Ok(Some(repo_root))
}

/// In interactive mode, prompts to create PROMPT.md from a template before `ensure_files()`.
///
/// Returns `Ok(Some(()))` if setup succeeded and should continue.
/// Returns `Ok(None)` if the user declined PROMPT.md creation (to exit early).
fn setup_git_and_prompt_file<H: AppEffectHandler>(
    config: &crate::config::Config,
    colors: Colors,
    logger: &Logger,
    handler: &mut H,
) -> anyhow::Result<Option<()>> {
    let prompt_exists =
        effectful::check_prompt_exists_effectful(handler).map_err(|e| anyhow::anyhow!("{e}"))?;

    // In interactive mode, prompt to create PROMPT.md from a template BEFORE ensure_files().
    // If the user declines (or we can't prompt), exit without creating a placeholder PROMPT.md.
    if config.behavior.interactive && !prompt_exists {
        if let Some(template_name) = prompt_template_selection(colors) {
            create_prompt_from_template(&template_name, colors)?;
            logger.info(""); // Empty line for spacing
            logger.info(
                "PROMPT.md created. Please edit it with your task details, then run ralph again.",
            );
            logger.info("Tip: Edit PROMPT.md, then run: ralph");
            return Ok(None);
        }
        logger.info(""); // Empty line for spacing
        logger.error("PROMPT.md not found in current directory.");
        logger.warn("PROMPT.md is required to run the Ralph pipeline.");
        logger.info(""); // Empty line for spacing
        logger.info("To get started:");
        logger.info("  ralph --init                    # Smart setup wizard");
        logger.info("  ralph --init bug-fix             # Create from Work Guide");
        logger.info("  ralph --list-work-guides          # See all Work Guides");
        logger.info(""); // Empty line for spacing
        return Ok(None);
    }

    // Non-interactive mode: show helpful error if PROMPT.md doesn't exist
    if !prompt_exists {
        logger.error("PROMPT.md not found in current directory.");
        logger.warn("PROMPT.md is required to run the Ralph pipeline.");
        logger.info(""); // Empty line for spacing
        logger.info("Quick start:");
        logger.info("  ralph --init                    # Smart setup wizard");
        logger.info("  ralph --init bug-fix             # Create from Work Guide");
        logger.info("  ralph --list-work-guides          # See all Work Guides");
        logger.info(""); // Empty line for spacing
        logger.info("Use -i flag for interactive mode to be prompted for template selection.");
        logger.info(""); // Empty line for spacing
        return Ok(None);
    }

    Ok(Some(()))
}

/// Set up the interrupt context with initial pipeline state.
///
/// This function initializes the global interrupt context so that if
/// the user presses Ctrl+C, the interrupt handler can save a checkpoint.
pub(crate) fn setup_interrupt_context_for_pipeline(
    phase: PipelinePhase,
    total_iterations: u32,
    total_reviewer_passes: u32,
    execution_history: &crate::checkpoint::ExecutionHistory,
    prompt_history: &std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
    run_context: &crate::checkpoint::RunContext,
    workspace: std::sync::Arc<dyn Workspace>,
) {
    use crate::interrupt::{set_interrupt_context, InterruptContext};

    // Determine initial iteration based on phase
    let (iteration, reviewer_pass) = match phase {
        PipelinePhase::Development => (1, 0),
        PipelinePhase::Review => (total_iterations, 1),
        PipelinePhase::PostRebase | PipelinePhase::CommitMessage => {
            (total_iterations, total_reviewer_passes)
        }
        _ => (0, 0),
    };

    let context = InterruptContext {
        phase,
        iteration,
        total_iterations,
        reviewer_pass,
        total_reviewer_passes,
        run_context: run_context.clone(),
        execution_history: execution_history.clone(),
        prompt_history: prompt_history.clone(),
        workspace,
    };

    set_interrupt_context(context);
}

/// Update the interrupt context from the current phase context.
///
/// This function should be called after each major phase to keep the
/// interrupt context up-to-date with the latest execution history.
///
/// `prompt_history` reflects the state at the time of the last checkpoint load.
///
/// While the reducer event loop is active, Ctrl+C triggers a reducer-driven
/// checkpoint write from live `PipelineState` (including up-to-date
/// `prompt_history`). This interrupt context is used only for early interrupts
/// before the event loop starts.
pub(crate) fn update_interrupt_context_from_phase(
    execution_history: &crate::checkpoint::ExecutionHistory,
    prompt_history: std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
    phase: PipelinePhase,
    total_iterations: u32,
    total_reviewer_passes: u32,
    run_context: &crate::checkpoint::RunContext,
    workspace: std::sync::Arc<dyn Workspace>,
) {
    use crate::interrupt::{set_interrupt_context, InterruptContext};

    // Determine current iteration based on phase
    let (iteration, reviewer_pass) = match phase {
        PipelinePhase::Development => {
            // Estimate iteration from actual runs
            let iter = run_context.actual_developer_runs.max(1);
            (iter, 0)
        }
        PipelinePhase::Review => (total_iterations, run_context.actual_reviewer_runs.max(1)),
        PipelinePhase::PostRebase | PipelinePhase::CommitMessage => {
            (total_iterations, total_reviewer_passes)
        }
        _ => (0, 0),
    };

    let context = InterruptContext {
        phase,
        iteration,
        total_iterations,
        reviewer_pass,
        total_reviewer_passes,
        run_context: run_context.clone(),
        execution_history: execution_history.clone(),
        prompt_history,
        workspace,
    };

    set_interrupt_context(context);
}

/// Helper to defer clearing interrupt context until function exit.
///
/// Uses a scope guard pattern to ensure the interrupt context is cleared
/// when the pipeline completes successfully, preventing an "interrupted"
/// checkpoint from being saved after normal completion.
pub(crate) const fn defer_clear_interrupt_context() -> InterruptContextGuard {
    InterruptContextGuard
}

/// RAII guard for clearing interrupt context on drop.
///
/// Ensures the interrupt context is cleared when the guard is dropped,
/// preventing an "interrupted" checkpoint from being saved after normal
/// pipeline completion.
pub(crate) struct InterruptContextGuard;

impl Drop for InterruptContextGuard {
    fn drop(&mut self) {
        crate::interrupt::clear_interrupt_context();
    }
}
