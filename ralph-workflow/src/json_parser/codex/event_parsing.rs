impl CodexParser {
    fn parse_non_json_line(line: &str) -> Option<String> {
        let trimmed = line.trim();
        if !trimmed.is_empty() && !trimmed.starts_with('{') { Some(format!("{trimmed}\n")) }
        else { None }
    }

    fn make_event_ctx(&self) -> EventHandlerContext<'_> {
        EventHandlerContext {
            colors: &self.colors,
            verbosity: self.verbosity,
            display_name: &self.display_name,
            streaming_session: &self.state.streaming_session,
            reasoning_accumulator: &self.state.reasoning_accumulator,
            terminal_mode: *self.state.terminal_mode.borrow(),
            show_streaming_metrics: self.show_streaming_metrics,
            last_rendered_content: &self.state.last_rendered_content,
        }
    }

    /// Update the shared tool-activity counter based on the event variant before dispatching.
    ///
    /// `ItemStarted` → increment counter (tool or item began executing).
    /// `ItemCompleted` → saturating-decrement counter (one tool/item finished).
    /// `TurnCompleted`, `TurnFailed` → hard-reset counter to 0 (turn is definitively over;
    ///   handles protocol anomalies where `ItemCompleted` was never received).
    /// All other events → no change.
    fn apply_tool_activity_for_event(&self, event: &CodexEvent) {
        match event {
            CodexEvent::ItemStarted { .. } => self.set_tool_active(),
            CodexEvent::ItemCompleted { .. } => self.clear_tool_active(),
            CodexEvent::TurnCompleted { .. } | CodexEvent::TurnFailed { .. } => {
                self.reset_tool_active();
            }
            _ => {}
        }
    }

    fn dispatch_event(&self, event: CodexEvent, line: &str, ctx: &EventHandlerContext<'_>) -> Option<String> {
        self.apply_tool_activity_for_event(&event);
        match event {
            CodexEvent::ThreadStarted { thread_id } => Self::optional_output(handle_thread_started(ctx, thread_id)),
            CodexEvent::TurnStarted {} => {
                let turn_id = self.state.with_turn_counter_mut(|counter| { let id = format!("turn-{}", *counter); *counter = counter.saturating_add(1); id });
                Self::optional_output(handle_turn_started(ctx, turn_id))
            }
            CodexEvent::TurnCompleted { usage } => Self::optional_output(handle_turn_completed(ctx, usage)),
            CodexEvent::TurnFailed { error } => Self::optional_output(handle_turn_failed(ctx, error)),
            CodexEvent::ItemStarted { item } => handle_item_started(ctx, item.as_ref()),
            CodexEvent::ItemCompleted { item } => handle_item_completed(ctx, item.as_ref()),
            CodexEvent::Error { message, error } => Self::optional_output(handle_error(ctx, message, error)),
            CodexEvent::Result { result } => self.format_result_event(result),
            CodexEvent::Unknown => Self::optional_output(format_unknown_json_event(line, &self.display_name, self.colors, self.verbosity.is_verbose())),
        }
    }

    /// Parse and display a single Codex JSON event
    ///
    /// Returns `Some(formatted_output)` for valid events, or None for:
    /// - Malformed JSON (non-JSON text passed through if meaningful)
    /// - Unknown event types
    /// - Empty or whitespace-only output
    pub(crate) fn parse_event(&self, line: &str) -> Option<String> {
        let Ok(event) = serde_json::from_str::<CodexEvent>(line) else {
            return Self::parse_non_json_line(line);
        };
        let ctx = self.make_event_ctx();
        self.dispatch_event(event, line, &ctx)
    }

    /// Format a Result event for display.
    ///
    /// Result events are synthetic control events that are written to the log file
    /// by `process_event_line`. In debug mode, this method also formats them for
    /// console output to help with troubleshooting.
    fn format_result_event(&self, result: Option<String>) -> Option<String> {
        if !self.verbosity.is_debug() {
            return None;
        }
        result.map(|content| {
            let limit = self.verbosity.truncate_limit("result");
            let preview = crate::common::truncate_text(&content, limit);
            format!(
                "{}[{}]{} {}Result:{} {}{}{}\n",
                self.colors.dim(),
                self.display_name,
                self.colors.reset(),
                self.colors.green(),
                self.colors.reset(),
                self.colors.dim(),
                preview,
                self.colors.reset()
            )
        })
    }
}
