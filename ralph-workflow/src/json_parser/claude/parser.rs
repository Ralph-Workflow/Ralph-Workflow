// Claude parser implementation.
//
// Contains the ClaudeParser struct and its core methods.

use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use super::io::ParserState;
use super::streaming_state::StreamingSession;
use super::terminal::TerminalMode;
use super::types::ContentBlock;
use super::{Colors, SharedPrinter};

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
    printer: SharedPrinter,
}

impl ClaudeParser {
    /// Create a new `ClaudeParser` with the given colors and verbosity.
    ///
    /// # Arguments
    ///
    /// * `colors` - Colors for terminal output
    /// * `verbosity` - Verbosity level for output
    ///
    /// # Returns
    ///
    /// A new `ClaudeParser` instance
    ///
    /// # Example
    ///
    /// ```ignore
    /// use ralph_workflow::json_parser::ClaudeParser;
    /// use ralph_workflow::logger::Colors;
    /// use ralph_workflow::config::Verbosity;
    ///
    /// let parser = ClaudeParser::new(Colors::new(), Verbosity::Normal);
    /// ```
    #[must_use]
    pub fn new(colors: Colors, verbosity: Verbosity) -> Self {
        Self::with_printer(colors, verbosity, super::printer::shared_stdout())
    }

    /// Create a new `ClaudeParser` with a custom printer.
    ///
    /// # Arguments
    ///
    /// * `colors` - Colors for terminal output
    /// * `verbosity` - Verbosity level for output
    /// * `printer` - Shared printer for output
    ///
    /// # Returns
    ///
    /// A new `ClaudeParser` instance
    pub fn with_printer(colors: Colors, verbosity: Verbosity, printer: SharedPrinter) -> Self {
        let verbose_warnings = matches!(verbosity, Verbosity::Debug);

        let _printer_is_terminal = printer.borrow().is_terminal();

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

    /// Set the display name for this parser.
    ///
    /// # Arguments
    ///
    /// * `display_name` - The name to display in output
    ///
    /// # Returns
    ///
    /// Self for builder pattern chaining
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

    fn streaming_session(&self) -> Rc<RefCell<StreamingSession>> {
        Rc::clone(&self.state.streaming_session)
    }

    fn thinking_non_tty_indices_mut(&mut self) -> RefMut<'_, std::collections::BTreeSet<u64>> {
        self.state.thinking_non_tty_indices.borrow_mut()
    }

    fn suppress_thinking_for_message_mut(&mut self) -> RefMut<'_, bool> {
        self.state.suppress_thinking_for_message.borrow_mut()
    }

    fn last_rendered_content(&self) -> Ref<'_, HashMap<String, String>> {
        self.state.last_rendered_content.borrow()
    }

    fn last_rendered_content_mut(&mut self) -> RefMut<'_, HashMap<String, String>> {
        self.state.last_rendered_content.borrow_mut()
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
    pub fn printer(&self) -> SharedPrinter {
        Rc::clone(&self.printer)
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
                let mut session = self.state.streaming_session.borrow_mut();
                let finalize = self.finalize_in_place_full_mode(&mut session);
                drop(session);
                let out = format!("{finalize}{trimmed}\n");
                if *self.state.terminal_mode.borrow() == TerminalMode::Full {
                    let mut cursor_up_active = self.state.cursor_up_active.borrow_mut();
                    if out.contains("\x1b[1B\n") {
                        *cursor_up_active = false;
                    }
                    if out.contains("\x1b[1A") {
                        *cursor_up_active = true;
                    }
                }
                return Some(out);
            }
            return None;
        };

        let finalize = if matches!(&event, ClaudeEvent::StreamEvent { .. }) {
            String::new()
        } else {
            let mut session = self.state.streaming_session.borrow_mut();
            let result = self.finalize_in_place_full_mode(&mut session);
            result
        };
        let c = &self.colors;
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
                format_unknown_json_event(line, prefix, *c, self.verbosity.is_verbose())
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
                let mut cursor_up_active = self.state.cursor_up_active.borrow_mut();
                if output.contains("\x1b[1B\n") {
                    *cursor_up_active = false;
                }
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
        let mut session = self.state.streaming_session.borrow_mut();

        match event {
            StreamInnerEvent::MessageStart {
                message,
                message_id,
            } => {
                let in_place_finalize = self.finalize_in_place_full_mode(&mut session);

                {
                    *self.state.thinking_active_index.borrow_mut() = None;
                    self.state.thinking_non_tty_indices.borrow_mut().clear();
                    *self.state.suppress_thinking_for_message.borrow_mut() = false;
                    *self.state.text_line_active.borrow_mut() = false;
                    *self.state.cursor_up_active.borrow_mut() = false;
                }

                let effective_message_id =
                    message_id.or_else(|| message.as_ref().and_then(|m| m.id.clone()));
                session.set_current_message_id(effective_message_id);
                session.on_message_start();

                {
                    self.state.last_rendered_content.borrow_mut().clear();
                }

                in_place_finalize
            }
            StreamInnerEvent::ContentBlockStart {
                index: Some(index),
                content_block: Some(block),
            } => {
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
                String::new()
            }
            StreamInnerEvent::ContentBlockStart {
                index: Some(index),
                content_block: None,
            } => {
                session.on_content_block_start(index);
                String::new()
            }
            StreamInnerEvent::ContentBlockStart { .. } => String::new(),
            StreamInnerEvent::ContentBlockDelta {
                index: Some(index),
                delta: Some(delta),
            } => {
                let idx = *index;
                let del = delta.clone();
                drop(session);
                self.handle_content_block_delta_inner(idx, del)
            }
            StreamInnerEvent::TextDelta { text: Some(text) } => {
                let txt = text.clone();
                drop(session);
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

    fn handle_content_block_delta_inner(
        &self,
        index: u64,
        delta: crate::json_parser::types::Delta,
    ) -> String {
        let mut session = self.state.streaming_session.borrow_mut();
        self.handle_content_block_delta(&mut session, index, delta)
    }

    fn handle_text_delta_inner(&self, text: &str) -> String {
        let mut session = self.state.streaming_session.borrow_mut();
        self.handle_text_delta(&mut session, text)
    }

    fn handle_message_stop_inner(&self) -> String {
        let mut session = self.state.streaming_session.borrow_mut();
        self.handle_message_stop(&mut session)
    }
}
