// Pipeline Phase Setup
//
// This module handles agent phase preparation, cloud runtime creation,
// initial state computation, and event loop invocation.

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
    phase_ctx: &crate::phases::PhaseContext<'_>,
    resume_checkpoint: Option<&crate::checkpoint::PipelineCheckpoint>,
    should_run_rebase: bool,
) -> crate::reducer::PipelineState {
    let base_state = resume_checkpoint.map_or_else(
        || crate::app::config::create_initial_state_with_config(phase_ctx),
        |checkpoint| {
            let base_state = crate::app::config::create_initial_state_with_config(phase_ctx);
            let migrated =
                crate::reducer::PipelineState::from_checkpoint_with_execution_history_limit(
                    checkpoint.clone(),
                    phase_ctx.config.execution_history_limit,
                );
            crate::app::config::overlay_checkpoint_progress_onto_base_state(
                base_state,
                migrated,
                phase_ctx.config.execution_history_limit,
            )
        },
    );

    if should_run_rebase {
        if matches!(
            base_state.rebase,
            crate::reducer::state::RebaseState::NotStarted
        ) {
            let default_branch =
                crate::git_helpers::get_default_branch().unwrap_or_else(|_| "main".to_string());
            crate::reducer::PipelineState {
                rebase: crate::reducer::state::RebaseState::InProgress {
                    original_head: "HEAD".to_string(),
                    target_branch: default_branch,
                },
                ..base_state
            }
        } else {
            base_state
        }
    } else if matches!(
        base_state.rebase,
        crate::reducer::state::RebaseState::NotStarted
    ) {
        crate::reducer::PipelineState {
            rebase: crate::reducer::state::RebaseState::Skipped,
            ..base_state
        }
    } else {
        base_state
    }
}

fn run_event_loop_with_default_handler<'r, 'ctx>(
    phase_ctx: &'r mut crate::phases::PhaseContext<'ctx>,
    initial_state: crate::reducer::PipelineState,
) -> anyhow::Result<crate::app::config::EventLoopResult> {
    crate::app::pipeline_setup::run_event_loop_with_handler_boundary(phase_ctx, initial_state)
}
