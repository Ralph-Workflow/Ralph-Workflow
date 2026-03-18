impl CodexParser {
    /// Check if a Codex event is a control event (state management with no user output)
    ///
    /// Control events are valid JSON that represent state transitions rather than
    /// user-facing content. They should be tracked separately from "ignored" events
    /// to avoid false health warnings.
    fn is_control_event(event: &CodexEvent) -> bool {
        match event {
            // Turn lifecycle events are control events
            CodexEvent::ThreadStarted { .. }
            | CodexEvent::TurnStarted { .. }
            | CodexEvent::TurnCompleted { .. }
            | CodexEvent::TurnFailed { .. }
            | CodexEvent::Result { .. } => true,
            // Item started/completed events are control events for certain item types
            CodexEvent::ItemStarted { item } => {
                item.as_ref().and_then(|i| i.item_type.as_deref()) == Some("plan_update")
            }
            CodexEvent::ItemCompleted { item } => {
                item.as_ref().and_then(|i| i.item_type.as_deref()) == Some("plan_update")
            }
            _ => false,
        }
    }

    /// Check if a Codex event is a partial/delta event (streaming content displayed incrementally)
    ///
    /// Partial events represent streaming content deltas (agent messages, reasoning)
    /// that are shown to the user in real-time. These should be tracked separately
    /// to avoid inflating "ignored" percentages.
    fn is_partial_event(event: &CodexEvent) -> bool {
        match event {
            // Item started events for agent_message and reasoning produce streaming content
            CodexEvent::ItemStarted { item: Some(item) } => matches!(
                item.item_type.as_deref(),
                Some("agent_message" | "reasoning")
            ),
            _ => false,
        }
    }

    /// Write a synthetic result event to the log file with accumulated content.
    ///
    /// This is called when a `TurnCompleted` event is encountered to ensure
    /// that the extraction process can find the aggregated content.
    ///
    /// # Persistence Guarantees
    ///
    /// This function flushes the writer after writing. Errors are propagated
    /// to ensure the result event is actually persisted before continuing.
    fn write_synthetic_result_event(
        file: &mut impl std::io::Write,
        accumulated: &str,
    ) -> io::Result<()> {
        let result_event = CodexEvent::Result {
            result: Some(accumulated.to_string()),
        };
        let json = serde_json::to_string(&result_event)?;
        writeln!(file, "{json}")?;
        file.flush()?;
        Ok(())
    }

    /// Write a synthetic result event to a byte buffer.
    fn write_synthetic_result_to_buffer(buffer: &mut Vec<u8>, accumulated: &str) -> io::Result<()> {
        Self::write_synthetic_result_event(buffer, accumulated)
    }

    /// Process a single JSON event line during parsing.
    fn process_event_line_with_buffer(
        &self,
        line: &str,
        monitor: &HealthMonitor,
        logging_enabled: bool,
        log_buffer: &mut Vec<u8>,
    ) -> io::Result<bool> {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            return Ok(false);
        }

        if self.verbosity.is_debug() {
            let mut printer = self.printer.borrow_mut();
            writeln!(
                printer,
                "{}[DEBUG]{} {}{}{}",
                self.colors.dim(),
                self.colors.reset(),
                self.colors.dim(),
                line,
                self.colors.reset()
            )?;
            printer.flush()?;
        }

        // Parse the event once for both display/logic and synthetic result writing
        let parsed_event = if trimmed.starts_with('{') {
            serde_json::from_str::<CodexEvent>(trimmed).ok()
        } else {
            None
        };

        // Check if this is a turn.completed event using the parsed event
        let is_turn_completed = parsed_event
            .as_ref()
            .is_some_and(|e| matches!(e, CodexEvent::TurnCompleted { .. }));

        match self.parse_event(line) {
            Some(output) => {
                if let Some(event) = &parsed_event {
                    if Self::is_partial_event(event) {
                        monitor.record_partial_event();
                    } else {
                        monitor.record_parsed();
                    }
                } else {
                    monitor.record_parsed();
                }
                let mut printer = self.printer.borrow_mut();
                write!(printer, "{output}")?;
                printer.flush()?;
            }
            None => {
                if let Some(event) = &parsed_event {
                    if Self::is_control_event(event) {
                        monitor.record_control_event();
                    } else {
                        monitor.record_unknown_event();
                    }
                } else {
                    monitor.record_ignored();
                }
            }
        }

        if logging_enabled {
            writeln!(log_buffer, "{line}")?;
            // Write synthetic result event on turn.completed to ensure content is captured
            // This handles the normal case where the stream completes properly
            if is_turn_completed {
                if let Some(accumulated) = self
                    .streaming_session
                    .borrow()
                    .get_accumulated(crate::json_parser::types::ContentType::Text, "agent_msg")
                {
                    Self::write_synthetic_result_to_buffer(log_buffer, accumulated)?;
                }
            }
        }

        Ok(true)
    }

    /// Parse a stream of Codex NDJSON events
    pub(crate) fn parse_stream<R: BufRead>(
        &self,
        mut reader: R,
        workspace: &dyn Workspace,
    ) -> io::Result<()> {
        use crate::json_parser::io::incremental_parser::IncrementalNdjsonParser;

        let monitor = HealthMonitor::new("Codex");
        // Accumulate log content in memory, write to workspace at the end
        let logging_enabled = self.log_path.is_some();
        let mut log_buffer: Vec<u8> = Vec::new();

        let mut incremental_parser = IncrementalNdjsonParser::new();
        let mut byte_buffer = Vec::new();
        // Track whether we've written a synthetic result event for the current turn
        let mut result_written_for_current_turn = false;

        loop {
            byte_buffer.clear();
            let chunk = reader.fill_buf()?;
            if chunk.is_empty() {
                break;
            }
            let consumed = chunk.len();
            byte_buffer.extend_from_slice(chunk);
            reader.consume(consumed);

            for line in incremental_parser.feed(&byte_buffer) {
                // Check if this is a turn.completed or turn.started event before processing
                let is_turn_completed = line.trim().starts_with('{')
                    && serde_json::from_str::<CodexEvent>(line.trim())
                        .ok()
                        .is_some_and(|e| matches!(e, CodexEvent::TurnCompleted { .. }));
                let is_turn_started = line.trim().starts_with('{')
                    && serde_json::from_str::<CodexEvent>(line.trim())
                        .ok()
                        .is_some_and(|e| matches!(e, CodexEvent::TurnStarted { .. }));

                self.process_event_line_with_buffer(
                    &line,
                    &monitor,
                    logging_enabled,
                    &mut log_buffer,
                )?;

                // Track result event writes - reset flag when new turn starts
                if is_turn_started {
                    result_written_for_current_turn = false;
                } else if is_turn_completed {
                    result_written_for_current_turn = true;
                }
            }
        }

        // Handle any remaining buffered data when the stream ends.
        // Only process if it's valid JSON - incomplete buffered data should be skipped.
        if let Some(remaining) = incremental_parser.finish() {
            // Only process if it's valid JSON to avoid processing incomplete buffered data
            if remaining.starts_with('{') && serde_json::from_str::<CodexEvent>(&remaining).is_ok()
            {
                self.process_event_line_with_buffer(
                    &remaining,
                    &monitor,
                    logging_enabled,
                    &mut log_buffer,
                )?;
            }
        }

        // Ensure accumulated content is written even if turn.completed was not received
        // This handles the case where the stream ends unexpectedly
        if logging_enabled && !result_written_for_current_turn {
            if let Some(accumulated) = self
                .streaming_session
                .borrow()
                .get_accumulated(crate::json_parser::types::ContentType::Text, "agent_msg")
            {
                // Write the synthetic result event for any accumulated content
                Self::write_synthetic_result_to_buffer(&mut log_buffer, accumulated)?;
            }
        }

        // Write accumulated log content to workspace
        if let Some(log_path) = &self.log_path {
            workspace.append_bytes(log_path, &log_buffer)?;
        }

        if let Some(warning) = monitor.check_and_warn(self.colors) {
            let mut printer = self.printer.borrow_mut();
            writeln!(printer, "{warning}")?;
        }
        Ok(())
    }
}
