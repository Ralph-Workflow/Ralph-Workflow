//! Event loop trace buffer and diagnostics.
//!
//! This module provides trace collection for the event loop, capturing a ring buffer
//! of recent effect→event transitions for debugging purposes. When the loop terminates
//! or encounters an error, the trace is dumped to `.agent/logs/run-*/event_loop_trace.jsonl`
//! for post-mortem analysis.

use crate::phases::PhaseContext;
use crate::reducer::PipelineState;
use serde::Serialize;
use std::collections::VecDeque;

/// Default capacity for the event trace ring buffer (retains last 200 entries).
pub(super) const DEFAULT_EVENT_LOOP_TRACE_CAPACITY: usize = 200;

/// A single entry in the event trace, capturing state and effect/event details.
#[derive(Clone, Serialize, Debug)]
pub(in crate::app) struct EventTraceEntry {
    pub iteration: usize,
    pub effect: String,
    pub event: String,
    pub phase: String,
    pub xsd_retry_pending: bool,
    pub xsd_retry_count: u32,
    pub invalid_output_attempts: u32,
    pub agent_index: usize,
    pub model_index: usize,
    pub retry_cycle: u32,
}

/// Ring buffer for event loop trace entries.
///
/// Maintains the last N entries (where N is the configured capacity) to avoid
/// unbounded memory growth during long-running pipelines.
#[derive(Debug)]
pub(super) struct EventTraceBuffer {
    capacity: usize,
    entries: VecDeque<EventTraceEntry>,
}

impl EventTraceBuffer {
    pub(super) fn new(capacity: usize) -> Self {
        Self {
            capacity: capacity.max(1),
            entries: VecDeque::new(),
        }
    }

    pub(super) fn push(&mut self, entry: EventTraceEntry) {
        self.entries.push_back(entry);
        let overflow = self.entries.len().saturating_sub(self.capacity);
        if overflow > 0 {
            self.entries.drain(0..overflow);
        }
    }

    pub(super) const fn entries(&self) -> &VecDeque<EventTraceEntry> {
        &self.entries
    }
}

/// Final state entry in the trace dump, indicating why the loop terminated.
#[derive(Serialize)]
struct EventTraceFinalState<'a> {
    kind: &'static str,
    reason: &'a str,
    state: &'a PipelineState,
}

/// Build a trace entry from current state and effect/event details.
pub(super) fn build_trace_entry(
    iteration: usize,
    state: &PipelineState,
    effect: &str,
    event: &str,
) -> EventTraceEntry {
    EventTraceEntry {
        iteration,
        effect: effect.to_string(),
        event: event.to_string(),
        phase: format!("{:?}", state.phase),
        xsd_retry_pending: state.continuation.xsd_retry_pending,
        xsd_retry_count: state.continuation.xsd_retry_count,
        invalid_output_attempts: state.continuation.invalid_output_attempts,
        agent_index: state.agent_chain.current_agent_index,
        model_index: state.agent_chain.current_model_index,
        retry_cycle: state.agent_chain.retry_cycle,
    }
}

/// Dump the event loop trace to disk for post-mortem analysis.
///
/// Writes the trace as JSONL (one JSON object per line) to the event loop trace file
/// path from the run log context. The final line contains the terminal state and
/// termination reason.
///
/// Returns `true` if the trace was successfully written, `false` otherwise.
pub(super) fn dump_event_loop_trace(
    ctx: &PhaseContext<'_>,
    trace: &EventTraceBuffer,
    final_state: &PipelineState,
    reason: &str,
) -> bool {
    let trace_lines: Vec<String> = trace
        .entries()
        .iter()
        .filter_map(|entry| serde_json::to_string(entry).ok())
        .collect();

    let error_count = trace.entries().len().saturating_sub(trace_lines.len());
    if error_count > 0 {
        ctx.logger.error(&format!(
            "Failed to serialize {error_count} event loop trace entries"
        ));
    }

    let final_line = match serde_json::to_string(&EventTraceFinalState {
        kind: "final_state",
        reason,
        state: final_state,
    }) {
        Ok(line) => line,
        Err(err) => {
            ctx.logger.error(&format!(
                "Failed to serialize event loop final state: {err}"
            ));
            format!(
                "{{\"kind\":\"final_state\",\"reason\":{},\"phase\":{}}}",
                serde_json::to_string(reason).unwrap_or_else(|_| "\"unknown\"".to_string()),
                serde_json::to_string(&format!("{:?}", final_state.phase))
                    .unwrap_or_else(|_| "\"unknown\"".to_string())
            )
        }
    };

    let out = trace_lines
        .into_iter()
        .chain(std::iter::once(final_line))
        .map(|line| format!("{line}\n"))
        .collect::<String>();

    let trace_path = ctx.run_log_context.event_loop_trace();

    if let Some(parent) = trace_path.parent() {
        if let Err(err) = ctx.workspace.create_dir_all(parent) {
            ctx.logger
                .error(&format!("Failed to create trace directory: {err}"));
            return false;
        }
    }

    match ctx.workspace.write(&trace_path, &out) {
        Ok(()) => true,
        Err(err) => {
            ctx.logger
                .error(&format!("Failed to write event loop trace: {err}"));
            false
        }
    }
}
