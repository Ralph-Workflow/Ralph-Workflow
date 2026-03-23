use crate::app::boundary::conflict_resolution::ConflictResolutionRuntimeParams;
use crate::app::rebase::ConflictResolutionResult;
use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

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
    workspace: &dyn crate::workspace::Workspace,
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
    };
    let result = crate::pipeline::run_with_prompt(&prompt_cmd, runtime)?;
    check_conflict_exit_and_remaining(result.exit_code)
}

fn check_conflict_exit_and_remaining(exit_code: i32) -> anyhow::Result<ConflictResolutionResult> {
    if exit_code != 0 {
        return Ok(ConflictResolutionResult::Failed);
    }
    let remaining_conflicts = crate::git_helpers::get_conflicted_files()?;
    if remaining_conflicts.is_empty() {
        Ok(ConflictResolutionResult::FileEditsOnly)
    } else {
        Ok(ConflictResolutionResult::Failed)
    }
}

pub fn try_resolve_conflicts_with_hook_boundary<F>(
    conflicted_files: &[String],
    ctx: &crate::app::rebase::types::ConflictResolutionContext<'_>,
    phase: &str,
    executor: &dyn crate::executor::ProcessExecutor,
    after_prompt_capture: F,
) -> anyhow::Result<(
    bool,
    crate::app::rebase::conflicts::ConflictResolutionPromptReplay,
    HashMap<String, crate::prompts::PromptHistoryEntry>,
)>
where
    F: FnMut(
        &crate::app::rebase::conflicts::ConflictResolutionPromptReplay,
    ) -> Option<(String, crate::prompts::PromptHistoryEntry)>,
{
    use crate::prompts::PromptScopeKey;
    if conflicted_files.is_empty() {
        return Ok((
            false,
            crate::app::rebase::conflicts::ConflictResolutionPromptReplay {
                key: PromptScopeKey::for_conflict_resolution(phase, 0).to_string(),
                was_replayed: false,
                captured_entry: None,
            },
            HashMap::new(),
        ));
    }
    ctx.logger.info(&format!(
        "Attempting AI conflict resolution for {} file(s)",
        conflicted_files.len()
    ));
    let conflicts = crate::app::rebase::conflicts::collect_conflict_info_or_error(
        conflicted_files,
        ctx.workspace,
        ctx.logger,
    )?;
    let current_content_id =
        crate::app::rebase::conflicts::conflict_resolution_content_id(phase, &conflicts);
    let (resolution_prompt, replay, prompt_history) = prepare_conflict_prompt_data(
        &conflicts,
        &current_content_id,
        phase,
        ctx,
        after_prompt_capture,
    );
    let resolved = execute_ai_conflict_resolution(&resolution_prompt, ctx, executor)?;
    Ok((resolved, replay, prompt_history))
}

fn prepare_conflict_prompt_data<F>(
    conflicts: &HashMap<String, crate::prompts::FileConflict>,
    current_content_id: &str,
    phase: &str,
    ctx: &crate::app::rebase::types::ConflictResolutionContext<'_>,
    after_prompt_capture: F,
) -> (
    String,
    crate::app::rebase::conflicts::ConflictResolutionPromptReplay,
    HashMap<String, crate::prompts::PromptHistoryEntry>,
)
where
    F: FnMut(
        &crate::app::rebase::conflicts::ConflictResolutionPromptReplay,
    ) -> Option<(String, crate::prompts::PromptHistoryEntry)>,
{
    use crate::prompts::PromptScopeKey;
    let scope_key = PromptScopeKey::for_conflict_resolution(phase, 0);
    let (resolution_prompt, was_replayed) =
        generate_conflict_resolution_prompt(conflicts, current_content_id, ctx, &scope_key);
    let (replay, prompt_history) = build_replay_and_history(
        &scope_key,
        resolution_prompt.clone(),
        was_replayed,
        current_content_id,
        after_prompt_capture,
    );
    (resolution_prompt, replay, prompt_history)
}

fn generate_conflict_resolution_prompt(
    conflicts: &HashMap<String, crate::prompts::FileConflict>,
    current_content_id: &str,
    ctx: &crate::app::rebase::types::ConflictResolutionContext<'_>,
    scope_key: &crate::prompts::PromptScopeKey,
) -> (String, bool) {
    use crate::prompts::get_stored_or_generate_prompt;
    let empty_history = HashMap::new();
    get_stored_or_generate_prompt(scope_key, &empty_history, Some(current_content_id), || {
        crate::app::rebase::conflicts::build_resolution_prompt(
            conflicts,
            ctx.template_context,
            ctx.workspace,
        )
    })
}

fn build_replay_and_history<F>(
    scope_key: &crate::prompts::PromptScopeKey,
    resolution_prompt: String,
    was_replayed: bool,
    current_content_id: &str,
    mut after_prompt_capture: F,
) -> (
    crate::app::rebase::conflicts::ConflictResolutionPromptReplay,
    HashMap<String, crate::prompts::PromptHistoryEntry>,
)
where
    F: FnMut(
        &crate::app::rebase::conflicts::ConflictResolutionPromptReplay,
    ) -> Option<(String, crate::prompts::PromptHistoryEntry)>,
{
    let captured_entry = (!was_replayed).then(|| {
        crate::prompts::PromptHistoryEntry::new(
            resolution_prompt.clone(),
            Some(current_content_id.to_string()),
        )
    });
    let replay = crate::app::rebase::conflicts::ConflictResolutionPromptReplay {
        key: scope_key.to_string(),
        was_replayed,
        captured_entry,
    };
    let prompt_history = after_prompt_capture(&replay)
        .map(|(k, v)| std::iter::once((k, v)).collect())
        .unwrap_or_default();
    (replay, prompt_history)
}

fn execute_ai_conflict_resolution(
    resolution_prompt: &str,
    ctx: &crate::app::rebase::types::ConflictResolutionContext<'_>,
    executor: &dyn crate::executor::ProcessExecutor,
) -> anyhow::Result<bool> {
    match crate::app::rebase::conflicts::run_ai_conflict_resolution(resolution_prompt, ctx) {
        Ok(ConflictResolutionResult::FileEditsOnly) => {
            crate::app::rebase::conflicts::handle_file_edits_resolution(ctx.logger)
        }
        Ok(ConflictResolutionResult::Failed) => Ok(
            crate::app::rebase::conflicts::handle_failed_resolution(ctx.logger, executor),
        ),
        Err(e) => Ok(crate::app::rebase::conflicts::handle_error_resolution(
            ctx.logger, executor, &e,
        )),
    }
}
