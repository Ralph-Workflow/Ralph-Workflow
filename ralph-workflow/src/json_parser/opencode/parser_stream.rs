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
        let event: OpenCodeEvent = if let Ok(e) = serde_json::from_str(line) {
            e
        } else {
            let trimmed = line.trim();
            if !trimmed.is_empty() && !trimmed.starts_with('{') {
                return Some(format!("{trimmed}\n"));
            }
            return None;
        };
        let c = &self.colors;
        let prefix = &self.display_name;

        let output = match event.event_type.as_str() {
            "step_start" => self.format_step_start_event(&event),
            "step_finish" => self.format_step_finish_event(&event),
            "tool_use" => self.format_tool_use_event(&event),
            "text" => self.format_text_event(&event),
            "error" => self.format_error_event(&event, line),
            _ => {
                // Unknown event type - use the generic formatter in verbose mode
                format_unknown_json_event(line, prefix, *c, self.verbosity.is_verbose())
            }
        };

        if output.is_empty() {
            None
        } else {
            Some(output)
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

        match self.parse_event(line) {
            Some(output) => {
                Self::record_monitor_event(
                    monitor,
                    Self::classify_successful_parse_for_monitor(line, trimmed),
                );
                self.with_printer_mut(|printer| {
                    write!(printer, "{output}")?;
                    printer.flush()
                })?;
            }
            None => {
                Self::record_monitor_event(
                    monitor,
                    Self::classify_empty_output_for_monitor(line, trimmed),
                );
            }
        }

        if logging_enabled {
            writeln!(log_buffer, "{line}")?;
        }
        Ok(())
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
        if trimmed.starts_with('{') {
            if let Ok(event) = serde_json::from_str::<OpenCodeEvent>(line) {
                if Self::is_partial_event(&event) {
                    return MonitorEventClassification::Partial;
                }
            }
        }
        MonitorEventClassification::Parsed
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
            let chunk = reader.fill_buf()?;
            if chunk.is_empty() {
                break;
            }

            let consumed = chunk.len();
            let data: Vec<u8> = chunk.to_vec();
            reader.consume(consumed);

            let (new_parser, batch) = parser.feed_and_get_events(&data);
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
        if trimmed.is_empty()
            || !trimmed.starts_with('{')
            || serde_json::from_str::<OpenCodeEvent>(remaining).is_err()
        {
            return Ok(());
        }

        match self.parse_event(remaining) {
            Some(output) => {
                monitor.record_parsed();
                self.with_printer_mut(|printer| {
                    write!(printer, "{output}")?;
                    printer.flush()
                })?;
            }
            None => {
                Self::record_monitor_event(
                    monitor,
                    Self::classify_empty_output_for_monitor(remaining, trimmed),
                );
            }
        }

        if logging_enabled {
            writeln!(log_buffer, "{remaining}")?;
        }
        Ok(())
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
        let accumulated: Option<String> = {
            let session = self.state.streaming_session.borrow();
            session
                .get_accumulated(ContentType::Text, "main")
                .map(str::to_string)
        };

        let Some(accumulated) = accumulated else {
            return Ok(());
        };

        let accumulated_tail = Self::with_xml_tail_bound(&accumulated, MAX_XML_SEARCH_BYTES);

        if let Some(xml) = crate::files::llm_output_extraction::xml_extraction::extract_xml_commit(
            accumulated_tail,
        ) {
            Self::persist_extracted_xml(
                workspace,
                crate::files::llm_output_extraction::file_based_extraction::paths::COMMIT_MESSAGE_XML,
                &xml,
            )?;
        }

        if let Some(xml) = crate::files::llm_output_extraction::extract_issues_xml(accumulated_tail)
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

        self.write_log_buffer_if_enabled(workspace, &log_buffer)?;
        self.persist_extracted_xml_artifacts(workspace)?;
        self.write_monitor_warning_if_needed(&monitor)?;
        Ok(())
    }
}
