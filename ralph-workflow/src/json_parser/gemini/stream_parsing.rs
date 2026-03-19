impl GeminiParser {
    /// Check if a Gemini event is a control event (state management with no user output)
    ///
    /// Control events are valid JSON that represent state transitions rather than
    /// user-facing content. They should be tracked separately from "ignored" events
    /// to avoid false health warnings.
    const fn is_control_event(event: &GeminiEvent) -> bool {
        match event {
            // Init and Result events are control events
            GeminiEvent::Init { .. } | GeminiEvent::Result { .. } => true,
            _ => false,
        }
    }

    /// Parse a stream of Gemini NDJSON events
    pub(crate) fn parse_stream<R: BufRead>(
        &mut self,
        mut reader: R,
        workspace: &dyn crate::workspace::Workspace,
    ) -> std::io::Result<()> {
        use crate::json_parser::incremental_parser::IncrementalNdjsonParser;

        let c = self.colors.clone();
        let monitor = HealthMonitor::new("Gemini");
        // Accumulate log content in memory, write to workspace at the end
        let logging_enabled = self.log_path.is_some();
        let mut log_buffer: Vec<u8> = Vec::new();

        // Use incremental parser for true real-time streaming
        // This processes JSON as soon as it's complete, not waiting for newlines
        let mut incremental_parser = IncrementalNdjsonParser::new();

        loop {
            // Read available bytes
            let chunk = reader.fill_buf()?;
            if chunk.is_empty() {
                break;
            }

            // Process all bytes immediately
            let consumed = chunk.len();

            let (new_parser, batch) = incremental_parser.feed_and_get_events(chunk);
            incremental_parser = new_parser;

            reader.consume(consumed);

            batch.into_iter().for_each(|line| {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    return;
                }

                // In debug mode, also show the raw JSON
                if self.verbosity.is_debug() {
                    self.with_printer_mut(|printer| {
                        if writeln!(
                            printer,
                            "{}[DEBUG]{} {}{}{}",
                            c.dim(),
                            c.reset(),
                            c.dim(),
                            &line,
                            c.reset()
                        )
                        .is_ok()
                        {
                            printer.flush().ok();
                        }
                    });
                }

                // Parse the event once - parse_event handles malformed JSON by returning None
                match self.parse_event(&line) {
                    Some(output) => {
                        monitor.record_parsed();
                        // Write output to printer
                        self.with_printer_mut(|printer| {
                            if write!(printer, "{output}").is_ok() {
                                printer.flush().ok();
                            }
                        });
                    }
                    None => {
                        // Check if this was a control event (state management with no user output)
                        if trimmed.starts_with('{') {
                            if let Ok(event) = serde_json::from_str::<GeminiEvent>(&line) {
                                if Self::is_control_event(&event) {
                                    monitor.record_control_event();
                                } else {
                                    // Valid JSON but not a control event - track as unknown
                                    monitor.record_unknown_event();
                                }
                            } else {
                                // Failed to deserialize - track as parse error
                                monitor.record_parse_error();
                            }
                        } else {
                            monitor.record_ignored();
                        }
                    }
                }

                // Log raw JSON to buffer if configured
                if logging_enabled {
                    writeln!(log_buffer, "{line}").ok();
                }
            });
        }

        // Write accumulated log content to workspace
        if let Some(log_path) = &self.log_path {
            workspace.append_bytes(log_path, &log_buffer)?;
        }
        if let Some(warning) = monitor.check_and_warn(c) {
            self.with_printer_mut(|printer| {
                writeln!(printer, "{warning}\n").ok();
            });
        }
        Ok(())
    }
}
