// Pipeline Event Loop Execution
//
// This module contains the core pipeline execution logic using the reducer-based event loop.
//
// Architecture:
//
// The pipeline follows the reducer pattern:
// State → Orchestrator → Effect → Handler → Event → Reducer → State
//
// Execution Flow:
//
// 1. Resume Handling: Check for existing checkpoint and offer interactive resume
// 2. State Initialization: Create or restore pipeline state from checkpoint
// 3. Context Setup: Configure interrupt handlers, git helpers, monitoring
// 4. Event Loop: Run the reducer event loop until completion
// 5. Finalization: Write completion checkpoint, cleanup, restore PROMPT.md
//
// Checkpoint and Resume:
//
// - Fresh run: Creates new `RunContext` with UUID, initializes state
// - Resume: Restores state from checkpoint, applies config overrides, restores env vars
// - Completion: Saves final checkpoint with Complete phase for idempotent resume
//
// Event Loop Result Handling:
//
// The event loop returns `EventLoopResult`:
// - `completed=true`: Normal completion (Complete or Interrupted phase)
// - `completed=false`: Abnormal exit (bug in event loop or reducer)
//
// When `completed=false`, we write a defensive completion marker to ensure
// external orchestrators can detect termination.

include!("execution_core_resume.rs");
include!("execution_core_phases.rs");
include!("execution_core_finish.rs");

/// Runs the pipeline with the default `MainEffectHandler`.
///
/// This is the production entry point - it creates a `MainEffectHandler` internally.
pub(super) fn run_pipeline_with_default_handler(ctx: &PipelineContext) -> anyhow::Result<()> {
    let resume_state = load_resume_and_config_state(ctx)?;
    let mut git_helpers = crate::git_helpers::GitHelpers::new();
    prepare_agent_phase(ctx, &mut git_helpers);
    let mut agent_phase_guard = AgentPhaseGuard::new(&mut git_helpers, &ctx.logger, &*ctx.workspace);

    let (cloud_reporter, _heartbeat_guard) = create_cloud_runtime(&resume_state.config);

    print_welcome_banner(ctx.colors, &ctx.developer_display, &ctx.reviewer_display);
    print_pipeline_info_with_config(ctx, &resume_state.config);
    validate_prompt_and_setup_backup(ctx)?;

    let mut prompt_monitor = setup_prompt_monitor(ctx);
    let (_project_stack, review_guidelines) = detect_project_stack(
        &resume_state.config,
        &ctx.repo_root,
        &ctx.logger,
        ctx.colors,
    );
    print_review_guidelines(ctx, review_guidelines.as_ref());
    println!();

    let mut timer = Timer::new();
    let mut phase_ctx = create_phase_context_with_config(
        ctx,
        &resume_state.config,
        &mut timer,
        review_guidelines.as_ref(),
        &resume_state.run_context,
        resume_state.resume_checkpoint.as_ref(),
        cloud_reporter.as_ref(),
    );
    save_start_commit_or_warn(ctx);

    let initial_phase = resume_state
        .resume_checkpoint
        .as_ref()
        .map_or(PipelinePhase::Planning, |checkpoint| checkpoint.phase);

    setup_interrupt_context_for_pipeline(
        initial_phase,
        resume_state.config.developer_iters,
        resume_state.config.reviewer_reviews,
        &phase_ctx.execution_history,
        &phase_ctx.prompt_history,
        &resume_state.run_context,
        std::sync::Arc::clone(&ctx.workspace),
    );

    let _interrupt_guard = defer_clear_interrupt_context();

    update_interrupt_context_from_phase(
        &phase_ctx,
        initial_phase,
        resume_state.config.developer_iters,
        resume_state.config.reviewer_reviews,
        &resume_state.run_context,
        std::sync::Arc::clone(&ctx.workspace),
    );

    let initial_state = compute_initial_state(
        &phase_ctx,
        resume_state.resume_checkpoint.as_ref(),
        ctx.args.rebase_flags.with_rebase,
    );

    let loop_result = run_event_loop_with_default_handler(&mut phase_ctx, initial_state)?;
    log_event_loop_outcome(ctx, &loop_result);

    let exit_after_cleanup_due_to_sigint = should_exit_due_to_sigint(&loop_result);

    save_complete_checkpoint_if_needed(
        ctx,
        &resume_state.config,
        &resume_state.run_context,
        &phase_ctx,
        &loop_result,
    );

    report_cloud_completion(
        ctx,
        &resume_state.config,
        cloud_reporter.as_ref(),
        &loop_result,
        &timer,
    )?;

    finish_pipeline(
        ctx,
        &resume_state.config,
        &timer,
        &mut agent_phase_guard,
        &mut prompt_monitor,
        &loop_result,
        exit_after_cleanup_due_to_sigint,
    )
}
