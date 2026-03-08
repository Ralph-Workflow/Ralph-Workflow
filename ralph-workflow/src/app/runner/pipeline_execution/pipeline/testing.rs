/// Runs the pipeline with a custom effect handler for testing.
///
/// This function is only available with the `test-utils` feature and allows
/// injecting a `MockEffectHandler` to prevent real git operations during tests.
///
/// # Arguments
///
/// * `ctx` - Pipeline context
/// * `effect_handler` - Custom effect handler (e.g., `MockEffectHandler`)
///
/// # Type Parameters
///
/// * `H` - Effect handler type that implements `EffectHandler` and `StatefulHandler`
///
/// # Errors
///
/// Returns an error if:
/// - Resume validation fails
/// - Pipeline execution fails
/// - Effect handling fails
#[cfg(feature = "test-utils")]
pub fn run_pipeline_with_effect_handler<'ctx, H>(
    ctx: &PipelineContext,
    effect_handler: &mut H,
) -> anyhow::Result<()>
where
    H: crate::reducer::EffectHandler<'ctx> + crate::app::event_loop::StatefulHandler,
{
    use crate::app::event_loop::EventLoopConfig;
    use crate::checkpoint::RunContext;
    use crate::reducer::PipelineState;

    // First, offer interactive resume if checkpoint exists without --resume flag
    let resume_result = offer_resume_if_checkpoint_exists(
        &ctx.args,
        &ctx.config,
        &ctx.registry,
        &ctx.logger,
        &ctx.developer_agent,
        &ctx.reviewer_agent,
        &*ctx.workspace,
    );

    // If interactive resume didn't happen, check for --resume flag
    let resume_result = match resume_result {
        Some(result) => Some(result),
        None => handle_resume_with_validation(
            &ctx.args,
            &ctx.config,
            &ctx.registry,
            &ctx.logger,
            &ctx.developer_display,
            &ctx.reviewer_display,
            &*ctx.workspace,
        )?,
    };

    let resume_checkpoint = resume_result.map(|r| r.checkpoint);

    // Create run context - either new or from checkpoint
    let run_context = resume_checkpoint
        .as_ref()
        .map_or_else(RunContext::new, RunContext::from_checkpoint);

    // Apply checkpoint configuration restoration if resuming
    let config = resume_checkpoint.as_ref().map_or_else(
        || ctx.config.clone(),
        |checkpoint| {
            use crate::checkpoint::apply_checkpoint_to_config;
            let mut restored_config = ctx.config.clone();
            apply_checkpoint_to_config(&mut restored_config, checkpoint);
            restored_config
        },
    );

    // Set up git helpers and agent phase
    // Use workspace-aware versions when test-utils feature is enabled
    // to avoid real git operations that would cause test failures.
    let mut git_helpers = crate::git_helpers::GitHelpers::new();

    #[cfg(feature = "test-utils")]
    {
        use crate::git_helpers::{
            cleanup_orphaned_marker_with_workspace, create_marker_with_workspace,
        };
        // Use workspace-based operations that don't require real git
        cleanup_orphaned_marker_with_workspace(&*ctx.workspace, &ctx.logger)?;
        create_marker_with_workspace(&*ctx.workspace)?;
        // Skip hook installation and git wrapper in test mode
    }
    #[cfg(not(feature = "test-utils"))]
    {
        cleanup_orphaned_marker(&ctx.logger)?;
        start_agent_phase(&mut git_helpers)?;
    }
    let mut agent_phase_guard =
        AgentPhaseGuard::new(&mut git_helpers, &ctx.logger, &*ctx.workspace);

    // Print welcome banner and validate PROMPT.md
    print_welcome_banner(ctx.colors, &ctx.developer_display, &ctx.reviewer_display);
    print_pipeline_info_with_config(ctx, &config);
    validate_prompt_and_setup_backup(ctx)?;

    // Set up PROMPT.md monitoring
    let mut prompt_monitor = setup_prompt_monitor(ctx);

    // Detect project stack and review guidelines
    let (_project_stack, review_guidelines) =
        detect_project_stack(&config, &ctx.repo_root, &ctx.logger, ctx.colors);

    print_review_guidelines(ctx, review_guidelines.as_ref());
    println!();

    // Initialize cloud reporter (for testing, always use Noop)
    let cloud_reporter = crate::cloud::NoopCloudReporter;

    // Create phase context and save starting commit
    let mut timer = Timer::new();
    let mut phase_ctx = create_phase_context_with_config(
        ctx,
        &config,
        &mut timer,
        review_guidelines.as_ref(),
        &run_context,
        resume_checkpoint.as_ref(),
        &cloud_reporter,
    );
    save_start_commit_or_warn(ctx);

    // Set up interrupt context for checkpoint saving on Ctrl+C
    let initial_phase = resume_checkpoint
        .as_ref()
        .map_or(PipelinePhase::Planning, |checkpoint| checkpoint.phase);
    let initial_prompt_history: std::collections::HashMap<String, crate::prompts::PromptHistoryEntry> =
        resume_checkpoint
            .as_ref()
            .and_then(|c| c.prompt_history.clone())
            .unwrap_or_default();

    setup_interrupt_context_for_pipeline(
        initial_phase,
        config.developer_iters,
        config.reviewer_reviews,
        &phase_ctx.execution_history,
        &initial_prompt_history,
        &run_context,
        std::sync::Arc::clone(&ctx.workspace),
    );

    // Ensure interrupt context is cleared on completion
    let _interrupt_guard = defer_clear_interrupt_context();

    // Determine if we should run rebase based on current args only.
    let should_run_rebase = ctx.args.rebase_flags.with_rebase;

    // Update interrupt context before entering the reducer event loop.
    update_interrupt_context_from_phase(
        &phase_ctx.execution_history,
        initial_prompt_history,
        initial_phase,
        config.developer_iters,
        config.reviewer_reviews,
        &run_context,
        std::sync::Arc::clone(&ctx.workspace),
    );

    // Initialize pipeline state
    let mut initial_state = if let Some(ref checkpoint) = resume_checkpoint {
        // Restore progress from checkpoint, but keep budgets/limits config-driven.
        // Initialize a config-aware base state first, then overlay checkpoint progress.
        let mut base_state = crate::app::event_loop::create_initial_state_with_config(&phase_ctx);
        let migrated = PipelineState::from_checkpoint_with_execution_history_limit(
            checkpoint.clone(),
            phase_ctx.config.execution_history_limit,
        );

        crate::app::event_loop::overlay_checkpoint_progress_onto_base_state(
            &mut base_state,
            migrated,
            phase_ctx.config.execution_history_limit,
        );

        base_state
    } else {
        crate::app::event_loop::create_initial_state_with_config(&phase_ctx)
    };

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

    // Configure event loop
    let event_loop_config = EventLoopConfig {
        max_iterations: event_loop::MAX_EVENT_LOOP_ITERATIONS,
    };

    // Run event loop with the provided handler
    effect_handler.update_state(initial_state.clone());
    let loop_result = {
        use crate::app::event_loop::run_event_loop_with_handler;
        let phase_ctx_ref = &mut phase_ctx;
        run_event_loop_with_handler(
            phase_ctx_ref,
            Some(initial_state),
            event_loop_config,
            effect_handler,
        )
    };

    // Handle event loop result
    let loop_result = loop_result?;
    if loop_result.completed {
        ctx.logger
            .success("Pipeline completed successfully via reducer event loop");
        ctx.logger.info(&format!(
            "Total events processed: {}",
            loop_result.events_processed
        ));
    } else {
        ctx.logger.warn("Pipeline exited without completion marker");
        // Mirror production runner: write a defensive completion marker so orchestration can
        // reliably detect termination even when the event loop fails unexpectedly.
        write_defensive_completion_marker(&*ctx.workspace, &ctx.logger, loop_result.final_phase);
    }

    // Save Complete checkpoint before clearing (for idempotent resume)
    if config.features.checkpoint_enabled
        && should_write_complete_checkpoint(loop_result.final_phase)
    {
        let builder = CheckpointBuilder::new()
            .phase(
                PipelinePhase::Complete,
                config.developer_iters,
                config.developer_iters,
            )
            .reviewer_pass(config.reviewer_reviews, config.reviewer_reviews)
            .capture_from_context(
                &config,
                &ctx.registry,
                &ctx.developer_agent,
                &ctx.reviewer_agent,
                &ctx.logger,
                &run_context,
            )
            .with_executor_from_context(std::sync::Arc::clone(&ctx.executor));

        let builder = builder
            .with_execution_history(phase_ctx.execution_history.clone())
            .with_prompt_history(loop_result.final_state.prompt_history.clone());

        if let Some(checkpoint) = builder.build_with_workspace(&*ctx.workspace) {
            let _ = save_checkpoint_with_workspace(&*ctx.workspace, &checkpoint);
        }
    }

    // Post-pipeline operations
    check_prompt_restoration(ctx, &mut prompt_monitor, "event loop");
    update_status_with_workspace(&*ctx.workspace, "In progress.", config.isolation_mode)?;

    // Commit phase
    finalize_pipeline(
        &mut agent_phase_guard,
        crate::app::finalization::FinalizeContext {
            logger: &ctx.logger,
            colors: ctx.colors,
            config: &config,
            timer: &timer,
            workspace: &*ctx.workspace,
        },
        &loop_result.final_state,
        prompt_monitor,
    );
    Ok(())
}
