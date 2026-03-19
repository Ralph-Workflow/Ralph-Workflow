use std::cell::RefCell;
use std::collections::{BTreeSet, HashMap};
use std::io::{BufRead, Write};

use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::HealthMonitor;

pub struct ParserState {
    pub terminal_mode: RefCell<TerminalMode>,
    pub streaming_session: std::rc::Rc<RefCell<StreamingSession>>,
    pub thinking_active_index: RefCell<Option<u64>>,
    pub thinking_non_tty_indices: RefCell<BTreeSet<u64>>,
    pub suppress_thinking_for_message: RefCell<bool>,
    pub text_line_active: RefCell<bool>,
    pub cursor_up_active: RefCell<bool>,
    pub last_rendered_content: RefCell<HashMap<String, String>>,
}

impl ParserState {
    pub fn new(verbose_warnings: bool) -> Self {
        let streaming_session = StreamingSession::new().with_verbose_warnings(verbose_warnings);
        Self {
            terminal_mode: RefCell::new(TerminalMode::detect()),
            streaming_session: std::rc::Rc::new(RefCell::new(streaming_session)),
            thinking_active_index: RefCell::new(None),
            thinking_non_tty_indices: RefCell::new(BTreeSet::new()),
            suppress_thinking_for_message: RefCell::new(false),
            text_line_active: RefCell::new(false),
            cursor_up_active: RefCell::new(false),
            last_rendered_content: RefCell::new(HashMap::new()),
        }
    }

    pub fn with_session_mut<R>(&self, f: impl FnOnce(&mut StreamingSession) -> R) -> R {
        f(&mut self.streaming_session.borrow_mut())
    }

    pub fn with_cursor_up_active_mut<R>(&self, f: impl FnOnce(&mut bool) -> R) -> R {
        f(&mut self.cursor_up_active.borrow_mut())
    }

    pub fn with_thinking_active_index_mut<R>(&self, f: impl FnOnce(&mut Option<u64>) -> R) -> R {
        f(&mut self.thinking_active_index.borrow_mut())
    }

    pub fn with_thinking_non_tty_indices_mut<R>(
        &self,
        f: impl FnOnce(&mut BTreeSet<u64>) -> R,
    ) -> R {
        f(&mut self.thinking_non_tty_indices.borrow_mut())
    }

    pub fn with_suppress_thinking_for_message_mut<R>(&self, f: impl FnOnce(&mut bool) -> R) -> R {
        f(&mut self.suppress_thinking_for_message.borrow_mut())
    }

    pub fn with_text_line_active_mut<R>(&self, f: impl FnOnce(&mut bool) -> R) -> R {
        f(&mut self.text_line_active.borrow_mut())
    }

    pub fn with_last_rendered_content_mut<R>(
        &self,
        f: impl FnOnce(&mut HashMap<String, String>) -> R,
    ) -> R {
        f(&mut self.last_rendered_content.borrow_mut())
    }

    pub fn with_terminal_mode_mut<R>(&self, f: impl FnOnce(&mut TerminalMode) -> R) -> R {
        f(&mut self.terminal_mode.borrow_mut())
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
    ) -> io::Result<()> {
        use crate::json_parser::incremental_parser::IncrementalNdjsonParser;

        let c = &self.colors;
        let monitor = HealthMonitor::new("Claude");
        let logging_enabled = self.log_path.is_some();
        let mut log_buffer: Vec<u8> = Vec::new();

        let incremental_parser = IncrementalNdjsonParser::new();

        let seen_success_result = std::cell::Cell::new(false);

        loop {
            let chunk = reader.fill_buf()?;
            if chunk.is_empty() {
                break;
            }

            let consumed = chunk.len();
            reader.consume(consumed);

            let (new_parser, events) = incremental_parser.feed(chunk);
            let incremental_parser = new_parser;

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
        if let Some(warning) = monitor.check_and_warn(*c) {
            self.with_printer_mut(|printer| {
                writeln!(printer, "{warning}").ok();
                printer.flush().ok();
            });
        }
        Ok(())
    }
}
