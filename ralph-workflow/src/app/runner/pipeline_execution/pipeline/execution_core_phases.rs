// Pipeline Phase Setup
//
// This module handles agent phase preparation, cloud runtime creation,
// initial state computation, and event loop invocation.

fn prepare_agent_phase(ctx: &PipelineContext, git_helpers: &mut crate::git_helpers::GitHelpers) {
    if let Err(err) =
        crate::git_helpers::cleanup_orphaned_marker_with_workspace(&*ctx.workspace, &ctx.logger)
    {
        ctx.logger
            .warn(&format!("Failed to cleanup orphaned marker: {err}"));
    }

    if let Some(warning) = crate::files::make_prompt_writable_with_workspace(&*ctx.workspace) {
        ctx.logger
            .warn(&format!("PROMPT.md permission restore on startup: {warning}"));
    }

    if let Err(err) = crate::git_helpers::create_marker_with_workspace(&*ctx.workspace) {
        ctx.logger
            .warn(&format!("Failed to create agent phase marker: {err}"));
    }

    let hooks_dir = crate::git_helpers::get_hooks_dir_in_repo(&ctx.repo_root);
    let ralph_hook_detected = hooks_dir.ok().is_some_and(|dir| {
        ["pre-commit", "pre-push"].into_iter().any(|name| {
            crate::files::file_contains_marker(&dir.join(name), crate::git_helpers::HOOK_MARKER)
                .unwrap_or(false)
        })
    });

    if ralph_hook_detected {
        if let Err(err) = crate::git_helpers::uninstall_hooks_in_repo(&ctx.repo_root, &ctx.logger)
        {
            ctx.logger
                .warn(&format!("Startup hook cleanup warning: {err}"));
        }
    }

    if let Err(err) = crate::git_helpers::start_agent_phase(git_helpers) {
        ctx.logger
            .warn(&format!("Failed to start agent phase: {err}"));
    }
}

fn create_cloud_runtime(
    config: &crate::config::Config,
) -> (
    std::sync::Arc<dyn crate::cloud::CloudReporter>,
    Option<crate::cloud::HeartbeatGuard>,
) {
    use crate::cloud::{CloudReporter, HeartbeatGuard, HttpCloudReporter, NoopCloudReporter};
    use std::sync::Arc;
    use std::time::Duration;

    let cloud_reporter: Arc<dyn CloudReporter> = if config.cloud.enabled {
        Arc::new(HttpCloudReporter::new(config.cloud.clone()))
    } else {
        Arc::new(NoopCloudReporter)
    };

    let heartbeat_guard = if config.cloud.enabled {
        Some(HeartbeatGuard::start(
            Arc::clone(&cloud_reporter),
            Duration::from_secs(u64::from(config.cloud.heartbeat_interval_secs)),
        ))
    } else {
        None
    };

    (cloud_reporter, heartbeat_guard)
}

fn compute_initial_state(
    phase_ctx: &PhaseContext<'_>,
    resume_checkpoint: Option<&crate::checkpoint::PipelineCheckpoint>,
    should_run_rebase: bool,
) -> crate::reducer::PipelineState {
    let mut initial_state = resume_checkpoint.map_or_else(
        || crate::app::event_loop::create_initial_state_with_config(phase_ctx),
        |checkpoint| {
            let mut base_state = crate::app::event_loop::create_initial_state_with_config(phase_ctx);
            let migrated = crate::reducer::PipelineState::from_checkpoint_with_execution_history_limit(
                checkpoint.clone(),
                phase_ctx.config.execution_history_limit,
            );
            crate::app::event_loop::overlay_checkpoint_progress_onto_base_state(
                &mut base_state,
                migrated,
                phase_ctx.config.execution_history_limit,
            );
            base_state
        },
    );

    if should_run_rebase {
        if matches!(
            initial_state.rebase,
            crate::reducer::state::RebaseState::NotStarted
        ) {
            let default_branch =
                crate::git_helpers::get_default_branch().unwrap_or_else(|_| "main".to_string());
            initial_state.rebase = crate::reducer::state::RebaseState::InProgress {
                original_head: "HEAD".to_string(),
                target_branch: default_branch,
            };
        }
    } else if matches!(
        initial_state.rebase,
        crate::reducer::state::RebaseState::NotStarted
    ) {
        initial_state.rebase = crate::reducer::state::RebaseState::Skipped;
    }

    initial_state
}

fn run_event_loop_with_default_handler(
    phase_ctx: &mut PhaseContext<'_>,
    initial_state: crate::reducer::PipelineState,
) -> anyhow::Result<crate::app::event_loop::EventLoopResult> {
    use crate::app::event_loop::{EventLoopConfig, run_event_loop_with_handler};
    use crate::reducer::MainEffectHandler;

    let event_loop_config = EventLoopConfig {
        max_iterations: event_loop::MAX_EVENT_LOOP_ITERATIONS,
    };

    let mut handler = MainEffectHandler::new(initial_state.clone());
    run_event_loop_with_handler(phase_ctx, Some(initial_state), event_loop_config, &mut handler)
}
