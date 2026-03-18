//! Event loop logging helpers.

use crate::logging::EventLoopLogger;
use crate::phases::PhaseContext;
use crate::reducer::event::PipelineEvent;
use crate::reducer::PipelineState;

/// Log effect execution to the event loop log.
///
/// This is a best-effort operation - failures are logged but do not affect
/// pipeline execution since event loop logging is for observability only.
pub(super) fn log_effect_execution(
    ctx: &PhaseContext<'_>,
    event_loop_logger: &mut EventLoopLogger,
    state: &PipelineState,
    effect_str: &str,
    event_str: &str,
    additional_events: &[PipelineEvent],
    duration_ms: u64,
) {
    let extra_events: Vec<String> = additional_events.iter().map(|e| format!("{e:?}")).collect();

    let context_pairs: Vec<(&str, String)> = vec![
        ("iteration", state.iteration.to_string()),
        ("reviewer_pass", state.reviewer_pass.to_string()),
    ];
    let context_refs: Vec<(&str, &str)> = context_pairs
        .iter()
        .map(|(k, v)| (*k, v.as_str()))
        .collect();

    let (new_logger, _seq) = event_loop_logger
        .clone()
        .log_effect(&crate::logging::LogEffectParams {
            workspace: ctx.workspace,
            log_path: &ctx.run_log_context.event_loop_log(),
            phase: state.phase,
            effect: effect_str,
            primary_event: event_str,
            extra_events: &extra_events,
            duration_ms,
            context: &context_refs,
        })
        .unwrap_or_else(|e| {
            ctx.logger
                .warn(&format!("Failed to write to event loop log: {e}"));
            (event_loop_logger.clone(), 0)
        });
    *event_loop_logger = new_logger;
}
