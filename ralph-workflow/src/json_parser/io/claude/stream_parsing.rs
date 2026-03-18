// Claude stream parsing methods.
//
// Contains parse_stream and event classification methods.
// This file includes the original stream_parsing implementations from the claude module.

include!("../../claude/stream_parsing.rs");

impl ClaudeParser {
    const fn is_control_event(event: &ClaudeEvent) -> bool {
        match event {
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

    const fn is_partial_event(event: &ClaudeEvent) -> bool {
        match event {
            ClaudeEvent::StreamEvent { event } => matches!(
                event,
                StreamInnerEvent::ContentBlockDelta { .. } | StreamInnerEvent::TextDelta { .. }
            ),
            _ => false,
        }
    }

    #[expect(
        clippy::print_stderr,
        reason = "debug-only output for verbose debugging"
    )]
    pub fn parse_stream<R: BufRead>(
        &self,
        mut reader: R,
        workspace: &dyn crate::workspace::Workspace,
    ) -> io::Result<()> {
        let c = &self.colors;
        let monitor = HealthMonitor::new("Claude");
        let logging_enabled = self.log_path.is_some();
        let mut log_buffer: Vec<u8> = Vec::new();

        let mut incremental_parser = IncrementalNdjsonParser::new();
        let mut byte_buffer = Vec::new();

        let mut seen_success_result = false;

        loop {
            byte_buffer.clear();
            let chunk = reader.fill_buf()?;
            if chunk.is_empty() {
                break;
            }

            byte_buffer.extend_from_slice(chunk);
            let consumed = chunk.len();
            reader.consume(consumed);

            let json_events = incremental_parser.feed(&byte_buffer);

            for line in json_events {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }

                let should_skip_result = if trimmed.starts_with('{') {
                    let has_errors_with_content =
                        serde_json::from_str::<serde_json::Value>(trimmed).is_ok_and(|json| {
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

                        let is_spurious_glm_error = is_error_result
                            && duration_ms.unwrap_or(0) < 100
                            && (error.is_none()
                                || error.as_ref().is_some_and(std::string::String::is_empty))
                            && !has_errors_with_content;

                        if is_spurious_glm_error && seen_success_result {
                            true
                        } else if subtype.as_deref() == Some("success") {
                            seen_success_result = true;
                            false
                        } else if is_spurious_glm_error {
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

                if should_skip_result {
                    if logging_enabled {
                        writeln!(log_buffer, "{line}")?;
                    }
                    monitor.record_control_event();
                    continue;
                }

                match self.parse_event(&line) {
                    Some(output) => {
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
                        let mut printer = self.printer.borrow_mut();
                        write!(printer, "{output}")?;
                        printer.flush()?;
                    }
                    None => {
                        if trimmed.starts_with('{') {
                            if let Ok(event) = serde_json::from_str::<ClaudeEvent>(&line) {
                                if Self::is_control_event(&event) {
                                    monitor.record_control_event();
                                } else {
                                    monitor.record_unknown_event();
                                }
                            } else {
                                monitor.record_parse_error();
                            }
                        } else {
                            monitor.record_ignored();
                        }
                    }
                }

                if logging_enabled {
                    writeln!(log_buffer, "{line}")?;
                }
            }
        }

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
