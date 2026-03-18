//! Conflict resolution boundary module.
//!
//! This module provides boundary functions for conflict resolution that involve
//! runtime creation and execution. As a boundary module, it is exempt from
//! functional programming lints.

use crate::app::rebase::ConflictResolutionResult;
use crate::config::Config;
use crate::logger::{Colors, Logger};
use crate::workspace::Workspace;
use std::path::Path;
use std::sync::Arc;

/// Run AI conflict resolution using a pipeline runtime.
/// This boundary function creates the timer and runtime internally.
pub fn run_ai_conflict_resolution_with_runtime(
    resolution_prompt: &str,
    config: &Config,
    logger: &Logger,
    colors: Colors,
    executor_arc: Arc<dyn crate::executor::ProcessExecutor>,
    workspace: &dyn Workspace,
    workspace_arc: Arc<dyn Workspace>,
    reviewer_agent: &str,
    registry: &crate::agents::AgentRegistry,
) -> anyhow::Result<ConflictResolutionResult> {
    let mut timer = crate::app::io::runtime_factory::create_timer();
    let executor_ref: &dyn crate::executor::ProcessExecutor = &*executor_arc;
    let mut runtime = crate::app::io::runtime_factory::create_pipeline_runtime(
        &mut timer,
        logger,
        &colors,
        config,
        executor_ref,
        Arc::clone(&executor_arc),
        workspace,
        Arc::clone(&workspace_arc),
    );

    let log_dir = ".agent/logs/rebase_conflict_resolution";
    workspace.create_dir_all(Path::new(log_dir))?;

    let agent_config = registry
        .resolve_config(reviewer_agent)
        .ok_or_else(|| anyhow::anyhow!("Agent not found: {reviewer_agent}"))?;
    let cmd_str = agent_config.build_cmd_with_model(true, true, true, None);

    let log_prefix = format!("{log_dir}/conflict_resolution");
    let model_index = 0usize;
    let attempt = crate::pipeline::logfile::next_logfile_attempt_index(
        Path::new(&log_prefix),
        reviewer_agent,
        model_index,
        workspace,
    );
    let logfile = crate::pipeline::logfile::build_logfile_path_with_attempt(
        &log_prefix,
        reviewer_agent,
        model_index,
        attempt,
    );

    let prompt_cmd = crate::pipeline::PromptCommand {
        label: reviewer_agent,
        display_name: reviewer_agent,
        cmd_str: &cmd_str,
        prompt: resolution_prompt,
        log_prefix: &log_prefix,
        model_index: Some(model_index),
        attempt: Some(attempt),
        logfile: &logfile,
        parser_type: agent_config.json_parser,
        env_vars: &agent_config.env_vars,
    };

    let result = crate::pipeline::run_with_prompt(&prompt_cmd, &mut runtime)?;
    if result.exit_code != 0 {
        return Ok(ConflictResolutionResult::Failed);
    }

    let remaining_conflicts = crate::git_helpers::get_conflicted_files()?;
    if remaining_conflicts.is_empty() {
        Ok(ConflictResolutionResult::FileEditsOnly)
    } else {
        Ok(ConflictResolutionResult::Failed)
    }
}
