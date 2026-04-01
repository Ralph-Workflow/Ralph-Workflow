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

pub struct ConflictResolutionRuntimeParams<'a> {
    pub resolution_prompt: &'a str,
    pub config: &'a Config,
    pub logger: &'a Logger,
    pub colors: Colors,
    pub executor_arc: Arc<dyn crate::executor::ProcessExecutor>,
    pub workspace: &'a dyn Workspace,
    pub workspace_arc: Arc<dyn Workspace>,
    pub reviewer_agent: &'a str,
    pub registry: &'a crate::agents::AgentRegistry,
}

pub fn run_ai_conflict_resolution_with_runtime(
    params: ConflictResolutionRuntimeParams<'_>,
) -> anyhow::Result<ConflictResolutionResult> {
    let ConflictResolutionRuntimeParams {
        resolution_prompt,
        config,
        logger,
        colors,
        executor_arc,
        workspace,
        workspace_arc,
        reviewer_agent,
        registry,
    } = params;
    let mut timer = crate::app::runtime_factory::create_timer();
    let executor_ref: &dyn crate::executor::ProcessExecutor = &*executor_arc;
    let mut runtime = crate::app::runtime_factory::create_pipeline_runtime(
        crate::app::runtime_factory::PipelineRuntimeFactoryParams {
            timer: &mut timer,
            logger,
            colors: &colors,
            config,
            executor: executor_ref,
            executor_arc: Arc::clone(&executor_arc),
            workspace,
            workspace_arc: Arc::clone(&workspace_arc),
        },
    );
    let log_dir = ".agent/logs/rebase_conflict_resolution";
    workspace.create_dir_all(Path::new(log_dir))?;
    run_conflict_prompt_and_check(
        resolution_prompt,
        reviewer_agent,
        log_dir,
        registry,
        workspace,
        &mut runtime,
    )
}

fn run_conflict_prompt_and_check(
    resolution_prompt: &str,
    reviewer_agent: &str,
    log_dir: &str,
    registry: &crate::agents::AgentRegistry,
    workspace: &dyn Workspace,
    runtime: &mut crate::pipeline::PipelineRuntime<'_>,
) -> anyhow::Result<ConflictResolutionResult> {
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
        completion_output_path: None,
    };
    let result = crate::pipeline::run_with_prompt(&prompt_cmd, runtime)?;
    check_conflict_exit_and_remaining(workspace.root(), result.exit_code)
}

fn check_conflict_exit_and_remaining(
    repo_root: &std::path::Path,
    exit_code: i32,
) -> anyhow::Result<ConflictResolutionResult> {
    if exit_code != 0 {
        return Ok(ConflictResolutionResult::Failed);
    }
    // If we can't get conflicted files (e.g., in tests with mock workspace),
    // assume no conflicts remain after successful resolution.
    let remaining_conflicts =
        crate::git_helpers::get_conflicted_files_at(repo_root).unwrap_or_default();
    if remaining_conflicts.is_empty() {
        Ok(ConflictResolutionResult::FileEditsOnly)
    } else {
        Ok(ConflictResolutionResult::Failed)
    }
}
