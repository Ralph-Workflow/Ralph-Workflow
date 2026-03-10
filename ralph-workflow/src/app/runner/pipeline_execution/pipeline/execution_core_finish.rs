// Pipeline Finish and Completion Reporting
//
// This module handles the post-event-loop phase of pipeline execution:
// logging the outcome, saving the completion checkpoint, reporting to cloud,
// and finalizing the pipeline state.

fn log_event_loop_outcome(
    ctx: &PipelineContext,
    loop_result: &crate::app::event_loop::EventLoopResult,
) {
    if loop_result.completed {
        match loop_result.final_phase {
            crate::reducer::event::PipelinePhase::Complete => {
                ctx.logger
                    .success("Pipeline completed successfully via reducer event loop");
            }
            crate::reducer::event::PipelinePhase::Interrupted => {
                ctx.logger
                    .info("Pipeline completed with Interrupted phase (failure handled)");
                ctx.logger.info(
                    "Completion marker was written during failure handling. \
                     External orchestration can detect termination via .agent/tmp/completion_marker",
                );
            }
            _ => {
                ctx.logger
                    .success("Pipeline completed via reducer event loop");
            }
        }
        ctx.logger.info(&format!(
            "Total events processed: {}",
            loop_result.events_processed
        ));
        return;
    }

    ctx.logger
        .error("⚠️  EXCEPTIONAL: Pipeline exited without normal completion");
    ctx.logger.warn(&format!(
        "This indicates a bug in the event loop or reducer. \
         Expected final phase: Complete or Interrupted+checkpoint. \
         Actual: completed=false, final_phase={:?}, events_processed={}",
        loop_result.final_phase, loop_result.events_processed
    ));

    if matches!(
        loop_result.final_phase,
        crate::reducer::event::PipelinePhase::AwaitingDevFix
    ) {
        ctx.logger.error(
            "BUG DETECTED: Event loop exited from AwaitingDevFix without completing dev-fix flow. \
             This should transition to Interrupted and save checkpoint. \
             Check: Was TriggerDevFixFlow executed? Was completion marker written? \
             See .agent/tmp/event_loop_trace.jsonl for execution trace.",
        );
    }

    write_defensive_completion_marker(&*ctx.workspace, &ctx.logger, loop_result.final_phase);
}

fn should_exit_due_to_sigint(loop_result: &crate::app::event_loop::EventLoopResult) -> bool {
    loop_result.final_state.interrupted_by_user || crate::interrupt::user_interrupted_occurred()
}

fn save_complete_checkpoint_if_needed(
    ctx: &PipelineContext,
    config: &crate::config::Config,
    run_context: &crate::checkpoint::RunContext,
    phase_ctx: &PhaseContext<'_>,
    loop_result: &crate::app::event_loop::EventLoopResult,
) {
    if !config.features.checkpoint_enabled
        || !should_write_complete_checkpoint(loop_result.final_phase)
    {
        return;
    }

    let builder = CheckpointBuilder::new()
        .phase(
            PipelinePhase::Complete,
            config.developer_iters,
            config.developer_iters,
        )
        .reviewer_pass(config.reviewer_reviews, config.reviewer_reviews)
        .capture_from_context(
            config,
            &ctx.registry,
            &ctx.developer_agent,
            &ctx.reviewer_agent,
            &ctx.logger,
            run_context,
        )
        .with_executor_from_context(std::sync::Arc::clone(&ctx.executor));

    let builder = builder
        .with_execution_history(phase_ctx.execution_history.clone())
        .with_prompt_history(loop_result.final_state.prompt_history.clone())
        .with_log_run_id(ctx.run_log_context.run_id().to_string());

    if let Some(checkpoint) = builder.build_with_workspace(&*ctx.workspace) {
        let mut checkpoint = checkpoint;
        checkpoint.dev_fix_attempt_count = loop_result.final_state.dev_fix_attempt_count;
        checkpoint.recovery_epoch = loop_result.final_state.recovery_epoch;
        checkpoint.recovery_escalation_level = loop_result.final_state.recovery_escalation_level;
        checkpoint.failed_phase_for_recovery = loop_result.final_state.failed_phase_for_recovery;
        checkpoint.interrupted_by_user = loop_result.final_state.interrupted_by_user;
        if loop_result.final_state.cloud.enabled {
            checkpoint.cloud_state = Some(
                crate::checkpoint::state::CloudCheckpointState::from_pipeline_state(
                    &loop_result.final_state,
                ),
            );
        }
        let _ = save_checkpoint_with_workspace(&*ctx.workspace, &checkpoint);
    }
}

fn report_cloud_completion(
    ctx: &PipelineContext,
    config: &crate::config::Config,
    cloud_reporter: &dyn crate::cloud::CloudReporter,
    loop_result: &crate::app::event_loop::EventLoopResult,
    timer: &Timer,
) -> anyhow::Result<()> {
    if !config.cloud.enabled {
        return Ok(());
    }

    let result_payload = build_cloud_completion_payload(loop_result, timer);
    if let Err(e) = cloud_reporter.report_completion(&result_payload) {
        let error = crate::cloud::redaction::redact_secrets(&e.to_string());
        if !config.cloud.graceful_degradation {
            return Err(anyhow::anyhow!("Cloud completion report failed: {error}"));
        }
        ctx.logger
            .warn(&format!("Cloud completion report failed: {error}"));
    }

    Ok(())
}

fn finish_pipeline(
    ctx: &PipelineContext,
    config: &crate::config::Config,
    timer: &Timer,
    agent_phase_guard: &mut AgentPhaseGuard<'_>,
    prompt_monitor: &mut Option<PromptMonitor>,
    loop_result: &crate::app::event_loop::EventLoopResult,
    exit_after_cleanup_due_to_sigint: bool,
) -> anyhow::Result<()> {
    check_prompt_restoration(ctx, prompt_monitor, "event loop");
    update_status_with_workspace(&*ctx.workspace, "In progress.", config.isolation_mode)?;

    if !exit_after_cleanup_due_to_sigint {
        finalize_pipeline(
            agent_phase_guard,
            crate::app::finalization::FinalizeContext {
                logger: &ctx.logger,
                colors: ctx.colors,
                config,
                timer,
                workspace: &*ctx.workspace,
            },
            &loop_result.final_state,
            prompt_monitor.take(),
        );
    }

    if exit_after_cleanup_due_to_sigint {
        // Perform explicit cleanup even on interrupt path.
        // The guard's Drop is a fallback, but we want deterministic cleanup with
        // all artifact types removed. All operations are idempotent so double-cleanup
        // from the guard's Drop (if it fires) is harmless.
        let repo_root = ctx.workspace.root();
        let mut cleanup_ok = true;
        crate::git_helpers::end_agent_phase_in_repo(repo_root);
        crate::git_helpers::disable_git_wrapper(agent_phase_guard.git_helpers);
        if let Err(err) = crate::git_helpers::uninstall_hooks_in_repo(repo_root, &ctx.logger) {
            if err.kind() == std::io::ErrorKind::NotFound {
                ctx.logger.warn(&format!(
                    "Skipping hook uninstall during SIGINT cleanup (repo not present on filesystem): {err}"
                ));
            } else {
                cleanup_ok = false;
                ctx.logger.warn(&format!(
                    "Failed to uninstall Ralph hooks during SIGINT cleanup: {err}"
                ));
            }
        }

        let wrapper_remaining = crate::git_helpers::verify_wrapper_cleaned(repo_root);
        if !wrapper_remaining.is_empty() {
            cleanup_ok = false;
            ctx.logger.warn(&format!(
                "Wrapper artifacts still present after SIGINT cleanup: {}",
                wrapper_remaining.join(", ")
            ));
        }

        match crate::git_helpers::verify_hooks_removed(repo_root) {
            Ok(remaining) => {
                if !remaining.is_empty() {
                    cleanup_ok = false;
                    ctx.logger.warn(&format!(
                        "Ralph hooks still present after SIGINT cleanup: {}",
                        remaining.join(", ")
                    ));
                }
            }
            Err(err) => {
                if err.kind() == std::io::ErrorKind::NotFound {
                    ctx.logger.warn(&format!(
                        "Skipping hook cleanup verification during SIGINT cleanup (repo not present on filesystem): {err}"
                    ));
                } else {
                    cleanup_ok = false;
                    ctx.logger.warn(&format!(
                        "Failed to verify hook cleanup during SIGINT cleanup: {err}"
                    ));
                }
            }
        }

        crate::files::cleanup_generated_files_with_workspace(&*ctx.workspace);
        if cleanup_ok {
            agent_phase_guard.disarm();
        } else {
            ctx.logger.warn(
                "SIGINT cleanup incomplete; leaving AgentPhaseGuard armed for Drop best-effort",
            );
        }
        crate::interrupt::request_exit_130_after_run();
    }

    Ok(())
}

fn build_cloud_completion_payload(
    loop_result: &crate::app::event_loop::EventLoopResult,
    timer: &Timer,
) -> crate::cloud::types::PipelineResult {
    let success = loop_result.completed
        && matches!(
            loop_result.final_phase,
            crate::reducer::event::PipelinePhase::Complete
        );

    crate::cloud::types::PipelineResult {
        success,
        commit_sha: loop_result.final_state.last_pushed_commit.clone().or_else(
            || match &loop_result.final_state.commit {
                crate::reducer::state::CommitState::Committed { hash } => Some(hash.clone()),
                _ => None,
            },
        ),
        pr_url: loop_result.final_state.pr_url.clone(),
        push_count: loop_result.final_state.push_count,
        last_pushed_commit: loop_result.final_state.last_pushed_commit.clone(),
        unpushed_commits: loop_result.final_state.unpushed_commits.clone(),
        last_push_error: loop_result.final_state.last_push_error.clone(),
        iterations_used: loop_result.final_state.metrics.dev_iterations_completed,
        review_passes_used: loop_result.final_state.metrics.review_passes_completed,
        issues_found: loop_result.final_state.review_issues_found,
        duration_secs: timer.elapsed().as_secs(),
        error_message: if matches!(
            loop_result.final_phase,
            crate::reducer::event::PipelinePhase::Interrupted
        ) {
            Some("Pipeline interrupted".to_string())
        } else {
            None
        },
    }
}

#[cfg(test)]
mod cloud_completion_payload_tests {
    use super::{build_cloud_completion_payload, should_exit_due_to_sigint};

    #[test]
    fn completion_payload_reports_completed_iteration_and_review_counts_from_metrics() {
        let mut state = crate::reducer::PipelineState::initial(10, 5);
        state.metrics.dev_iterations_completed = 3;
        state.metrics.review_passes_completed = 2;
        state.iteration = 4;
        state.reviewer_pass = 3;

        let loop_result = crate::app::event_loop::EventLoopResult {
            completed: true,
            events_processed: 0,
            final_phase: crate::reducer::event::PipelinePhase::Complete,
            final_state: state,
        };

        let timer = crate::pipeline::Timer::new();
        let payload = build_cloud_completion_payload(&loop_result, &timer);

        assert_eq!(
            payload.iterations_used, 3,
            "iterations_used should report completed dev iterations (metrics)"
        );
        assert_eq!(
            payload.review_passes_used, 2,
            "review_passes_used should report completed review passes (metrics)"
        );
    }

    #[test]
    fn should_exit_due_to_sigint_uses_persistent_interrupt_flag_after_request_is_consumed() {
        use crate::interrupt::{
            request_user_interrupt, reset_user_interrupted_occurred, take_user_interrupt_request,
        };

        // The interrupt flags are process-global; coordinate all test access so
        // parallel tests can't steal each other's pending interrupt requests.
        let _lock = crate::interrupt::interrupt_test_lock();

        let _ = take_user_interrupt_request();
        reset_user_interrupted_occurred();

        request_user_interrupt();
        assert!(
            take_user_interrupt_request(),
            "test precondition: interrupt request should be pending before explicit consume"
        );

        let loop_result = crate::app::event_loop::EventLoopResult {
            completed: true,
            events_processed: 0,
            final_phase: crate::reducer::event::PipelinePhase::Complete,
            final_state: crate::reducer::PipelineState::initial(1, 0),
        };

        assert!(
            should_exit_due_to_sigint(&loop_result),
            "shutdown path should still detect Ctrl+C after event loop consumed pending request"
        );

        let _ = take_user_interrupt_request();
        reset_user_interrupted_occurred();
    }
}

#[cfg(test)]
mod completion_checkpoint_tests {
    use super::save_complete_checkpoint_if_needed;
    use crate::agents::AgentRegistry;
    use crate::app::context::PipelineContext;
    use crate::checkpoint::execution_history::ExecutionHistory;
    use crate::checkpoint::{load_checkpoint_with_workspace, RunContext};
    use crate::cli::Args;
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::logging::RunLogContext;
    use crate::phases::PhaseContext;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::PipelineState;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    #[test]
    fn completion_checkpoint_preserves_recovery_epoch_for_idempotent_resume() {
        // Arrange
        let workspace = Arc::new(MemoryWorkspace::new_test().with_dir(".agent/tmp"));
        let run_log_context = RunLogContext::new(&*workspace).expect("run log context");

        let colors = Colors { enabled: false };
        let logger = Logger::new(colors);

        let registry = AgentRegistry::new().expect("registry");
        let executor =
            Arc::new(MockProcessExecutor::new()) as Arc<dyn crate::executor::ProcessExecutor>;

        let config = Config::default();
        // `CheckpointBuilder::capture_from_context` requires agent configs to exist in the
        // registry; use a known built-in agent key.
        let developer_agent = "codex".to_string();
        let reviewer_agent = "codex".to_string();

        let ctx = PipelineContext {
            args: Args::default(),
            config,
            registry,
            developer_agent,
            reviewer_agent,
            developer_display: "codex".to_string(),
            reviewer_display: "codex".to_string(),
            repo_root: PathBuf::from("/test/repo"),
            workspace: Arc::clone(&workspace) as Arc<dyn crate::workspace::Workspace>,
            logger,
            colors,
            template_context: TemplateContext::default(),
            executor: Arc::clone(&executor),
            run_log_context,
        };

        let run_context = RunContext::new();

        let mut timer = crate::pipeline::Timer::new();
        let phase_ctx = PhaseContext {
            config: &ctx.config,
            registry: &ctx.registry,
            logger: &ctx.logger,
            colors: &ctx.colors,
            timer: &mut timer,
            developer_agent: &ctx.developer_agent,
            reviewer_agent: &ctx.reviewer_agent,
            review_guidelines: None,
            template_context: &ctx.template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: executor.as_ref(),
            executor_arc: Arc::clone(&executor),
            repo_root: std::path::Path::new("/test/repo"),
            workspace: &*workspace,
            workspace_arc: Arc::clone(&ctx.workspace),
            run_log_context: &ctx.run_log_context,
            cloud_reporter: None,
            cloud: &ctx.config.cloud,
        };

        let mut final_state = PipelineState::initial(1, 0);
        final_state.recovery_epoch = 5;

        let loop_result = crate::app::event_loop::EventLoopResult {
            completed: true,
            events_processed: 0,
            final_phase: PipelinePhase::Complete,
            final_state,
        };

        // Act
        save_complete_checkpoint_if_needed(
            &ctx,
            &ctx.config,
            &run_context,
            &phase_ctx,
            &loop_result,
        );

        // Assert
        let checkpoint = load_checkpoint_with_workspace(&*workspace)
            .expect("checkpoint load")
            .expect("checkpoint exists");

        assert_eq!(
            checkpoint.recovery_epoch, 5,
            "completion checkpoint must preserve reducer recovery_epoch"
        );
    }
}
