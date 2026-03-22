pub fn check_no_resume_prompt() -> bool {
    crate::app::io::effect_io::check_no_resume_prompt()
}

pub fn is_terminal_io() -> bool {
    crate::app::io::effect_io::is_terminal_io()
}

pub fn get_current_dir() -> std::path::PathBuf {
    crate::app::io::effect_io::get_current_dir()
}

pub fn set_current_dir(path: &std::path::Path) -> std::io::Result<()> {
    crate::app::io::effect_io::set_current_dir(path)
}

pub fn get_args() -> Vec<String> {
    crate::app::io::effect_io::get_args()
}

pub fn get_program_args() -> Vec<String> {
    crate::app::io::effect_io::get_program_args()
}

pub fn get_process_id() -> u32 {
    crate::app::io::effect_io::get_process_id()
}

pub fn exit_with_code(code: i32) -> ! {
    crate::app::io::effect_io::exit_with_code(code)
}

use crate::logging::EventLoopLogger;
use crate::phases::PhaseContext;
use crate::reducer::event::{ErrorEvent, PipelineEvent, PipelinePhase, PromptInputEvent};
use crate::reducer::{determine_next_effect, reduce, EffectHandler, PipelineState};
use anyhow::Result;
use std::time::Instant;

use crate::app::cloud_progress::report_cloud_progress;
use crate::app::config::{create_initial_state_with_config, EventLoopConfig, EventLoopResult};
use crate::app::core::StatefulHandler;
use crate::app::error_handling::{
    execute_effect_guarded, handle_panic, handle_unrecoverable_error, ErrorRecoveryContext,
    GuardedEffectResult,
};
use crate::app::iteration::{should_exit_after_effect, should_exit_before_effect};
use crate::app::logging::log_effect_execution;
use crate::app::recovery::{
    handle_forced_checkpoint_after_completion, handle_max_iterations_in_awaiting_dev_fix,
    RecoveryResult,
};
use crate::app::trace::{
    build_trace_entry, dump_event_loop_trace, EventTraceBuffer, DEFAULT_EVENT_LOOP_TRACE_CAPACITY,
};

struct LoopRuntime {
    state: PipelineState,
    events_processed: usize,
    trace: EventTraceBuffer,
    event_loop_logger: EventLoopLogger,
}

#[derive(PartialEq, Eq)]
enum IterationResult {
    Continue,
    Break,
}

enum EffectExecutionOutcome {
    Continue,
    EffectResult(Box<crate::reducer::effect::EffectResult>),
}

struct MaxIterationRecovery {
    recovery_failed: bool,
}

fn create_event_loop_logger(ctx: &PhaseContext<'_>) -> EventLoopLogger {
    let event_loop_log_path = ctx.run_log_context.event_loop_log();
    match EventLoopLogger::from_existing_log(ctx.workspace, &event_loop_log_path) {
        Ok(logger) => logger,
        Err(e) => {
            ctx.logger.warn(&format!(
                "Failed to read existing event loop log, starting fresh: {e}"
            ));
            EventLoopLogger::new()
        }
    }
}

fn handle_user_interrupt<'ctx, H>(
    ctx: &PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
) -> bool
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    if !crate::interrupt::take_user_interrupt_request() {
        return false;
    }

    let effect_str = "Signal(SIGINT)".to_string();
    let interrupt_event = PipelineEvent::PromptInput(PromptInputEvent::HandlerError {
        phase: runtime.state.phase,
        error: ErrorEvent::UserInterruptRequested,
    });
    let event_str = format!("{interrupt_event:?}");
    let start_time = Instant::now();
    let new_state = reduce(runtime.state.clone(), interrupt_event);
    let duration_ms = u64::try_from(start_time.elapsed().as_millis()).unwrap_or(u64::MAX);

    log_effect_execution(
        ctx,
        &mut runtime.event_loop_logger,
        &new_state,
        &effect_str,
        &event_str,
        &[],
        duration_ms,
    );

    runtime.trace =
        std::mem::replace(&mut runtime.trace, EventTraceBuffer::new(1)).append(build_trace_entry(
            runtime.events_processed,
            &new_state,
            &effect_str,
            &event_str,
        ));
    handler.update_state(new_state.clone());
    runtime.state = new_state;
    runtime.events_processed = runtime.events_processed.saturating_add(1);
    true
}

fn execute_effect_with_recovery<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
    effect_str: &str,
    start_time: Instant,
    effect: crate::reducer::effect::Effect,
) -> EffectExecutionOutcome
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    match execute_effect_guarded(handler, effect, ctx, &runtime.state) {
        GuardedEffectResult::Ok(result) => EffectExecutionOutcome::EffectResult(result),
        GuardedEffectResult::Unrecoverable(err) => {
            let mut recovery_ctx = ErrorRecoveryContext {
                ctx,
                trace: &runtime.trace,
                state: &runtime.state,
                effect_str,
                start_time,
                handler,
                event_loop_logger: &mut runtime.event_loop_logger,
            };
            runtime.state = handle_unrecoverable_error(&mut recovery_ctx, &err);
            runtime.events_processed = runtime.events_processed.saturating_add(1);
            EffectExecutionOutcome::Continue
        }
        GuardedEffectResult::Panic => {
            let mut recovery_ctx = ErrorRecoveryContext {
                ctx,
                trace: &runtime.trace,
                state: &runtime.state,
                effect_str,
                start_time,
                handler,
                event_loop_logger: &mut runtime.event_loop_logger,
            };
            runtime.state = handle_panic(&mut recovery_ctx, runtime.events_processed);
            runtime.events_processed = runtime.events_processed.saturating_add(1);
            EffectExecutionOutcome::Continue
        }
    }
}

fn process_primary_event<'ctx, H>(
    ctx: &PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
    effect_str: &str,
    event: PipelineEvent,
    additional_events: &[PipelineEvent],
    duration_ms: u64,
) where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    let event_str = format!("{event:?}");
    let new_state = reduce(runtime.state.clone(), event);

    log_effect_execution(
        ctx,
        &mut runtime.event_loop_logger,
        &new_state,
        effect_str,
        &event_str,
        additional_events,
        duration_ms,
    );

    runtime.trace = std::mem::replace(&mut runtime.trace, EventTraceBuffer::new(1)).append(
        build_trace_entry(runtime.events_processed, &new_state, effect_str, &event_str),
    );
    handler.update_state(new_state.clone());
    runtime.state = new_state;
    runtime.events_processed = runtime.events_processed.saturating_add(1);
}

fn process_additional_events<'ctx, H>(
    handler: &mut H,
    runtime: &mut LoopRuntime,
    effect_str: &str,
    additional_events: Vec<PipelineEvent>,
) where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    let base_events_processed = runtime.events_processed;
    let trace_data: Vec<_> = additional_events
        .iter()
        .enumerate()
        .map(|(i, event)| {
            let event_str = format!("{event:?}");
            let state = reduce(runtime.state.clone(), event.clone());
            (
                build_trace_entry(base_events_processed + i, &state, effect_str, &event_str),
                state,
            )
        })
        .collect();

    let final_state = trace_data
        .last()
        .map(|(_, s)| s.clone())
        .unwrap_or_else(|| runtime.state.clone());

    trace_data.into_iter().for_each(|(entry, state)| {
        runtime.trace =
            std::mem::replace(&mut runtime.trace, EventTraceBuffer::new(1)).append(entry);
        handler.update_state(state);
    });

    runtime.state = final_state;
    runtime.events_processed = base_events_processed.saturating_add(additional_events.len());
}

fn update_loop_detection_state<'ctx, H>(handler: &mut H, runtime: &mut LoopRuntime)
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    let current_fingerprint = crate::reducer::compute_effect_fingerprint(&runtime.state);
    let continuation = runtime
        .state
        .continuation
        .clone()
        .update_loop_detection_counters(current_fingerprint);
    runtime.state = PipelineState {
        continuation,
        ..runtime.state.clone()
    };
    handler.update_state(runtime.state.clone());
}

const AWAITING_DEV_FIX_INTERRUPTION_WARNING: &str =
    "Interrupted phase reached from AwaitingDevFix without checkpoint saved. SaveCheckpoint effect should execute on next iteration.";

fn completion_transition_warning(state: &PipelineState) -> Option<&'static str> {
    if matches!(state.phase, PipelinePhase::Interrupted)
        && matches!(state.previous_phase, Some(PipelinePhase::AwaitingDevFix))
        && state.checkpoint_saved_count == 0
    {
        Some(AWAITING_DEV_FIX_INTERRUPTION_WARNING)
    } else {
        None
    }
}

fn log_ui_events(ctx: &PhaseContext<'_>, ui_events: &[crate::reducer::ui_event::UIEvent]) {
    ui_events.iter().for_each(|ui_event| {
        ctx.logger
            .info(&crate::rendering::render_ui_event(ui_event));
    });
}

fn finalize_iteration<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
    effect_str: &str,
    event: PipelineEvent,
    additional_events: Vec<PipelineEvent>,
    duration_ms: u64,
    ui_events: &[crate::reducer::ui_event::UIEvent],
) -> Result<bool>
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    log_ui_events(ctx, ui_events);

    process_primary_event(
        ctx,
        handler,
        runtime,
        effect_str,
        event,
        &additional_events,
        duration_ms,
    );
    process_additional_events(handler, runtime, effect_str, additional_events);
    update_loop_detection_state(handler, runtime);
    report_cloud_progress(ctx, &runtime.state, ui_events)?;

    Ok(log_completion_transition_if_needed(ctx, &runtime.state))
}

fn iteration_duration(start_time: Instant) -> u64 {
    u64::try_from(start_time.elapsed().as_millis()).unwrap_or(u64::MAX)
}

fn prepare_next_effect(state: &PipelineState) -> (crate::reducer::effect::Effect, String, Instant) {
    let effect = determine_next_effect(state);
    let effect_str = format!("{effect:?}");
    (effect, effect_str, Instant::now())
}

fn pre_iteration_check<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
) -> Option<IterationResult>
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    if should_exit_before_effect(&runtime.state) {
        ctx.logger.info(&format!(
            "Event loop: state already complete (phase: {:?}, checkpoint_saved_count: {})",
            runtime.state.phase, runtime.state.checkpoint_saved_count
        ));
        return Some(IterationResult::Break);
    }

    if handle_user_interrupt(ctx, handler, runtime) {
        return Some(IterationResult::Continue);
    }

    None
}

fn log_completion_transition_if_needed(ctx: &PhaseContext<'_>, state: &PipelineState) -> bool {
    if !should_exit_after_effect(state) {
        return false;
    }

    ctx.logger.info(&format!(
        "Event loop: state became complete (phase: {:?}, checkpoint_saved_count: {})",
        state.phase, state.checkpoint_saved_count
    ));

    if let Some(warning) = completion_transition_warning(state) {
        ctx.logger.warn(warning);
    }

    true
}

enum RecoveryImpact {
    Applied {
        state: PipelineState,
        events_processed: usize,
        trace_dumped: bool,
        failed: bool,
    },
    NotNeeded,
}

fn recovery_impact(result: RecoveryResult) -> RecoveryImpact {
    match result {
        RecoveryResult::Success(state, events_processed, dumped) => RecoveryImpact::Applied {
            state,
            events_processed,
            trace_dumped: dumped,
            failed: false,
        },
        RecoveryResult::FailedUnrecoverable(state, events_processed, dumped) => {
            RecoveryImpact::Applied {
                state,
                events_processed,
                trace_dumped: dumped,
                failed: true,
            }
        }
        RecoveryResult::NotNeeded => RecoveryImpact::NotNeeded,
    }
}

fn execute_effect_and_capture_result<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
    effect_str: &str,
    start_time: Instant,
    effect: crate::reducer::effect::Effect,
) -> Option<crate::reducer::effect::EffectResult>
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    match execute_effect_with_recovery(ctx, handler, runtime, effect_str, start_time, effect) {
        EffectExecutionOutcome::Continue => None,
        EffectExecutionOutcome::EffectResult(result) => Some(*result),
    }
}

fn execute_effect_and_finalize<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
    effect_str: &str,
    start_time: Instant,
    effect: crate::reducer::effect::Effect,
) -> Result<IterationResult>
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    let Some(result) =
        execute_effect_and_capture_result(ctx, handler, runtime, effect_str, start_time, effect)
    else {
        return Ok(IterationResult::Continue);
    };

    finalize_effect_result(ctx, handler, runtime, effect_str, start_time, result)
}

fn execute_single_iteration<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
) -> Result<IterationResult>
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    if let Some(pre_check) = pre_iteration_check(ctx, handler, runtime) {
        return Ok(pre_check);
    }

    let (effect, effect_str, start_time) = prepare_next_effect(&runtime.state);

    execute_effect_and_finalize(ctx, handler, runtime, &effect_str, start_time, effect)
}

fn finalize_effect_result<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
    effect_str: &str,
    start_time: Instant,
    result: crate::reducer::effect::EffectResult,
) -> Result<IterationResult>
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    let crate::reducer::effect::EffectResult {
        event,
        additional_events,
        ui_events,
    } = result;

    let duration_ms = iteration_duration(start_time);
    if finalize_iteration(
        ctx,
        handler,
        runtime,
        effect_str,
        event,
        additional_events,
        duration_ms,
        &ui_events,
    )? {
        return Ok(IterationResult::Break);
    }

    Ok(IterationResult::Continue)
}

fn apply_recovery_result(
    recovery: RecoveryResult,
    runtime: &mut LoopRuntime,
    trace_already_dumped: bool,
) -> (bool, bool) {
    match recovery_impact(recovery) {
        RecoveryImpact::Applied {
            state,
            events_processed,
            trace_dumped,
            failed,
        } => {
            runtime.state = state;
            runtime.events_processed = events_processed;
            (trace_already_dumped || trace_dumped, failed)
        }
        RecoveryImpact::NotNeeded => (trace_already_dumped, false),
    }
}

fn handle_max_iteration_recovery<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    config: EventLoopConfig,
    runtime: &mut LoopRuntime,
) -> MaxIterationRecovery
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    if !exceeded_max_iterations(runtime, &config) {
        return MaxIterationRecovery {
            recovery_failed: false,
        };
    }

    let context = collect_max_iteration_recovery_context(ctx, handler, runtime);

    ensure_trace_dumped_after_max_iterations(
        ctx,
        runtime,
        context.trace_already_dumped,
        config.max_iterations,
    );

    log_max_iterations_exit_if_needed(ctx, runtime, context.forced_completion);

    MaxIterationRecovery {
        recovery_failed: context.recovery_failed,
    }
}

fn exceeded_max_iterations(runtime: &LoopRuntime, config: &EventLoopConfig) -> bool {
    runtime.events_processed >= config.max_iterations
}

fn attempt_forced_checkpoint_recovery<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
) -> (bool, bool)
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    let checkpoint_result = handle_forced_checkpoint_after_completion(
        ctx,
        handler,
        runtime.state.clone(),
        runtime.events_processed,
        &mut runtime.trace,
    );
    apply_recovery_result(checkpoint_result, runtime, false)
}

fn attempt_dev_fix_completion<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
    trace_already_dumped: bool,
    recovery_failed: bool,
) -> (bool, bool, bool)
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    if runtime.state.is_complete() || recovery_failed {
        return (trace_already_dumped, recovery_failed, false);
    }

    let dev_fix_result = handle_max_iterations_in_awaiting_dev_fix(
        ctx,
        handler,
        runtime.state.clone(),
        runtime.events_processed,
        &mut runtime.trace,
    );
    let (trace_already_dumped, recovery_failed) =
        apply_recovery_result(dev_fix_result, runtime, trace_already_dumped);
    (trace_already_dumped, recovery_failed, !recovery_failed)
}

struct MaxIterationRecoveryContext {
    trace_already_dumped: bool,
    recovery_failed: bool,
    forced_completion: bool,
}

fn collect_max_iteration_recovery_context<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    runtime: &mut LoopRuntime,
) -> MaxIterationRecoveryContext
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    let (trace_already_dumped, recovery_failed) =
        attempt_forced_checkpoint_recovery(ctx, handler, runtime);

    let (trace_already_dumped, recovery_failed, forced_completion) =
        attempt_dev_fix_completion(ctx, handler, runtime, trace_already_dumped, recovery_failed);

    MaxIterationRecoveryContext {
        trace_already_dumped,
        recovery_failed,
        forced_completion,
    }
}

fn ensure_trace_dumped_after_max_iterations<'ctx>(
    ctx: &mut PhaseContext<'_>,
    runtime: &LoopRuntime,
    trace_already_dumped: bool,
    max_iterations: usize,
) {
    if trace_already_dumped {
        return;
    }

    let dumped = dump_event_loop_trace(ctx, &runtime.trace, &runtime.state, "max_iterations");
    if dumped {
        let trace_path = ctx.run_log_context.event_loop_trace();
        ctx.logger.warn(&format!(
            "Event loop reached max iterations ({}) without completion (trace: {})",
            max_iterations,
            trace_path.display()
        ));
    } else {
        ctx.logger.warn(&format!(
            "Event loop reached max iterations ({}) without completion",
            max_iterations
        ));
    }
}

fn log_max_iterations_exit(ctx: &PhaseContext<'_>, runtime: &LoopRuntime) {
    ctx.logger.error(&format!(
        "Event loop exiting: reason=max_iterations, phase={:?}, checkpoint_saved_count={}, events_processed={}",
        runtime.state.phase,
        runtime.state.checkpoint_saved_count,
        runtime.events_processed
    ));
}

fn log_max_iterations_exit_if_needed(
    ctx: &PhaseContext<'_>,
    runtime: &LoopRuntime,
    forced_completion: bool,
) {
    if forced_completion || runtime.state.is_complete() {
        return;
    }

    log_max_iterations_exit(ctx, runtime);
}

fn create_loop_runtime<'ctx>(
    ctx: &PhaseContext<'_>,
    initial_state: Option<PipelineState>,
) -> LoopRuntime {
    LoopRuntime {
        state: initial_state.unwrap_or_else(|| create_initial_state_with_config(ctx)),
        events_processed: 0,
        trace: EventTraceBuffer::new(DEFAULT_EVENT_LOOP_TRACE_CAPACITY),
        event_loop_logger: create_event_loop_logger(ctx),
    }
}

fn run_event_loop_iterations<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    handler: &mut H,
    config: &EventLoopConfig,
    runtime: &mut LoopRuntime,
) -> Result<()>
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    for _ in 0..config.max_iterations {
        if execute_single_iteration(ctx, handler, runtime)? == IterationResult::Break {
            break;
        }
    }

    Ok(())
}

pub fn run_event_loop_driver<'ctx, H>(
    ctx: &mut PhaseContext<'_>,
    initial_state: Option<PipelineState>,
    config: EventLoopConfig,
    handler: &mut H,
) -> Result<EventLoopResult>
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    let mut runtime = create_loop_runtime(ctx, initial_state);

    handler.update_state(runtime.state.clone());
    ctx.logger.info("Starting reducer-based event loop");

    let _event_loop_guard = crate::interrupt::event_loop_active_guard();

    run_event_loop_iterations(ctx, handler, &config, &mut runtime)?;

    let recovery = handle_max_iteration_recovery(ctx, handler, config, &mut runtime);
    let completed = runtime.state.is_complete() && !recovery.recovery_failed;

    if !completed {
        log_event_loop_non_completion(ctx, &runtime, recovery.recovery_failed);
    }

    Ok(EventLoopResult {
        completed,
        events_processed: runtime.events_processed,
        final_phase: runtime.state.phase,
        final_state: runtime.state.clone(),
    })
}

fn log_event_loop_non_completion(
    ctx: &PhaseContext<'_>,
    runtime: &LoopRuntime,
    recovery_failed: bool,
) {
    ctx.logger.warn(&format!(
        "Event loop exiting without completion: phase={:?}, checkpoint_saved_count={}, \
             previous_phase={:?}, events_processed={}, recovery_failed={}",
        runtime.state.phase,
        runtime.state.checkpoint_saved_count,
        runtime.state.previous_phase,
        runtime.events_processed,
        recovery_failed
    ));
    ctx.logger.info(&format!(
        "Final state: agent_chain.retry_cycle={}, agent_chain.active_role={:?}",
        runtime.state.agent_chain.retry_cycle,
        runtime.state.agent_chain.active_role()
    ));
}
