//! Error handling and panic recovery for the event loop.
//!
//! This module provides infrastructure for recovering from handler errors and panics
//! while maintaining the non-terminating pipeline guarantee. All errors are routed
//! through the reducer state machine to ensure proper remediation flow.

use crate::app::core::StatefulHandler;
use crate::app::trace::{dump_event_loop_trace, EventTraceBuffer};
use crate::logging::EventLoopLogger;
use crate::phases::PhaseContext;
use crate::reducer::effect::{Effect, EffectResult};
use crate::reducer::event::PipelineEvent;
use crate::reducer::PipelineState;
use crate::reducer::{reduce, EffectHandler};
use std::path::Path;
use std::time::Instant;

/// Extract `ErrorEvent` from `anyhow::Error` if present.
///
/// # Error Event Processing Architecture
///
/// Effect handlers return errors through `Err(ErrorEvent::Variant.into())`. This function
/// extracts the original `ErrorEvent` so it can be processed through the reducer.
///
/// ## Why Downcast?
///
/// When an effect handler returns `Err(ErrorEvent::Variant.into())`, the error is wrapped
/// in an `anyhow::Error`. Since `ErrorEvent` implements `std::error::Error`, anyhow's
/// blanket `From` implementation preserves the original error type, allowing us to downcast
/// back to `ErrorEvent` for reducer processing.
///
/// ## Processing Flow
///
/// 1. Handler returns `Err(ErrorEvent::AgentChainExhausted { ... }.into())`
/// 2. Event loop catches the error and calls this function
/// 3. If downcast succeeds, wrap in `PipelineEvent::PromptInput(PromptInputEvent::HandlerError { ... })`
///    and process through reducer
/// 4. If downcast fails, return `Err()` to terminate the event loop (truly unrecoverable error)
///
/// This architecture allows the reducer to decide recovery strategy based on the specific
/// error type, rather than terminating immediately on any `Err()`.
pub(super) fn extract_error_event(
    err: &anyhow::Error,
) -> Option<crate::reducer::event::ErrorEvent> {
    err.chain()
        .find_map(|cause| cause.downcast_ref::<crate::reducer::event::ErrorEvent>())
        .cloned()
}

/// Result of guarded effect execution.
///
/// Distinguishes between:
/// - `Ok`: Effect executed successfully, returning an `EffectResult`
/// - `Unrecoverable`: Handler returned an error that cannot be downcast to `ErrorEvent`
/// - `Panic`: Handler panicked during execution
pub(super) enum GuardedEffectResult {
    Ok(Box<EffectResult>),
    Unrecoverable(anyhow::Error),
    Panic,
}

/// Execute an effect with panic recovery.
///
/// Catches panics from the effect handler and converts them to `GuardedEffectResult::Panic`.
/// Handler errors are analyzed via `extract_error_event()`:
/// - If the error is a typed `ErrorEvent`, it's wrapped as a `HandlerError` event
/// - Otherwise, it's returned as `Unrecoverable`
pub(super) fn execute_effect_guarded<'ctx, H>(
    handler: &mut H,
    effect: Effect,
    ctx: &mut PhaseContext<'_>,
    state: &PipelineState,
) -> GuardedEffectResult
where
    H: EffectHandler<'ctx>,
{
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        handler.execute(effect, ctx)
    })) {
        Ok(Ok(result)) => GuardedEffectResult::Ok(Box::new(result)),
        Ok(Err(err)) => extract_error_event(&err).map_or_else(
            || GuardedEffectResult::Unrecoverable(err),
            |error_event| {
                GuardedEffectResult::Ok(Box::new(crate::reducer::effect::EffectResult::event(
                    crate::reducer::event::PipelineEvent::PromptInput(
                        crate::reducer::event::PromptInputEvent::HandlerError {
                            phase: state.phase,
                            error: error_event,
                        },
                    ),
                )))
            },
        ),
        Err(_) => GuardedEffectResult::Panic,
    }
}

/// Write a completion marker on unrecoverable handler error.
///
/// This is a best-effort operation to ensure orchestration is notified even when
/// the dev-fix flow cannot execute normally. Returns `true` if the marker was
/// successfully written.
pub(super) fn write_completion_marker_on_error(
    ctx: &PhaseContext<'_>,
    err: &anyhow::Error,
) -> bool {
    if let Err(err) = ctx.workspace.create_dir_all(Path::new(".agent/tmp")) {
        ctx.logger.error(&format!(
            "Failed to create completion marker directory: {err}"
        ));
        return false;
    }

    let marker_path = Path::new(".agent/tmp/completion_marker");
    let content = format!("failure\nUnrecoverable handler error: {err}");
    match ctx.workspace.write(marker_path, &content) {
        Ok(()) => true,
        Err(err) => {
            ctx.logger.error(&format!(
                "Failed to write completion marker for unrecoverable handler error: {err}"
            ));
            false
        }
    }
}

/// Context for error recovery operations in the event loop.
///
/// Groups related parameters needed for error handling to avoid
/// excessive function parameters.
pub(super) struct ErrorRecoveryContext<'a, 'b, H>
where
    H: StatefulHandler,
{
    pub(super) ctx: &'a mut PhaseContext<'b>,
    pub(super) trace: &'a EventTraceBuffer,
    pub(super) state: &'a PipelineState,
    pub(super) effect_str: &'a str,
    pub(super) start_time: Instant,
    pub(super) handler: &'a mut H,
    pub(super) event_loop_logger: &'a mut EventLoopLogger,
}

/// Handle an unrecoverable error from the effect handler.
///
/// Routes the error through the reducer as a `HandlerError` event, transitioning
/// to `AwaitingDevFix` phase. Dumps trace and writes completion marker as best-effort.
pub(super) fn handle_unrecoverable_error<'ctx, H>(
    recovery_ctx: &mut ErrorRecoveryContext<'_, '_, H>,
    err: &anyhow::Error,
) -> PipelineState
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    let ErrorRecoveryContext {
        ctx,
        trace,
        state,
        effect_str,
        start_time,
        handler,
        event_loop_logger,
    } = recovery_ctx;
    let dumped = dump_event_loop_trace(ctx, trace, state, "unrecoverable_handler_error");
    let marker_written = write_completion_marker_on_error(ctx, err);

    if dumped {
        let trace_path = ctx.run_log_context.event_loop_trace();
        ctx.logger.error(&format!(
            "Event loop encountered unrecoverable handler error (trace: {})",
            trace_path.display()
        ));
    } else {
        ctx.logger
            .error("Event loop encountered unrecoverable handler error");
    }
    if marker_written {
        ctx.logger
            .info("Completion marker written for unrecoverable handler error");
    }

    // Emit a reducer-visible error that transitions us into AwaitingDevFix.
    let failure_event =
        PipelineEvent::PromptInput(crate::reducer::event::PromptInputEvent::HandlerError {
            phase: state.phase,
            error: crate::reducer::event::ErrorEvent::WorkspaceWriteFailed {
                path: "(unrecoverable_handler_error)".to_string(),
                kind: crate::reducer::event::WorkspaceIoErrorKind::Other,
            },
        });

    let event_str = format!("{failure_event:?}");
    let duration_ms = u64::try_from(start_time.elapsed().as_millis()).unwrap_or(u64::MAX);
    let new_state = reduce(state.clone(), failure_event);

    // Log to event loop log (best-effort)
    super::logging::log_effect_execution(
        ctx,
        event_loop_logger,
        &new_state,
        effect_str,
        &event_str,
        &[],
        duration_ms,
    );

    handler.update_state(new_state.clone());
    new_state
}

/// Handle a panic from the effect handler.
///
/// Routes the panic through the reducer as a `HandlerError` event, transitioning
/// to `AwaitingDevFix` phase. Dumps trace and writes completion marker as best-effort.
pub(super) fn handle_panic<'ctx, H>(
    recovery_ctx: &mut ErrorRecoveryContext<'_, '_, H>,
    events_processed: usize,
) -> PipelineState
where
    H: EffectHandler<'ctx> + StatefulHandler,
{
    let ErrorRecoveryContext {
        ctx,
        trace,
        state,
        effect_str,
        start_time,
        handler,
        event_loop_logger,
    } = recovery_ctx;
    let dumped = dump_event_loop_trace(ctx, trace, state, "panic");
    if dumped {
        let trace_path = ctx.run_log_context.event_loop_trace();
        ctx.logger.error(&format!(
            "Event loop recovered from panic (trace: {})",
            trace_path.display()
        ));
    } else {
        ctx.logger.error("Event loop recovered from panic");
    }

    // Best-effort completion marker for orchestration
    if let Err(err) = ctx.workspace.create_dir_all(Path::new(".agent/tmp")) {
        ctx.logger.error(&format!(
            "Failed to create completion marker directory: {err}"
        ));
    }
    let marker_path = Path::new(".agent/tmp/completion_marker");
    let content = format!(
        "failure\nHandler panic in effect execution (phase={:?}, events_processed={})",
        state.phase, events_processed
    );
    if let Err(err) = ctx.workspace.write(marker_path, &content) {
        ctx.logger.error(&format!(
            "Failed to write completion marker for handler panic: {err}"
        ));
    }

    let failure_event =
        PipelineEvent::PromptInput(crate::reducer::event::PromptInputEvent::HandlerError {
            phase: state.phase,
            error: crate::reducer::event::ErrorEvent::WorkspaceWriteFailed {
                path: "(handler_panic)".to_string(),
                kind: crate::reducer::event::WorkspaceIoErrorKind::Other,
            },
        });

    let event_str = format!("{failure_event:?}");
    let duration_ms = u64::try_from(start_time.elapsed().as_millis()).unwrap_or(u64::MAX);
    let new_state = reduce(state.clone(), failure_event);

    // Log to event loop log (best-effort)
    super::logging::log_effect_execution(
        ctx,
        event_loop_logger,
        &new_state,
        effect_str,
        &event_str,
        &[],
        duration_ms,
    );

    handler.update_state(new_state.clone());
    new_state
}
