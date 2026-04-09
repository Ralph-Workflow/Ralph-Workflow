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
    ) -> std::io::Result<()> {
        let result_event = CodexEvent::Result {
            result: Some(accumulated.to_string()),
        };
        let json = serde_json::to_string(&result_event)?;
        writeln!(file, "{json}")?;
        file.flush()?;
        Ok(())
    }

    /// Write a synthetic result event to a byte buffer.
    fn write_synthetic_result_to_buffer(
        buffer: &mut Vec<u8>,
        accumulated: &str,
    ) -> std::io::Result<()> {
        Self::write_synthetic_result_event(buffer, accumulated)
    }

    fn print_debug_line(&mut self, line: &str) -> std::io::Result<()> {
        let colors = self.colors;
        self.with_printer_mut(|printer| {
            writeln!(printer, "{}[DEBUG]{} {}{}{}", colors.dim(), colors.reset(), colors.dim(), line, colors.reset())?;
            printer.flush()
        })
    }

    fn log_event_line(
        &self,
        line: &str,
        is_turn_completed: bool,
        log_buffer: &mut Vec<u8>,
    ) -> std::io::Result<()> {
        writeln!(log_buffer, "{line}")?;
        if is_turn_completed {
            if let Some(acc) = self.state.streaming_session.borrow()
                .get_accumulated(super::types::ContentType::Text, "agent_msg")
            {
                Self::write_synthetic_result_to_buffer(log_buffer, acc)?;
            }
        }
        Ok(())
    }

    fn dispatch_parsed_event(&mut self, line: &str, parsed_event: &Option<CodexEvent>, monitor: &HealthMonitor) -> std::io::Result<()> {
        let output = self.parse_event(line);
        record_event_monitor_outcome(parsed_event, output.as_deref(), monitor);
        print_event_output_if_present(self, output)
    }

    /// Process a single JSON event line during parsing.
    fn process_event_line_with_buffer(&mut self, line: &str, monitor: &HealthMonitor, logging_enabled: bool, log_buffer: &mut Vec<u8>) -> std::io::Result<bool> {
        let trimmed = line.trim();
        if trimmed.is_empty() { return Ok(false); }
        if self.verbosity.is_debug() { self.print_debug_line(line)?; }
        let parsed_event = parse_codex_event_if_json(trimmed);
        let is_turn_completed = parsed_event.as_ref().is_some_and(|e| matches!(e, CodexEvent::TurnCompleted { .. }));
        self.dispatch_parsed_event(line, &parsed_event, monitor)?;
        if logging_enabled { self.log_event_line(line, is_turn_completed, log_buffer)?; }
        Ok(true)
    }

    /// Parse a stream of Codex NDJSON events
    pub(crate) fn parse_stream<R: BufRead>(
        &mut self,
        mut reader: R,
        workspace: &dyn Workspace,
    ) -> std::io::Result<()> {
        use crate::json_parser::incremental_parser::IncrementalNdjsonParser;
        let monitor = HealthMonitor::new("Codex");
        let logging_enabled = self.log_path.is_some();
        let mut log_buffer: Vec<u8> = Vec::new();
        let mut incremental_parser = IncrementalNdjsonParser::new();
        let result_written = std::cell::Cell::new(false);
        self.run_codex_stream_loop(
            &mut reader,
            &mut incremental_parser,
            &monitor,
            &mut log_buffer,
            logging_enabled,
            &result_written,
        )?;
        self.finalize_codex_stream(
            workspace, &monitor, &mut log_buffer, logging_enabled, incremental_parser, &result_written,
        )
    }

    fn run_codex_stream_loop<R: BufRead>(
        &mut self, reader: &mut R,
        incremental_parser: &mut crate::json_parser::incremental_parser::IncrementalNdjsonParser,
        monitor: &HealthMonitor, log_buffer: &mut Vec<u8>,
        logging_enabled: bool, result_written: &std::cell::Cell<bool>,
    ) -> std::io::Result<()> {
        loop {
            let chunk = reader.fill_buf()?;
            if chunk.is_empty() { break; }
            let data = chunk.to_vec(); reader.consume(data.len());
            let (new_parser, batch) = std::mem::take(incremental_parser).feed_and_get_events(&data);
            *incremental_parser = new_parser;
            batch.into_iter().try_for_each(|line| { self.process_stream_line_with_turn_tracking(&line, monitor, logging_enabled, log_buffer, result_written) })?;
        }
        Ok(())
    }

    fn process_stream_line_with_turn_tracking(
        &mut self,
        line: &str,
        monitor: &HealthMonitor,
        logging_enabled: bool,
        log_buffer: &mut Vec<u8>,
        result_written: &std::cell::Cell<bool>,
    ) -> std::io::Result<()> {
        let trimmed = line.trim();
        let is_turn_completed = is_codex_turn_event::<true>(trimmed);
        let is_turn_started = is_codex_turn_event::<false>(trimmed);
        self.process_event_line_with_buffer(line, monitor, logging_enabled, log_buffer)?;
        update_turn_tracking(is_turn_started, is_turn_completed, result_written);
        Ok(())
    }

    fn process_remaining_input(&mut self, remaining: &str, monitor: &HealthMonitor, logging_enabled: bool, log_buffer: &mut Vec<u8>) -> std::io::Result<()> {
        if remaining.starts_with('{') && serde_json::from_str::<CodexEvent>(remaining).is_ok() {
            self.process_event_line_with_buffer(remaining, monitor, logging_enabled, log_buffer)?;
        }
        Ok(())
    }

    fn flush_unwritten_result(&self, log_buffer: &mut Vec<u8>, result_written: &std::cell::Cell<bool>) -> std::io::Result<()> {
        if result_written.get() { return Ok(()); }
        if let Some(acc) = self.state.streaming_session.borrow().get_accumulated(super::types::ContentType::Text, "agent_msg") {
            Self::write_synthetic_result_to_buffer(log_buffer, acc)?;
        }
        Ok(())
    }

    fn print_monitor_warning(&mut self, monitor: &HealthMonitor) {
        if let Some(warning) = monitor.check_and_warn(self.colors) {
            self.with_printer_mut(|printer| { writeln!(printer, "{warning}").ok(); });
        }
    }

    fn finalize_codex_stream(
        &mut self, workspace: &dyn Workspace, monitor: &HealthMonitor,
        log_buffer: &mut Vec<u8>, logging_enabled: bool,
        incremental_parser: crate::json_parser::incremental_parser::IncrementalNdjsonParser,
        result_written: &std::cell::Cell<bool>,
    ) -> std::io::Result<()> {
        self.reset_tool_active(); // hard-reset at stream end — stdout is closed, no more tool events
        if let Some(remaining) = incremental_parser.finish() { self.process_remaining_input(&remaining, monitor, logging_enabled, log_buffer)?; }
        if logging_enabled { self.flush_unwritten_result(log_buffer, result_written)?; }
        if let Some(log_path) = &self.log_path { workspace.append_bytes(log_path, log_buffer)?; }
        self.print_monitor_warning(monitor);
        Ok(())
    }
}

fn update_turn_tracking(
    is_turn_started: bool,
    is_turn_completed: bool,
    result_written: &std::cell::Cell<bool>,
) {
    if is_turn_started {
        result_written.set(false);
    } else if is_turn_completed {
        result_written.set(true);
    }
}

fn is_codex_turn_event<const IS_COMPLETED: bool>(trimmed: &str) -> bool {
    if !trimmed.starts_with('{') { return false; }
    serde_json::from_str::<CodexEvent>(trimmed)
        .ok()
        .is_some_and(|e| if IS_COMPLETED {
            matches!(e, CodexEvent::TurnCompleted { .. })
        } else {
            matches!(e, CodexEvent::TurnStarted { .. })
        })
}

fn parse_codex_event_if_json(trimmed: &str) -> Option<CodexEvent> {
    if trimmed.starts_with('{') {
        serde_json::from_str::<CodexEvent>(trimmed).ok()
    } else {
        None
    }
}

fn record_event_monitor_outcome(parsed_event: &Option<CodexEvent>, output: Option<&str>, monitor: &HealthMonitor) {
    if output.is_some() {
        record_codex_monitor_parsed(parsed_event, monitor);
    } else {
        record_codex_monitor_no_output(parsed_event, monitor);
    }
}

fn print_event_output_if_present(parser: &mut CodexParser, output: Option<String>) -> std::io::Result<()> {
    output.map_or(Ok(()), |text| {
        parser.with_printer_mut(|printer| { write!(printer, "{text}")?; printer.flush() })
    })
}

fn record_codex_monitor_parsed(parsed_event: &Option<CodexEvent>, monitor: &HealthMonitor) {
    match parsed_event {
        Some(event) if CodexParser::is_partial_event(event) => monitor.record_partial_event(),
        Some(_) => monitor.record_parsed(),
        None => monitor.record_parsed(),
    }
}

fn record_codex_monitor_no_output(parsed_event: &Option<CodexEvent>, monitor: &HealthMonitor) {
    match parsed_event {
        Some(event) if CodexParser::is_control_event(event) => monitor.record_control_event(),
        Some(_) => monitor.record_unknown_event(),
        None => monitor.record_ignored(),
    }
}
