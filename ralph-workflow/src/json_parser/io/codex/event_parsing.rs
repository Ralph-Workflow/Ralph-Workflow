impl CodexParser {
    /// Parse and display a single Codex JSON event
    ///
    /// Returns `Some(formatted_output)` for valid events, or None for:
    /// - Malformed JSON (non-JSON text passed through if meaningful)
    /// - Unknown event types
    /// - Empty or whitespace-only output
    pub(crate) fn parse_event(&self, line: &str) -> Option<String> {
        let event: CodexEvent = if let Ok(e) = serde_json::from_str(line) {
            e
        } else {
            // Non-JSON line - pass through as-is if meaningful
            let trimmed = line.trim();
            if !trimmed.is_empty() && !trimmed.starts_with('{') {
                return Some(format!("{trimmed}\n"));
            }
            return None;
        };

        let ctx = EventHandlerContext {
            colors: &self.colors,
            verbosity: self.verbosity,
            display_name: &self.display_name,
            streaming_session: &self.streaming_session,
            reasoning_accumulator: &self.reasoning_accumulator,
            terminal_mode: *self.terminal_mode.borrow(),
            show_streaming_metrics: self.show_streaming_metrics,
            last_rendered_content: &self.last_rendered_content,
        };

        match event {
            CodexEvent::ThreadStarted { thread_id } => {
                Self::optional_output(handle_thread_started(&ctx, thread_id))
            }
            CodexEvent::TurnStarted {} => {
                // Generate and set synthetic turn ID for duplicate detection
                let turn_id = {
                    let mut counter = self.turn_counter.borrow_mut();
                    let id = format!("turn-{}", *counter);
                    *counter = counter.saturating_add(1);
                    id
                };
                Self::optional_output(handle_turn_started(&ctx, turn_id))
            }
            CodexEvent::TurnCompleted { usage } => {
                Self::optional_output(handle_turn_completed(&ctx, usage))
            }
            CodexEvent::TurnFailed { error } => {
                Self::optional_output(handle_turn_failed(&ctx, error))
            }
            CodexEvent::ItemStarted { item } => handle_item_started(&ctx, item.as_ref()),
            CodexEvent::ItemCompleted { item } => handle_item_completed(&ctx, item.as_ref()),
            CodexEvent::Error { message, error } => {
                Self::optional_output(handle_error(&ctx, message, error))
            }
            CodexEvent::Result { result } => self.format_result_event(result),
            CodexEvent::Unknown => {
                let output = format_unknown_json_event(
                    line,
                    &self.display_name,
                    self.colors,
                    self.verbosity.is_verbose(),
                );
                Self::optional_output(output)
            }
        }
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
