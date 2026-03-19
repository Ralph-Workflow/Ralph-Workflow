use crate::app::rebase::ConflictResolutionResult;
use crate::config::Config;
use crate::logger::{Colors, Logger};
use crate::workspace::Workspace;
use std::cell::RefCell;
use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

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
    let mut timer = crate::app::runtime_factory::create_timer();
    let executor_ref: &dyn crate::executor::ProcessExecutor = &*executor_arc;
    let mut runtime = crate::app::runtime_factory::create_pipeline_runtime(
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

pub fn try_resolve_conflicts_with_hook_boundary<F>(
    conflicted_files: &[String],
    ctx: &crate::app::rebase::types::ConflictResolutionContext<'_>,
    phase: &str,
    executor: &dyn crate::executor::ProcessExecutor,
    mut after_prompt_capture: F,
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
    use crate::prompts::{get_stored_or_generate_prompt, PromptScopeKey};

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

    let scope_key = PromptScopeKey::for_conflict_resolution(phase, 0);
    let prompt_key = scope_key.to_string();
    let prompt_history_cell = RefCell::new(HashMap::new());
    let (resolution_prompt, was_replayed) = get_stored_or_generate_prompt(
        &scope_key,
        &prompt_history_cell.borrow(),
        Some(&current_content_id),
        || {
            crate::app::rebase::conflicts::build_resolution_prompt(
                &conflicts,
                ctx.template_context,
                ctx.workspace,
            )
        },
    );

    let replay = crate::app::rebase::conflicts::ConflictResolutionPromptReplay {
        key: prompt_key,
        was_replayed,
        captured_entry: if was_replayed {
            None
        } else {
            Some(crate::prompts::PromptHistoryEntry::new(
                resolution_prompt.clone(),
                Some(current_content_id),
            ))
        },
    };

    let captured_entry = after_prompt_capture(&replay);

    if let Some(entry) = captured_entry {
        prompt_history_cell.borrow_mut().insert(entry.0, entry.1);
    }

    let prompt_history = prompt_history_cell.into_inner();

    let resolved =
        match crate::app::rebase::conflicts::run_ai_conflict_resolution(&resolution_prompt, ctx) {
            Ok(ConflictResolutionResult::FileEditsOnly) => {
                crate::app::rebase::conflicts::handle_file_edits_resolution(ctx.logger)?
            }
            Ok(ConflictResolutionResult::Failed) => {
                crate::app::rebase::conflicts::handle_failed_resolution(ctx.logger, executor)
            }
            Err(e) => {
                crate::app::rebase::conflicts::handle_error_resolution(ctx.logger, executor, &e)
            }
        };

    Ok((resolved, replay, prompt_history))
}
