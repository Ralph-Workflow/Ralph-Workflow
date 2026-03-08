use super::conflicts::try_resolve_conflicts;
use super::types::{ConflictResolutionContext, InitialRebaseOutcome};
use crate::checkpoint::execution_history::{ExecutionStep, StepOutcome};
use crate::checkpoint::{
    save_checkpoint_with_workspace, CheckpointBuilder, PipelinePhase, RebaseState, RunContext,
};
use crate::executor::ProcessExecutor;
use crate::git_helpers::{
    abort_rebase, continue_rebase, get_conflicted_files, get_default_branch, rebase_onto,
    RebaseErrorKind, RebaseResult,
};
use crate::logger::{Colors, Logger};
use crate::phases::PhaseContext;
use crate::workspace::Workspace;

/// Run rebase to the default branch.
///
/// This function performs a rebase from the current branch to the
/// default branch (main/master). It handles all edge cases including:
/// - Already on main/master (proceeds with rebase attempt)
/// - Empty repository (returns `NoOp`)
/// - Upstream branch not found (error)
/// - Conflicts during rebase (returns `Conflicts` result)
pub fn run_rebase_to_default(
    logger: &Logger,
    colors: Colors,
    executor: &dyn ProcessExecutor,
) -> std::io::Result<RebaseResult> {
    let default_branch = get_default_branch()?;
    logger.info(&format!(
        "Rebasing onto {}{}{}",
        colors.cyan(),
        default_branch,
        colors.reset()
    ));
    rebase_onto(&default_branch, executor)
}

/// Run initial rebase before development phase.
///
/// This function is called before the development phase starts to ensure
/// the feature branch is up-to-date with the default branch.
///
/// Uses a state machine for fault tolerance and automatic recovery from
/// interruptions or failures.
pub fn run_initial_rebase(
    phase_ctx: &mut PhaseContext<'_>,
    run_context: &RunContext,
    executor: &dyn ProcessExecutor,
    prompt_history: &mut std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
) -> anyhow::Result<InitialRebaseOutcome> {
    phase_ctx
        .logger
        .header("Pre-development rebase", Colors::cyan);

    record_rebase_start(phase_ctx);
    save_pre_rebase_checkpoint(phase_ctx, run_context, prompt_history);

    match run_rebase_to_default(phase_ctx.logger, *phase_ctx.colors, executor) {
        Ok(RebaseResult::Success) => {
            handle_rebase_success(phase_ctx, run_context, prompt_history);
            Ok(InitialRebaseOutcome::Succeeded {
                new_head: read_repo_head_or_unknown(phase_ctx.workspace),
            })
        }
        Ok(RebaseResult::NoOp { reason }) => {
            handle_rebase_noop(phase_ctx, run_context, &reason, prompt_history);
            Ok(InitialRebaseOutcome::Skipped { reason })
        }
        Ok(RebaseResult::Conflicts(_)) => {
            let resolved = handle_rebase_conflicts(phase_ctx, run_context, executor, prompt_history)?;
            if resolved {
                Ok(InitialRebaseOutcome::Succeeded {
                    new_head: read_repo_head_or_unknown(phase_ctx.workspace),
                })
            } else {
                Ok(InitialRebaseOutcome::Skipped {
                    reason: "Rebase conflicts unresolved".to_string(),
                })
            }
        }
        Ok(RebaseResult::Failed(err)) => {
            handle_rebase_failed(phase_ctx, &err);
            Ok(InitialRebaseOutcome::Skipped {
                reason: "Rebase failed".to_string(),
            })
        }
        Err(e) => {
            handle_rebase_error(phase_ctx, &e);
            Ok(InitialRebaseOutcome::Skipped {
                reason: "Rebase error".to_string(),
            })
        }
    }
}

/// Record the start of a pre-rebase operation.
fn record_rebase_start(phase_ctx: &mut PhaseContext<'_>) {
    let step = ExecutionStep::new(
        "PreRebase",
        0,
        "pre_rebase_start",
        StepOutcome::success(None, vec![]),
    );
    phase_ctx
        .execution_history
        .add_step_bounded(step, phase_ctx.config.execution_history_limit);
}

/// Save checkpoint at the start of pre-rebase phase.
fn save_pre_rebase_checkpoint(
    phase_ctx: &PhaseContext<'_>,
    run_context: &RunContext,
    prompt_history: &std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
) {
    if !phase_ctx.config.features.checkpoint_enabled {
        return;
    }

    let default_branch = get_default_branch().unwrap_or_else(|_| "main".to_string());
    let builder =
        create_checkpoint_builder(phase_ctx, run_context, PipelinePhase::PreRebase, prompt_history);

    if let Some(mut checkpoint) = builder.build_with_workspace(phase_ctx.workspace) {
        checkpoint.rebase_state = RebaseState::PreRebaseInProgress {
            upstream_branch: default_branch,
        };
        let _ = save_checkpoint_with_workspace(phase_ctx.workspace, &checkpoint);
    }
}

/// Handle successful rebase completion.
fn handle_rebase_success(
    phase_ctx: &mut PhaseContext<'_>,
    run_context: &RunContext,
    prompt_history: &std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
) {
    phase_ctx.logger.success("Rebase completed successfully");

    let step = ExecutionStep::new(
        "PreRebase",
        0,
        "pre_rebase_complete",
        StepOutcome::success(None, vec![]),
    );
    phase_ctx
        .execution_history
        .add_step_bounded(step, phase_ctx.config.execution_history_limit);

    save_post_rebase_checkpoint(phase_ctx, run_context, prompt_history);
}

/// Handle rebase that was not needed.
fn handle_rebase_noop(
    phase_ctx: &mut PhaseContext<'_>,
    run_context: &RunContext,
    reason: &str,
    prompt_history: &std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
) {
    phase_ctx
        .logger
        .info(&format!("No rebase needed: {reason}"));

    let step = ExecutionStep::new(
        "PreRebase",
        0,
        "pre_rebase_skipped",
        StepOutcome::skipped(reason.to_string()),
    );
    phase_ctx
        .execution_history
        .add_step_bounded(step, phase_ctx.config.execution_history_limit);

    save_post_rebase_checkpoint(phase_ctx, run_context, prompt_history);
}

/// Handle rebase conflicts by attempting AI resolution.
fn handle_rebase_conflicts(
    phase_ctx: &mut PhaseContext<'_>,
    run_context: &RunContext,
    executor: &dyn ProcessExecutor,
    prompt_history: &mut std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
) -> anyhow::Result<bool> {
    let conflicted_files = get_conflicted_files()?;
    if conflicted_files.is_empty() {
        phase_ctx
            .logger
            .warn("Rebase reported conflicts but no conflicted files found");
        let _ = abort_rebase(executor);
        return Ok(false);
    }

    record_conflict_detected(phase_ctx, conflicted_files.len());
    save_conflict_checkpoint(phase_ctx, run_context, &conflicted_files, prompt_history);

    phase_ctx.logger.warn(&format!(
        "Rebase resulted in {} conflict(s), attempting AI resolution",
        conflicted_files.len()
    ));

    let resolution_ctx = ConflictResolutionContext {
        config: phase_ctx.config,
        registry: phase_ctx.registry,
        template_context: phase_ctx.template_context,
        logger: phase_ctx.logger,
        colors: *phase_ctx.colors,
        executor_arc: std::sync::Arc::clone(&phase_ctx.executor_arc),
        workspace: phase_ctx.workspace,
        workspace_arc: std::sync::Arc::clone(&phase_ctx.workspace_arc),
    };

    match try_resolve_conflicts(
        &conflicted_files,
        &resolution_ctx,
        prompt_history,
        "PreRebase",
        executor,
    ) {
        Ok(true) => {
            handle_conflicts_resolved(phase_ctx, run_context, executor, prompt_history);
            Ok(true)
        }
        Ok(false) => {
            handle_resolution_failed(phase_ctx, executor);
            Ok(false)
        }
        Err(e) => {
            handle_resolution_error(phase_ctx, executor, &e);
            Ok(false)
        }
    }
}

/// Record that conflicts were detected during rebase.
fn record_conflict_detected(phase_ctx: &mut PhaseContext<'_>, conflict_count: usize) {
    let step = ExecutionStep::new(
        "PreRebase",
        0,
        "pre_rebase_conflict",
        StepOutcome::partial(
            "Rebase started".to_string(),
            format!("{conflict_count} conflicts detected"),
        ),
    );
    phase_ctx
        .execution_history
        .add_step_bounded(step, phase_ctx.config.execution_history_limit);
}

/// Save checkpoint for conflict state.
fn save_conflict_checkpoint(
    phase_ctx: &PhaseContext<'_>,
    run_context: &RunContext,
    conflicted_files: &[String],
    prompt_history: &std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
) {
    if !phase_ctx.config.features.checkpoint_enabled {
        return;
    }

    let builder = create_checkpoint_builder(
        phase_ctx,
        run_context,
        PipelinePhase::PreRebaseConflict,
        prompt_history,
    );

    if let Some(mut checkpoint) = builder.build_with_workspace(phase_ctx.workspace) {
        checkpoint.rebase_state = RebaseState::HasConflicts {
            files: conflicted_files.to_vec(),
        };
        let _ = save_checkpoint_with_workspace(phase_ctx.workspace, &checkpoint);
    }
}

/// Handle successful conflict resolution.
fn handle_conflicts_resolved(
    phase_ctx: &mut PhaseContext<'_>,
    run_context: &RunContext,
    executor: &dyn ProcessExecutor,
    prompt_history: &std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
) {
    phase_ctx
        .logger
        .info("Continuing rebase after conflict resolution");

    match continue_rebase(executor) {
        Ok(()) => {
            phase_ctx
                .logger
                .success("Rebase completed successfully after AI resolution");

            let step = ExecutionStep::new(
                "PreRebase",
                0,
                "pre_rebase_resolution",
                StepOutcome::success(None, vec![]),
            );
            phase_ctx
                .execution_history
                .add_step_bounded(step, phase_ctx.config.execution_history_limit);

            save_post_rebase_checkpoint(phase_ctx, run_context, prompt_history);
        }
        Err(e) => {
            phase_ctx
                .logger
                .warn(&format!("Failed to continue rebase: {e}"));
            let _ = abort_rebase(executor);

            let step = ExecutionStep::new(
                "PreRebase",
                0,
                "pre_rebase_resolution",
                StepOutcome::partial(
                    "Conflicts resolved by AI".to_string(),
                    format!("Failed to continue rebase: {e}"),
                ),
            );
            phase_ctx
                .execution_history
                .add_step_bounded(step, phase_ctx.config.execution_history_limit);
        }
    }
}

/// Handle failed AI conflict resolution.
fn handle_resolution_failed(phase_ctx: &mut PhaseContext<'_>, executor: &dyn ProcessExecutor) {
    phase_ctx
        .logger
        .warn("AI conflict resolution failed, aborting rebase");
    let _ = abort_rebase(executor);

    let step = ExecutionStep::new(
        "PreRebase",
        0,
        "pre_rebase_resolution",
        StepOutcome::failure("AI conflict resolution failed".to_string(), true),
    );
    phase_ctx
        .execution_history
        .add_step_bounded(step, phase_ctx.config.execution_history_limit);
}

/// Handle error during conflict resolution.
fn handle_resolution_error(
    phase_ctx: &mut PhaseContext<'_>,
    executor: &dyn ProcessExecutor,
    e: &anyhow::Error,
) {
    phase_ctx
        .logger
        .error(&format!("Conflict resolution error: {e}"));
    let _ = abort_rebase(executor);

    let step = ExecutionStep::new(
        "PreRebase",
        0,
        "pre_rebase_resolution",
        StepOutcome::failure(format!("Conflict resolution error: {e}"), true),
    );
    phase_ctx
        .execution_history
        .add_step_bounded(step, phase_ctx.config.execution_history_limit);
}

/// Handle rebase failure.
fn handle_rebase_failed(phase_ctx: &mut PhaseContext<'_>, err: &RebaseErrorKind) {
    phase_ctx.logger.error(&format!("Rebase failed: {err}"));
    let _ = abort_rebase(phase_ctx.executor);

    let step = ExecutionStep::new(
        "PreRebase",
        0,
        "pre_rebase_failed",
        StepOutcome::failure(format!("Rebase failed: {err}"), true),
    );
    phase_ctx
        .execution_history
        .add_step_bounded(step, phase_ctx.config.execution_history_limit);
}

/// Handle rebase error.
fn handle_rebase_error(phase_ctx: &mut PhaseContext<'_>, e: &std::io::Error) {
    phase_ctx
        .logger
        .warn(&format!("Rebase failed, continuing without rebase: {e}"));

    let step = ExecutionStep::new(
        "PreRebase",
        0,
        "pre_rebase_error",
        StepOutcome::failure(format!("Rebase error: {e}"), true),
    );
    phase_ctx
        .execution_history
        .add_step_bounded(step, phase_ctx.config.execution_history_limit);
}

/// Save checkpoint after successful rebase completion.
fn save_post_rebase_checkpoint(
    phase_ctx: &PhaseContext<'_>,
    run_context: &RunContext,
    prompt_history: &std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
) {
    if !phase_ctx.config.features.checkpoint_enabled {
        return;
    }

    let builder = create_checkpoint_builder(
        phase_ctx,
        run_context,
        PipelinePhase::Planning,
        prompt_history,
    );

    if let Some(checkpoint) = builder.build_with_workspace(phase_ctx.workspace) {
        let _ = save_checkpoint_with_workspace(phase_ctx.workspace, &checkpoint);
    }
}

/// Create a checkpoint builder with common configuration.
fn create_checkpoint_builder(
    phase_ctx: &PhaseContext<'_>,
    run_context: &RunContext,
    phase: PipelinePhase,
    prompt_history: &std::collections::HashMap<String, crate::prompts::PromptHistoryEntry>,
) -> CheckpointBuilder {
    CheckpointBuilder::new()
        .phase(phase, 0, phase_ctx.config.developer_iters)
        .reviewer_pass(0, phase_ctx.config.reviewer_reviews)
        .capture_from_context(
            phase_ctx.config,
            phase_ctx.registry,
            phase_ctx.developer_agent,
            phase_ctx.reviewer_agent,
            phase_ctx.logger,
            run_context,
        )
        .with_executor_from_context(std::sync::Arc::clone(&phase_ctx.executor_arc))
        .with_execution_history(phase_ctx.execution_history.clone())
        .with_prompt_history(prompt_history.clone())
        .with_log_run_id(phase_ctx.run_log_context.run_id().to_string())
}

fn read_repo_head_or_unknown(workspace: &dyn Workspace) -> String {
    git2::Repository::open(workspace.root()).map_or_else(
        |_| "unknown".to_string(),
        |repo| {
            repo.head()
                .ok()
                .and_then(|head| head.peel_to_commit().ok())
                .map_or_else(|| "unknown".to_string(), |commit| commit.id().to_string())
        },
    )
}
