// Claude parser implementation.
//
// Contains the ClaudeParser struct and its core methods.

/// Claude event parser
///
/// Note: This parser is designed for single-threaded use only.
/// Do not share this parser across threads.
pub struct ClaudeParser {
    colors: Colors,
    pub(crate) verbosity: Verbosity,
    /// Relative path to log file (if logging enabled)
    log_path: Option<std::path::PathBuf>,
    display_name: String,
    /// Unified streaming session tracker
    /// Provides single source of truth for streaming state across all content types
    streaming_session: Rc<RefCell<StreamingSession>>,
    /// Terminal mode for output formatting
    /// Detected at parse time and cached for performance
    terminal_mode: RefCell<TerminalMode>,
    /// Whether to show streaming quality metrics
    show_streaming_metrics: bool,
    /// Output printer for capturing or displaying output
    printer: SharedPrinter,

    /// Tracks whether a thinking delta line is currently being streamed.
    ///
    /// - In `TerminalMode::Full`, thinking deltas use the append-only streaming pattern (no cursor
    ///   movement during deltas) and must be finalized (emit the completion newline) before emitting
    ///   other newline-based output.
    /// - In `TerminalMode::Basic|None`, we suppress per-delta thinking output and flush accumulated
    ///   thinking content once at the next output boundary (or at `message_stop`).
    thinking_active_index: RefCell<Option<u64>>,

    /// Tracks which thinking content block indices have streamed thinking content that is eligible
    /// for non-TTY flushing.
    ///
    /// Some providers can emit multiple thinking blocks (multiple indices) within a single message.
    /// In non-TTY modes we suppress per-delta output, so we must remember all indices that
    /// accumulated thinking to flush them at `message_stop`.
    thinking_non_tty_indices: RefCell<std::collections::BTreeSet<u64>>,

    /// Once non-thinking output has started for the current message, suppress any
    /// subsequent thinking deltas to avoid corrupting visible output.
    ///
    /// Claude/CCS can occasionally emit thinking deltas after text deltas. Because
    /// both streams append on the current line in Full mode, allowing late thinking can
    /// glue onto or visually corrupt previously-rendered text.
    suppress_thinking_for_message: RefCell<bool>,

    /// Tracks whether a text delta line is currently being streamed (Full mode).
    ///
    /// In the append-only streaming pattern, deltas do not move the cursor; they simply
    /// append new suffixes on the current line. When true, any newline-based non-stream
    /// output should ensure the streamed line is finalized (emit the completion newline)
    /// before printing unrelated lines, to avoid "glued" output.
    text_line_active: RefCell<bool>,

    /// Defensive cursor state for legacy/inconsistent streams.
    ///
    /// The append-only streaming implementation should not emit cursor-up sequences,
    /// but real-world logs can include raw passthrough output with escape codes.
    /// When this flag is true, newline-based output should first emit a completion newline
    /// to avoid overwriting/gluing onto visible content.
    cursor_up_active: RefCell<bool>,

    /// Tracks the last rendered content for append-only streaming in Full mode.
    ///
    /// In append-only mode, we emit the prefix once, then only emit new suffixes for subsequent deltas.
    /// This map stores the last rendered content for each (`ContentType`, index) pair.
    /// Key format: "{`content_type}:{index`}" (e.g., "text:0", "thinking:1")
    last_rendered_content: RefCell<std::collections::HashMap<String, String>>,
}

impl ClaudeParser {
    #[must_use]
    pub fn new(colors: Colors, verbosity: Verbosity) -> Self {
        Self::with_printer(colors, verbosity, super::printer::shared_stdout())
    }

    pub fn with_printer(colors: Colors, verbosity: Verbosity, printer: SharedPrinter) -> Self {
        let verbose_warnings = matches!(verbosity, Verbosity::Debug);
        let streaming_session = StreamingSession::new().with_verbose_warnings(verbose_warnings);

        let _printer_is_terminal = printer.borrow().is_terminal();

        Self {
            colors,
            verbosity,
            log_path: None,
            display_name: "Claude".to_string(),
            streaming_session: Rc::new(RefCell::new(streaming_session)),
            terminal_mode: RefCell::new(TerminalMode::detect()),
            show_streaming_metrics: false,
            printer,
            thinking_active_index: RefCell::new(None),
            thinking_non_tty_indices: RefCell::new(std::collections::BTreeSet::new()),
            suppress_thinking_for_message: RefCell::new(false),
            text_line_active: RefCell::new(false),
            cursor_up_active: RefCell::new(false),
            last_rendered_content: RefCell::new(std::collections::HashMap::new()),
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

    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_terminal_mode(self, mode: TerminalMode) -> Self {
        *self.terminal_mode.borrow_mut() = mode;
        self
    }

    #[cfg(any(test, feature = "test-utils"))]
    pub fn printer(&self) -> SharedPrinter {
        Rc::clone(&self.printer)
    }

    #[cfg(any(test, feature = "test-utils"))]
    pub fn streaming_metrics(&self) -> StreamingQualityMetrics {
        self.streaming_session
            .borrow()
            .get_streaming_quality_metrics()
    }

    pub fn parse_event(&self, line: &str) -> Option<String> {
        let event: ClaudeEvent = if let Ok(e) = serde_json::from_str(line) {
            e
        } else {
            let trimmed = line.trim();
            if !trimmed.is_empty() && !trimmed.starts_with('{') {
                let mut session = self.streaming_session.borrow_mut();
                let finalize = self.finalize_in_place_full_mode(&mut session);
                let out = format!("{finalize}{trimmed}\n");
                if *self.terminal_mode.borrow() == TerminalMode::Full {
                    let mut cursor_up_active = self.cursor_up_active.borrow_mut();
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
            let mut session = self.streaming_session.borrow_mut();
            self.finalize_in_place_full_mode(&mut session)
        };
        let c = &self.colors;
        let prefix = &self.display_name;

        let output = match event {
            ClaudeEvent::System { subtype, session_id, cwd } => {
                self.format_system_event(subtype.as_ref(), session_id, cwd)
            }
            ClaudeEvent::Assistant { message } => self.format_assistant_event(message.as_ref()),
            ClaudeEvent::User { message } => self.format_user_event(message),
            ClaudeEvent::Result { subtype, duration_ms, total_cost_usd, num_turns, result, error } => {
                self.format_result_event(subtype, duration_ms, total_cost_usd, num_turns, result, error)
            }
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
            if *self.terminal_mode.borrow() == TerminalMode::Full {
                let mut cursor_up_active = self.cursor_up_active.borrow_mut();
                if output.contains("\x1b[1B\n") {
                    *cursor_up_active = false;
                }
            }
            Some(output)
        }
    }

    fn parse_stream_event(&self, event: StreamInnerEvent) -> String {
        let mut session = self.streaming_session.borrow_mut();

        match event {
            StreamInnerEvent::MessageStart { message, message_id } => {
                let in_place_finalize = self.finalize_in_place_full_mode(&mut session);

                *self.thinking_active_index.borrow_mut() = None;
                self.thinking_non_tty_indices.borrow_mut().clear();
                *self.suppress_thinking_for_message.borrow_mut() = false;
                *self.text_line_active.borrow_mut() = false;
                *self.cursor_up_active.borrow_mut() = false;

                let effective_message_id =
                    message_id.or_else(|| message.as_ref().and_then(|m| m.id.clone()));
                session.set_current_message_id(effective_message_id);
                session.on_message_start();
                self.last_rendered_content.borrow_mut().clear();
                in_place_finalize
            }
            StreamInnerEvent::ContentBlockStart { index: Some(index), content_block: Some(block) } => {
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
            StreamInnerEvent::ContentBlockStart { index: Some(index), content_block: None } => {
                session.on_content_block_start(index);
                String::new()
            }
            StreamInnerEvent::ContentBlockStart { .. } => String::new(),
            StreamInnerEvent::ContentBlockDelta { index: Some(index), delta: Some(delta) } => {
                self.handle_content_block_delta(&mut session, index, delta)
            }
            StreamInnerEvent::TextDelta { text: Some(text) } => {
                self.handle_text_delta(&mut session, &text)
            }
            StreamInnerEvent::ContentBlockStop { .. } => String::new(),
            StreamInnerEvent::MessageDelta { .. } => String::new(),
            StreamInnerEvent::ContentBlockDelta { .. }
            | StreamInnerEvent::Ping
            | StreamInnerEvent::TextDelta { text: None }
            | StreamInnerEvent::Error { error: None } => String::new(),
            StreamInnerEvent::MessageStop => self.handle_message_stop(&mut session),
            StreamInnerEvent::Error { error: Some(err), .. } => self.handle_error_event(err),
            StreamInnerEvent::Unknown => self.handle_unknown_event(),
        }
    }
}
