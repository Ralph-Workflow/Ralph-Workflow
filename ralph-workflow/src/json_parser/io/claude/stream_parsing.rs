// Claude stream parsing methods.
//
// Contains parse_stream and event classification methods.

impl ClaudeParser {
    /// Check if a Claude event is a control event (state management with no user output)
    ///
    /// Control events are valid JSON that represent state transitions rather than
    /// user-facing content. They should be tracked separately from "ignored" events
    /// to avoid false health warnings.
    const fn is_control_event(event: &ClaudeEvent) -> bool {
        match event {
            // Stream events that are control events
            ClaudeEvent::StreamEvent { event } => matches!(
                event,
                StreamInnerEvent::MessageStart { .. }
                    | StreamInnerEvent::ContentBlockStart { .. }
                    | StreamInnerEvent::ContentBlockStop { .. }
                    | StreamInnerEvent::MessageDelta { .. }
                    | StreamInnerEvent::MessageStop
                    | StreamInnerEvent::Ping
            ),
            _ => false,
        }
    }

    /// Check if a Claude event is a partial/delta event (streaming content displayed incrementally)
    ///
    /// Partial events represent streaming content deltas (text deltas, thinking deltas,
    /// tool input deltas) that are shown to the user in real-time. These should be
    /// tracked separately to avoid inflating "ignored" percentages.
    const fn is_partial_event(event: &ClaudeEvent) -> bool {
        match event {
            // Stream events that produce incremental content
            ClaudeEvent::StreamEvent { event } => matches!(
                event,
                StreamInnerEvent::ContentBlockDelta { .. } | StreamInnerEvent::TextDelta { .. }
            ),
            _ => false,
        }
    }

    /// Parse a stream of Claude NDJSON events
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    #[expect(
        clippy::print_stderr,
        reason = "debug-only output for verbose debugging"
    )]
    pub fn parse_stream<R: BufRead>(
        &self,
        mut reader: R,
        workspace: &dyn crate::workspace::Workspace,
    ) -> io::Result<()> {
        use crate::json_parser::io::incremental_parser::IncrementalNdjsonParser;

        let c = &self.colors;
        let monitor = HealthMonitor::new("Claude");
        // Accumulate log content in memory, write to workspace at the end
        let logging_enabled = self.log_path.is_some();
        let mut log_buffer: Vec<u8> = Vec::new();

        // Use incremental parser for true real-time streaming
        // This processes JSON as soon as it's complete, not waiting for newlines
        let mut incremental_parser = IncrementalNdjsonParser::new();
        let mut byte_buffer = Vec::new();

        // Track whether we've seen a success result event for GLM/ccs-glm compatibility
        // Some agents (GLM via CCS) emit both a success result and an error_during_execution
        // result when they exit with code 1 despite producing valid output. We suppress
        // the spurious error event to avoid confusing duplicate output.
        let mut seen_success_result = false;

        loop {
            // Read available bytes
            byte_buffer.clear();
            let chunk = reader.fill_buf()?;
            if chunk.is_empty() {
                break;
            }

            // Process all bytes immediately
            byte_buffer.extend_from_slice(chunk);
            let consumed = chunk.len();
            reader.consume(consumed);

            // Feed bytes to incremental parser
            let json_events = incremental_parser.feed(&byte_buffer);

            // Process each complete JSON event immediately
            for line in json_events {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }

                // Check for Result events to handle GLM/ccs-glm duplicate event bug
                // Some agents emit both success and error_during_execution results
                let should_skip_result = if trimmed.starts_with('{') {
                    // First, check if the JSON has an 'errors' field with actual error messages.
                    // This is important because Claude events can have either 'error' (string)
                    // or 'errors' (array of strings), and we need to check both.
                    let has_errors_with_content =
                        serde_json::from_str::<serde_json::Value>(trimmed).is_ok_and(|json| {
                            // Check for 'errors' array with at least one non-empty string
                            json.get("errors")
                                .and_then(|v| v.as_array())
                                .is_some_and(|arr| {
                                    arr.iter()
                                        .any(|e| e.as_str().is_some_and(|s| !s.trim().is_empty()))
                                })
                        });

                    if let Ok(ClaudeEvent::Result {
                        subtype,
                        duration_ms,
                        error,
                        ..
                    }) = serde_json::from_str::<ClaudeEvent>(trimmed)
                    {
                        let is_error_result = subtype.as_deref() != Some("success");

                        // Suppress spurious GLM error events based on these characteristics:
                        // 1. Error event (subtype != "success")
                        // 2. duration_ms is 0 or very small (< 100ms, indicating synthetic event)
                        // 3. error field is null or empty (no actual error message)
                        // 4. NO 'errors' field with actual error messages (this indicates a real error)
                        //
                        // These criteria identify the spurious error_during_execution events
                        // that GLM emits when exiting with code 1 despite producing valid output.
                        //
                        // We DON'T suppress if there's an 'errors' array with content, because
                        // that indicates a real error condition that the user should see.
                        let is_spurious_glm_error = is_error_result
                            && duration_ms.unwrap_or(0) < 100
                            && (error.is_none()
                                || error.as_ref().is_some_and(std::string::String::is_empty))
                            && !has_errors_with_content;

                        if is_spurious_glm_error && seen_success_result {
                            // Error after success - suppress (original fix)
                            true
                        } else if subtype.as_deref() == Some("success") {
                            seen_success_result = true;
                            false
                        } else if is_spurious_glm_error {
                            // Spurious error BEFORE success - still suppress based on characteristics
                            // This handles the reverse-order case where error arrives first
                            true
                        } else {
                            false
                        }
                    } else {
                        false
                    }
                } else {
                    false
                };

                // In debug mode, also show the raw JSON
                if self.verbosity.is_debug() {
                    eprintln!(
                        "{}[DEBUG]{} {}{}{}",
                        c.dim(),
                        c.reset(),
                        c.dim(),
                        &line,
                        c.reset()
                    );
                }

                // Skip suppressed result events but still log them
                if should_skip_result {
                    if logging_enabled {
                        writeln!(log_buffer, "{line}")?;
                    }
                    monitor.record_control_event();
                    continue;
                }

                // Parse the event once - parse_event handles malformed JSON by returning None
                match self.parse_event(&line) {
                    Some(output) => {
                        // Check if this is a partial/delta event (streaming content)
                        if trimmed.starts_with('{') {
                            if let Ok(event) = serde_json::from_str::<ClaudeEvent>(&line) {
                                if Self::is_partial_event(&event) {
                                    monitor.record_partial_event();
                                } else {
                                    monitor.record_parsed();
                                }
                            } else {
                                monitor.record_parsed();
                            }
                        } else {
                            monitor.record_parsed();
                        }
                        // Write output to printer
                        let mut printer = self.printer.borrow_mut();
                        write!(printer, "{output}")?;
                        printer.flush()?;
                    }
                    None => {
                        // Check if this was a control event (state management with no user output)
                        // Control events are valid JSON that return empty output but aren't "ignored"
                        if trimmed.starts_with('{') {
                            if let Ok(event) = serde_json::from_str::<ClaudeEvent>(&line) {
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
                    writeln!(log_buffer, "{line}")?;
                }
            }
        }

        // Write accumulated log content to workspace
        if let Some(log_path) = &self.log_path {
            workspace.append_bytes(log_path, &log_buffer)?;
        }
        if let Some(warning) = monitor.check_and_warn(*c) {
            let mut printer = self.printer.borrow_mut();
            writeln!(printer, "{warning}")?;
            printer.flush()?;
        }
        Ok(())
    }
}
