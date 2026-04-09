// OpenCode parser stream processing: event parsing and stream handling methods.

impl OpenCodeParser {
    /// Parse and display a single `OpenCode` JSON event
    ///
    /// From `OpenCode` source (`run.ts` lines 146-201), the NDJSON format uses events with:
    /// - `step_start`: Step initialization with snapshot info
    /// - `step_finish`: Step completion with reason, cost, tokens
    /// - `tool_use`: Tool invocation with tool name, callID, and state (status, input, output)
    /// - `text`: Streaming text content
    /// - `error`: Session/API error events
    pub(crate) fn parse_event(&self, line: &str) -> Option<String> {
        let event = match parse_opencode_event_or_passthrough(line) {
            Ok(event) => event,
            Err(passthrough) => return passthrough,
        };
        let output = self.dispatch_event(&event, line);
        if output.is_empty() { None } else { Some(output) }
    }

    /// Update the shared tool-activity counter based on the OpenCode event before dispatching.
    ///
    /// `step_finish` → hard-reset counter to 0 (step is definitively over).
    /// `tool_use` with status "pending" → increment (new call starting).
    /// `tool_use` with status "running" → no-op (already counted; avoid double-increment).
    /// `tool_use` with status "completed"/"error" → saturating-decrement (call done).
    /// All other events → no change.
    fn apply_tool_activity_for_event(&self, event: &OpenCodeEvent) {
        match event.event_type.as_str() {
            "step_finish" => self.reset_tool_active(),
            "tool_use" => self.apply_tool_use_activity(event),
            _ => {}
        }
    }

    fn apply_tool_use_activity(&self, event: &OpenCodeEvent) {
        let status = event
            .part
            .as_ref()
            .and_then(|p| p.state.as_ref())
            .and_then(|s| s.status.as_deref())
            .unwrap_or("pending");
        match status {
            "pending" => self.set_tool_active(),   // new call starting — increment
            "running" => {}                         // status update, already counted — no-op
            "completed" | "error" => self.clear_tool_active(), // call done — decrement
            _ => {}
        }
    }

    fn dispatch_event(&self, event: &OpenCodeEvent, line: &str) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;
        self.apply_tool_activity_for_event(event);
        match event.event_type.as_str() {
            "step_start" => self.format_step_start_event(event),
            "step_finish" => self.format_step_finish_event(event),
            "tool_use" => self.format_tool_use_event(event),
            "text" => self.format_text_event(event),
            "error" => self.format_error_event(event, line),
            _ => format_unknown_json_event(line, prefix, *c, self.verbosity.is_verbose()),
        }
    }

    fn next_fallback_step_id(&self, session: &str, timestamp: Option<u64>) -> String {
        let counter = self.state.fallback_step_counter.get().saturating_add(1);
        self.state.fallback_step_counter.set(counter);
        timestamp.map_or_else(
            || format!("{session}:fallback:{counter}"),
            |ts| format!("{session}:{ts}:{counter}"),
        )
    }

    /// Check if an `OpenCode` event is a control event (state management with no user output)
    ///
    /// Control events are valid JSON that represent state transitions rather than
    /// user-facing content. They should be tracked separately from "ignored" events
    /// to avoid false health warnings.
    fn is_control_event(event: &OpenCodeEvent) -> bool {
        match event.event_type.as_str() {
            // Step lifecycle events are control events
            "step_start" | "step_finish" => true,
            _ => false,
        }
    }

    /// Check if an `OpenCode` event is a partial/delta event (streaming content displayed incrementally)
    ///
    /// Partial events represent streaming text deltas that are shown to the user
    /// in real-time. These should be tracked separately to avoid inflating "ignored" percentages.
    fn is_partial_event(event: &OpenCodeEvent) -> bool {
        match event.event_type.as_str() {
            // Text events produce streaming content
            "text" => true,
            _ => false,
        }
    }

    fn process_stream_json_line(
        &mut self,
        line: &str,
        monitor: &HealthMonitor,
        logging_enabled: bool,
        log_buffer: &mut Vec<u8>,
    ) -> std::io::Result<()> {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            return Ok(());
        }
        self.maybe_write_debug_event(line)?;
        self.parse_and_print_event(line, trimmed, monitor)?;
        if logging_enabled {
            writeln!(log_buffer, "{line}")?;
        }
        Ok(())
    }

    fn parse_and_print_event(
        &mut self,
        line: &str,
        trimmed: &str,
        monitor: &HealthMonitor,
    ) -> std::io::Result<()> {
        match self.parse_event(line) {
            Some(output) => {
                Self::record_monitor_event(
                    monitor,
                    Self::classify_successful_parse_for_monitor(line, trimmed),
                );
                self.with_printer_mut(|printer| {
                    write!(printer, "{output}")?;
                    printer.flush()
                })
            }
            None => {
                Self::record_monitor_event(
                    monitor,
                    Self::classify_empty_output_for_monitor(line, trimmed),
                );
                Ok(())
            }
        }
    }

    fn maybe_write_debug_event(&mut self, line: &str) -> std::io::Result<()> {
        if !self.verbosity.is_debug() {
            return Ok(());
        }

        let c = self.colors;
        self.with_printer_mut(|printer| {
            writeln!(
                printer,
                "{}[DEBUG]{} {}{}{}",
                c.dim(),
                c.reset(),
                c.dim(),
                line,
                c.reset()
            )?;
            printer.flush()
        })?;
        Ok(())
    }

    fn classify_successful_parse_for_monitor(
        line: &str,
        trimmed: &str,
    ) -> MonitorEventClassification {
        classify_successful_parse(line, trimmed)
    }

    fn classify_empty_output_for_monitor(line: &str, trimmed: &str) -> MonitorEventClassification {
        if !trimmed.starts_with('{') {
            return MonitorEventClassification::Ignored;
        }

        serde_json::from_str::<OpenCodeEvent>(line).map_or(
            MonitorEventClassification::ParseError,
            |event| {
                if Self::is_control_event(&event) {
                    MonitorEventClassification::Control
                } else {
                    MonitorEventClassification::Unknown
                }
            },
        )
    }

    fn record_monitor_event(monitor: &HealthMonitor, classification: MonitorEventClassification) {
        match classification {
            MonitorEventClassification::Parsed => monitor.record_parsed(),
            MonitorEventClassification::Partial => monitor.record_partial_event(),
            MonitorEventClassification::Control => monitor.record_control_event(),
            MonitorEventClassification::Unknown => monitor.record_unknown_event(),
            MonitorEventClassification::ParseError => monitor.record_parse_error(),
            MonitorEventClassification::Ignored => monitor.record_ignored(),
        }
    }

    fn process_incremental_stream<R: BufRead>(
        &mut self,
        reader: &mut R,
        mut parser: crate::json_parser::incremental_parser::IncrementalNdjsonParser,
        monitor: &HealthMonitor,
        logging_enabled: bool,
        log_buffer: &mut Vec<u8>,
    ) -> std::io::Result<crate::json_parser::incremental_parser::IncrementalNdjsonParser> {
        loop {
            let Some(data) = read_next_chunk(reader)? else {
                break;
            };
            let (new_parser, batch) = feed_chunk_data(parser, &data);
            parser = new_parser;
            batch.into_iter().try_for_each(|line| {
                self.process_stream_json_line(&line, monitor, logging_enabled, log_buffer)
            })?;
        }
        Ok(parser)
    }

    fn process_remaining_buffered_event(
        &mut self,
        remaining: &str,
        monitor: &HealthMonitor,
        logging_enabled: bool,
        log_buffer: &mut Vec<u8>,
    ) -> std::io::Result<()> {
        let trimmed = remaining.trim();
        if !is_valid_remaining_event(remaining, trimmed) {
            return Ok(());
        }
        self.parse_and_emit_remaining(remaining, trimmed, monitor)?;
        if logging_enabled {
            writeln!(log_buffer, "{remaining}")?;
        }
        Ok(())
    }

    fn parse_and_emit_remaining(
        &mut self,
        remaining: &str,
        trimmed: &str,
        monitor: &HealthMonitor,
    ) -> std::io::Result<()> {
        match self.parse_event(remaining) {
            Some(output) => {
                monitor.record_parsed();
                self.with_printer_mut(|printer| {
                    write!(printer, "{output}")?;
                    printer.flush()
                })
            }
            None => {
                Self::record_monitor_event(
                    monitor,
                    Self::classify_empty_output_for_monitor(remaining, trimmed),
                );
                Ok(())
            }
        }
    }

    fn write_log_buffer_if_enabled(
        &self,
        workspace: &dyn crate::workspace::Workspace,
        log_buffer: &[u8],
    ) -> std::io::Result<()> {
        if let Some(log_path) = &self.log_path {
            workspace.append_bytes(log_path, log_buffer)?;
        }
        Ok(())
    }

    fn with_xml_tail_bound(accumulated: &str, max_bytes: usize) -> &str {
        if accumulated.len() <= max_bytes {
            return accumulated;
        }

        let start = (accumulated.len() - max_bytes..accumulated.len())
            .find(|&i| accumulated.is_char_boundary(i))
            .unwrap_or(accumulated.len());
        &accumulated[start..]
    }

    fn persist_extracted_xml(
        workspace: &dyn crate::workspace::Workspace,
        output_path: &str,
        xml: &str,
    ) -> std::io::Result<()> {
        if xml.len() > MAX_XML_BYTES {
            return Ok(());
        }

        workspace.create_dir_all(Path::new(".agent/tmp"))?;
        workspace.write(Path::new(output_path), xml)?;
        Ok(())
    }

    fn persist_extracted_xml_artifacts(
        &self,
        workspace: &dyn crate::workspace::Workspace,
    ) -> std::io::Result<()> {
        let Some(accumulated) = self.get_accumulated_text() else {
            return Ok(());
        };
        let tail = Self::with_xml_tail_bound(&accumulated, MAX_XML_SEARCH_BYTES);
        self.persist_commit_xml_if_present(workspace, tail)?;
        self.persist_issues_xml_if_present(workspace, tail)
    }

    fn get_accumulated_text(&self) -> Option<String> {
        let session = self.state.streaming_session.borrow();
        session
            .get_accumulated(ContentType::Text, "main")
            .map(str::to_string)
    }

    fn persist_commit_xml_if_present(
        &self,
        workspace: &dyn crate::workspace::Workspace,
        tail: &str,
    ) -> std::io::Result<()> {
        if let Some(xml) =
            crate::files::llm_output_extraction::xml_extraction::extract_xml_commit(tail)
        {
            Self::persist_extracted_xml(
                workspace,
                crate::files::llm_output_extraction::file_based_extraction::paths::COMMIT_MESSAGE_XML,
                &xml,
            )?;
        }
        Ok(())
    }

    fn persist_issues_xml_if_present(
        &self,
        workspace: &dyn crate::workspace::Workspace,
        tail: &str,
    ) -> std::io::Result<()> {
        if let Some(xml) =
            crate::files::llm_output_extraction::extract_issues_xml(tail)
        {
            Self::persist_extracted_xml(
                workspace,
                crate::files::llm_output_extraction::file_based_extraction::paths::ISSUES_XML,
                &xml,
            )?;
        }
        Ok(())
    }

    fn write_monitor_warning_if_needed(&mut self, monitor: &HealthMonitor) -> std::io::Result<()> {
        if let Some(warning) = monitor.check_and_warn(self.colors) {
            self.with_printer_mut(|printer| {
                writeln!(printer, "{warning}").ok();
            });
        }
        Ok(())
    }

    /// Parse a stream of `OpenCode` NDJSON events
    pub(crate) fn parse_stream<R: BufRead>(
        &mut self,
        mut reader: R,
        workspace: &dyn crate::workspace::Workspace,
    ) -> std::io::Result<()> {
        use crate::json_parser::incremental_parser::IncrementalNdjsonParser;

        let monitor = HealthMonitor::new("OpenCode");
        let logging_enabled = self.log_path.is_some();
        let mut log_buffer: Vec<u8> = Vec::new();
        let incremental_parser = IncrementalNdjsonParser::new();

        let incremental_parser = self.process_incremental_stream(
            &mut reader,
            incremental_parser,
            &monitor,
            logging_enabled,
            &mut log_buffer,
        )?;

        if let Some(remaining) = incremental_parser.finish() {
            self.process_remaining_buffered_event(
                &remaining,
                &monitor,
                logging_enabled,
                &mut log_buffer,
            )?;
        }

        self.reset_tool_active(); // hard-reset at stream end — no more tool events can arrive

        self.write_log_buffer_if_enabled(workspace, &log_buffer)?;
        self.persist_extracted_xml_artifacts(workspace)?;
        self.write_monitor_warning_if_needed(&monitor)?;
        Ok(())
    }
}

/// Parse an `OpenCode` event line into an `OpenCodeEvent`, or return a passthrough string for
/// non-JSON lines that should be forwarded verbatim.
///
/// Returns `Ok(event)` if the line is a valid JSON event, or `Err(passthrough)` where
/// `passthrough` is the output to emit directly (including `None` for empty/silenced lines).
fn parse_opencode_event_or_passthrough(line: &str) -> Result<OpenCodeEvent, Option<String>> {
    match serde_json::from_str::<OpenCodeEvent>(line) {
        Ok(event) => Ok(event),
        Err(_) => {
            let trimmed = line.trim();
            if !trimmed.is_empty() && !trimmed.starts_with('{') {
                Err(Some(format!("{trimmed}\n")))
            } else {
                Err(None)
            }
        }
    }
}

fn classify_successful_parse(line: &str, trimmed: &str) -> MonitorEventClassification {
    if trimmed.starts_with('{') {
        if let Ok(event) = serde_json::from_str::<OpenCodeEvent>(line) {
            if OpenCodeParser::is_partial_event(&event) {
                return MonitorEventClassification::Partial;
            }
        }
    }
    MonitorEventClassification::Parsed
}

fn read_next_chunk<R: BufRead>(reader: &mut R) -> std::io::Result<Option<Vec<u8>>> {
    let chunk = reader.fill_buf()?;
    if chunk.is_empty() {
        return Ok(None);
    }
    let data = chunk.to_vec();
    reader.consume(data.len());
    Ok(Some(data))
}

fn feed_chunk_data(
    parser: crate::json_parser::incremental_parser::IncrementalNdjsonParser,
    data: &[u8],
) -> (crate::json_parser::incremental_parser::IncrementalNdjsonParser, Vec<String>) {
    parser.feed_and_get_events(data)
}

fn is_valid_remaining_event(remaining: &str, trimmed: &str) -> bool {
    !trimmed.is_empty()
        && trimmed.starts_with('{')
        && serde_json::from_str::<OpenCodeEvent>(remaining).is_ok()
}
