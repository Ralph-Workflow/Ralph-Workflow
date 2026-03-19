// Claude parser implementation.
//
// Contains the ClaudeParser struct and its core methods.

use std::cell::RefCell;
use std::io::BufRead;
use std::rc::Rc;

use crate::json_parser::claude::io::ParserState;
#[cfg(any(test, feature = "test-utils"))]
use crate::json_parser::health::StreamingQualityMetrics;
use crate::json_parser::incremental_parser::IncrementalNdjsonParser;
use crate::json_parser::printer::Printable;
use crate::json_parser::printer::StdoutPrinter;
use crate::json_parser::types::{ContentBlock, ContentBlockDelta};

/// Claude event parser
///
/// Note: This parser is designed for single-threaded use only.
/// Do not share this parser across threads.
pub struct ClaudeParser {
    colors: Colors,
    pub(crate) verbosity: Verbosity,
    log_path: Option<std::path::PathBuf>,
    display_name: String,
    state: ParserState,
    show_streaming_metrics: bool,
    printer: Rc<RefCell<dyn Printable>>,
}

impl ClaudeParser {
    #[must_use]
    pub fn new(colors: Colors, verbosity: Verbosity) -> Self {
        Self::with_printer(
            colors,
            verbosity,
            Rc::new(RefCell::new(StdoutPrinter::new())),
        )
    }

    pub fn with_printer(
        colors: Colors,
        verbosity: Verbosity,
        printer: Rc<RefCell<dyn Printable>>,
    ) -> Self {
        let verbose_warnings = matches!(verbosity, Verbosity::Debug);

        Self {
            colors,
            verbosity,
            log_path: None,
            display_name: "Claude".to_string(),
            state: ParserState::new(verbose_warnings),
            show_streaming_metrics: false,
            printer,
        }
    }

    pub(crate) const fn with_show_streaming_metrics(mut self, show: bool) -> Self {
        self.show_streaming_metrics = show;
        self
    }

    #[must_use]
    pub fn with_display_name(mut self, display_name: &str) -> Self {
        self.display_name = display_name.to_string();
        self
    }

    pub(crate) fn with_log_file(mut self, path: &str) -> Self {
        self.log_path = Some(std::path::PathBuf::from(path));
        self
    }

    /// Set the terminal mode for this parser.
    ///
    /// # Arguments
    ///
    /// * `mode` - The terminal mode to use
    ///
    /// # Returns
    ///
    /// Self for builder pattern chaining
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_terminal_mode(self, mode: TerminalMode) -> Self {
        self.state.terminal_mode.replace(mode);
        self
    }

    /// Get a shared reference to the printer.
    ///
    /// This allows tests, monitoring, and other code to access the printer after parsing
    /// to verify output content, check for duplicates, or capture output for analysis.
    ///
    /// # Returns
    ///
    /// A clone of the shared printer reference (`Rc<RefCell<dyn Printable>>`)
    ///
    /// # Example
    ///
    /// ```ignore
    /// use ralph_workflow::json_parser::{ClaudeParser, printer::TestPrinter};
    /// use std::rc::Rc;
    /// use std::cell::RefCell;
    ///
    /// let printer = Rc::new(RefCell::new(TestPrinter::new()));
    /// let parser = ClaudeParser::with_printer(colors, verbosity, Rc::clone(&printer));
    ///
    /// // Parse events...
    ///
    /// // Now access the printer to verify output
    /// let printer_ref = parser.printer().borrow();
    /// assert!(!printer_ref.has_duplicate_consecutive_lines());
    /// ```
    /// Get a clone of the printer used by this parser.
    ///
    /// This is primarily useful for integration tests and monitoring in this repository.
    /// Only available with the `test-utils` feature.
    ///
    /// Note: downstream crates should avoid relying on this API in production builds.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn printer(&self) -> Rc<RefCell<dyn Printable>> {
        self.printer.clone()
    }

    pub(crate) fn with_printer_mut<R>(&mut self, f: impl FnOnce(&mut dyn Printable) -> R) -> R {
        let mut printer_ref = self.printer.borrow_mut();
        f(&mut *printer_ref)
    }

    /// Get streaming quality metrics from the current session.
    ///
    /// This provides insight into the deduplication and streaming quality of the
    /// parsing session, including:
    /// - Number of snapshot repairs (when the agent sent accumulated content as a delta)
    /// - Number of large deltas (potential protocol violations)
    /// - Total deltas processed
    ///
    /// Useful for testing, monitoring, and debugging streaming behavior.
    /// Only available with the `test-utils` feature.
    ///
    /// # Returns
    ///
    /// A copy of the streaming quality metrics from the internal `StreamingSession`.
    ///
    /// # Example
    ///
    /// ```ignore
    /// use ralph_workflow::json_parser::{ClaudeParser, printer::TestPrinter};
    /// use std::rc::Rc;
    /// use std::cell::RefCell;
    ///
    /// let printer = Rc::new(RefCell::new(TestPrinter::new()));
    /// let parser = ClaudeParser::with_printer(colors, verbosity, Rc::clone(&printer));
    ///
    /// // Parse events...
    ///
    /// // Verify deduplication logic triggered
    /// let metrics = parser.streaming_metrics();
    /// assert!(metrics.snapshot_repairs_count > 0, "Snapshot repairs should occur");
    /// ```
    #[cfg(any(test, feature = "test-utils"))]
    pub fn streaming_metrics(&self) -> StreamingQualityMetrics {
        self.state
            .streaming_session
            .borrow()
            .get_streaming_quality_metrics()
    }

    /// Parse and display a single Claude JSON event
    ///
    /// Returns `Some(formatted_output)` for valid events, or None for:
    /// - Malformed JSON (logged at debug level)
    /// - Unknown event types
    /// - Empty or whitespace-only output
    pub fn parse_event(&self, line: &str) -> Option<String> {
        let event: ClaudeEvent = if let Ok(e) = serde_json::from_str(line) {
            e
        } else {
            let trimmed = line.trim();
            if !trimmed.is_empty() && !trimmed.starts_with('{') {
                let finalize = self
                    .state
                    .with_session_mut(|session| self.finalize_in_place_full_mode(session));
                let out = format!("{finalize}{trimmed}\n");
                if *self.state.terminal_mode.borrow() == TerminalMode::Full {
                    self.state.with_cursor_up_active_mut(|cursor_up_active| {
                        if out.contains("\x1b[1B\n") {
                            *cursor_up_active = false;
                        }
                        if out.contains("\x1b[1A") {
                            *cursor_up_active = true;
                        }
                    });
                }
                return Some(out);
            }
            return None;
        };

        let finalize = if matches!(&event, ClaudeEvent::StreamEvent { .. }) {
            String::new()
        } else {
            self.state
                .with_session_mut(|session| self.finalize_in_place_full_mode(session))
        };
        let c = self.colors.clone();
        let prefix = &self.display_name;

        let output = match event {
            ClaudeEvent::System {
                subtype,
                session_id,
                cwd,
            } => self.format_system_event(subtype.as_ref(), session_id, cwd),
            ClaudeEvent::Assistant { message } => self.format_assistant_event(message.as_ref()),
            ClaudeEvent::User { message } => self.format_user_event(message),
            ClaudeEvent::Result {
                subtype,
                duration_ms,
                total_cost_usd,
                num_turns,
                result,
                error,
            } => self.format_result_event(
                subtype,
                duration_ms,
                total_cost_usd,
                num_turns,
                result,
                error,
            ),
            ClaudeEvent::StreamEvent { event } => self.parse_stream_event(event),
            ClaudeEvent::Unknown => {
                format_unknown_json_event(line, prefix, c, self.verbosity.is_verbose())
            }
        };

        let output = if output.is_empty() {
            finalize
        } else {
            format!("{finalize}{output}")
        };

        if output.is_empty() {
            None
        } else {
            if *self.state.terminal_mode.borrow() == TerminalMode::Full {
                self.state.with_cursor_up_active_mut(|cursor_up_active| {
                    if output.contains("\x1b[1B\n") {
                        *cursor_up_active = false;
                    }
                });
            }
            Some(output)
        }
    }

    /// Parse a streaming event for delta/partial updates
    ///
    /// Handles the nested events within `stream_event`:
    /// - MessageStart/Stop: Manage session state
    /// - `ContentBlockStart`: Initialize new content blocks
    /// - ContentBlockDelta/TextDelta: Accumulate and display incrementally
    /// - `ContentBlockStop`: Finalize content blocks
    /// - `MessageDelta`: Process message metadata without output
    /// - Error: Display appropriately
    ///
    /// Returns String for display content, empty String for control events.
    fn parse_stream_event(&self, event: StreamInnerEvent) -> String {
        match event {
            StreamInnerEvent::MessageStart {
                message,
                message_id,
            } => {
                let in_place_finalize = self
                    .state
                    .with_session_mut(|session| self.finalize_in_place_full_mode(session));

                {
                    self.state.with_thinking_active_index_mut(|idx| *idx = None);
                    self.state
                        .with_thinking_non_tty_indices_mut(|indices| indices.clear());
                    self.state
                        .with_suppress_thinking_for_message_mut(|v| *v = false);
                    self.state.with_text_line_active_mut(|v| *v = false);
                    self.state.with_cursor_up_active_mut(|v| *v = false);
                }

                let effective_message_id =
                    message_id.or_else(|| message.as_ref().and_then(|m| m.id.clone()));
                self.state.with_session_mut(|session| {
                    session.set_current_message_id(effective_message_id);
                    session.on_message_start();
                });

                {
                    self.state.with_last_rendered_content_mut(|v| v.clear());
                }

                in_place_finalize
            }
            StreamInnerEvent::ContentBlockStart {
                index: Some(index),
                content_block: Some(block),
            } => {
                self.state.with_session_mut(|session| {
                    session.on_content_block_start(index);
                    match &block {
                        ContentBlock::Text { text: Some(t) } if !t.is_empty() => {
                            session.on_text_delta(index, t);
                        }
                        ContentBlock::ToolUse { name, input } => {
                            if let Some(n) = name {
                                session.set_tool_name(index, Some(n.clone()));
                            }

                            if let Some(i) = input {
                                let input_str = if let serde_json::Value::String(s) = &i {
                                    s.clone()
                                } else {
                                    format_tool_input(i)
                                };
                                session.on_tool_input_delta(index, &input_str);
                            }
                        }
                        _ => {}
                    }
                });
                String::new()
            }
            StreamInnerEvent::ContentBlockStart {
                index: Some(index),
                content_block: None,
            } => {
                self.state.with_session_mut(|session| {
                    session.on_content_block_start(index);
                });
                String::new()
            }
            StreamInnerEvent::ContentBlockStart { .. } => String::new(),
            StreamInnerEvent::ContentBlockDelta {
                index: Some(index),
                delta: Some(delta),
            } => {
                let idx = index;
                let del = delta.clone();
                self.handle_content_block_delta_inner(idx, del)
            }
            StreamInnerEvent::TextDelta { text: Some(text) } => {
                let txt = text.clone();
                self.handle_text_delta_inner(&txt)
            }
            StreamInnerEvent::ContentBlockStop { .. } => String::new(),
            StreamInnerEvent::MessageDelta { .. } => String::new(),
            StreamInnerEvent::ContentBlockDelta { .. }
            | StreamInnerEvent::Ping
            | StreamInnerEvent::TextDelta { text: None }
            | StreamInnerEvent::Error { error: None } => String::new(),
            StreamInnerEvent::MessageStop => {
                let result = self.handle_message_stop_inner();
                result
            }
            StreamInnerEvent::Error {
                error: Some(err), ..
            } => self.handle_error_event(err),
            StreamInnerEvent::Unknown => self.handle_unknown_event(),
        }
    }

    fn handle_content_block_delta_inner(&self, index: u64, delta: ContentBlockDelta) -> String {
        self.state
            .with_session_mut(|session| self.handle_content_block_delta(session, index, delta))
    }

    fn handle_text_delta_inner(&self, text: &str) -> String {
        self.state
            .with_session_mut(|session| self.handle_text_delta(session, text))
    }

    fn handle_message_stop_inner(&self) -> String {
        self.state
            .with_session_mut(|session| self.handle_message_stop(session))
    }
}

impl ClaudeParser {
    #[expect(
        clippy::print_stderr,
        reason = "debug-only output for verbose debugging"
    )]
    pub fn parse_stream<R: BufRead>(
        &mut self,
        mut reader: R,
        workspace: &dyn crate::workspace::Workspace,
    ) -> std::io::Result<()> {
        let c = self.colors.clone();
        let monitor = HealthMonitor::new("Claude");
        let logging_enabled = self.log_path.is_some();
        let mut log_buffer: Vec<u8> = Vec::new();

        let mut incremental_parser = IncrementalNdjsonParser::new();

        let seen_success_result = std::cell::Cell::new(false);

        loop {
            let events = {
                let chunk = reader.fill_buf()?;
                if chunk.is_empty() {
                    break;
                }
                let consumed = chunk.len();
                let data: Vec<u8> = chunk.to_vec();
                reader.consume(consumed);

                let (new_parser, events) = incremental_parser.feed_and_get_events(&data);
                incremental_parser = new_parser;
                events
            };

            events.into_iter().for_each(|line| {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    return;
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

                        if is_spurious_glm_error && seen_success_result.get() {
                            true
                        } else if subtype.as_deref() == Some("success") {
                            seen_success_result.set(true);
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
                        let _ = writeln!(log_buffer, "{line}");
                    }
                    monitor.record_control_event();
                    return;
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
                        self.with_printer_mut(|printer| {
                            if write!(printer, "{output}").is_ok() {
                                printer.flush().ok();
                            }
                        });
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
                    let _ = writeln!(log_buffer, "{line}");
                }
            });
        }

        if let Some(log_path) = &self.log_path {
            workspace.append_bytes(log_path, &log_buffer)?;
        }
        if let Some(warning) = monitor.check_and_warn(c) {
            self.with_printer_mut(|printer| {
                writeln!(printer, "{warning}").ok();
                printer.flush().ok();
            });
        }
        Ok(())
    }
}
