// Pipeline Finish and Completion Reporting
//
// This module handles the post-event-loop phase of pipeline execution:
// logging the outcome, saving the completion checkpoint, reporting to cloud,
// and finalizing the pipeline state.

fn log_event_loop_outcome(
    ctx: &crate::app::context::PipelineContext,
    loop_result: &crate::app::config::EventLoopResult,
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

    crate::app::runner::pipeline_execution::write_defensive_completion_marker(
        &*ctx.workspace,
        &ctx.logger,
        loop_result.final_phase,
    );
}

fn should_exit_due_to_sigint(loop_result: &crate::app::config::EventLoopResult) -> bool {
    loop_result.final_state.interrupted_by_user || crate::interrupt::user_interrupted_occurred()
}

fn save_complete_checkpoint_if_needed(
    ctx: &crate::app::context::PipelineContext,
    config: &crate::config::Config,
    run_context: &crate::checkpoint::RunContext,
    phase_ctx: &crate::phases::PhaseContext<'_>,
    loop_result: &crate::app::config::EventLoopResult,
) {
    if !config.features.checkpoint_enabled
        || !should_write_complete_checkpoint(loop_result.final_phase)
    {
        return;
    }

    let builder = crate::checkpoint::CheckpointBuilder::new()
        .phase(
            crate::checkpoint::PipelinePhase::Complete,
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
        let checkpoint = checkpoint.with_recovery_state(&loop_result.final_state);
        let _ = crate::checkpoint::save_checkpoint_with_workspace(&*ctx.workspace, &checkpoint);
    }
}

fn report_cloud_completion(
    ctx: &crate::app::context::PipelineContext,
    config: &crate::config::Config,
    cloud_reporter: &dyn crate::cloud::CloudReporter,
    loop_result: &crate::app::config::EventLoopResult,
    timer: &crate::pipeline::Timer,
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
    ctx: &crate::app::context::PipelineContext,
    config: &crate::config::Config,
    timer: &crate::pipeline::Timer,
    agent_phase_guard: &mut crate::pipeline::AgentPhaseGuard<'_>,
    prompt_monitor: &mut Option<crate::files::protection::monitoring::PromptMonitor>,
    loop_result: &crate::app::config::EventLoopResult,
    exit_after_cleanup_due_to_sigint: bool,
) -> anyhow::Result<()> {
    crate::app::runner::pipeline_execution::check_prompt_restoration(
        ctx,
        prompt_monitor,
        "event loop",
    );
    crate::files::update_status_with_workspace(
        &*ctx.workspace,
        "In progress.",
        config.isolation_mode,
    )?;

    if !exit_after_cleanup_due_to_sigint {
        crate::app::finalization::finalize_pipeline(
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
        let repo_root = ctx.workspace.root();
        crate::git_helpers::end_agent_phase_in_repo(repo_root);
        crate::git_helpers::disable_git_wrapper(agent_phase_guard.git_helpers);

        let hook_uninstall_ok = match crate::git_helpers::uninstall_hooks_in_repo(
            repo_root,
            &ctx.logger,
        ) {
            Ok(()) => true,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                ctx.logger.warn(&format!(
                    "Skipping hook uninstall during SIGINT cleanup (repo not present on filesystem): {err}"
                ));
                true
            }
            Err(err) => {
                ctx.logger.warn(&format!(
                    "Failed to uninstall Ralph hooks during SIGINT cleanup: {err}"
                ));
                false
            }
        };

        let wrapper_remaining = crate::git_helpers::verify_wrapper_cleaned(repo_root);
        let wrapper_ok = wrapper_remaining.is_empty();
        if !wrapper_ok {
            ctx.logger.warn(&format!(
                "Wrapper artifacts still present after SIGINT cleanup: {}",
                wrapper_remaining.join(", ")
            ));
        }

        let hooks_ok = match crate::git_helpers::verify_hooks_removed(repo_root) {
            Ok(remaining) => {
                if !remaining.is_empty() {
                    ctx.logger.warn(&format!(
                        "Ralph hooks still present after SIGINT cleanup: {}",
                        remaining.join(", ")
                    ));
                    false
                } else {
                    true
                }
            }
            Err(err) => {
                if err.kind() == std::io::ErrorKind::NotFound {
                    ctx.logger.warn(&format!(
                        "Skipping hook cleanup verification during SIGINT cleanup (repo not present on filesystem): {err}"
                    ));
                    true
                } else {
                    ctx.logger.warn(&format!(
                        "Failed to verify hook cleanup during SIGINT cleanup: {err}"
                    ));
                    false
                }
            }
        };

        crate::files::cleanup_generated_files_with_workspace(&*ctx.workspace);
        let ralph_dir_ok = if !crate::git_helpers::try_remove_ralph_dir(repo_root) {
            let remaining = crate::git_helpers::verify_ralph_dir_removed(repo_root);
            ctx.logger.warn(&format!(
                "Ralph git dir still present after SIGINT cleanup: {}",
                remaining.join(", ")
            ));
            false
        } else {
            true
        };

        let cleanup_ok = hook_uninstall_ok && wrapper_ok && hooks_ok && ralph_dir_ok;

        if cleanup_ok {
            crate::git_helpers::clear_agent_phase_global_state();
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
    loop_result: &crate::app::config::EventLoopResult,
    timer: &crate::pipeline::Timer,
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
