impl GeminiParser {
    /// Parse and display a single Gemini JSON event
    ///
    /// Returns `Some(formatted_output)` for valid events, or None for:
    /// - Malformed JSON (non-JSON text passed through if meaningful)
    /// - Unknown event types
    /// - Empty or whitespace-only output
    pub(crate) fn parse_event(&self, line: &str) -> Option<String> {
        let event: GeminiEvent = if let Ok(e) = serde_json::from_str(line) {
            e
        } else {
            // Non-JSON line - pass through as-is if meaningful
            let trimmed = line.trim();
            if !trimmed.is_empty() && !trimmed.starts_with('{') {
                return Some(format!("{trimmed}\n"));
            }
            return None;
        };
        let c = &self.colors;
        let prefix = &self.display_name;

        let output = match event {
            GeminiEvent::Init {
                session_id, model, ..
            } => self.format_init_event(session_id, model),
            GeminiEvent::Message {
                role,
                content,
                delta,
            } => self.format_message_event(role, content, delta),
            GeminiEvent::ToolUse {
                tool_name,
                parameters,
                ..
            } => self.format_tool_use_event(tool_name, parameters.as_ref()),
            GeminiEvent::ToolResult { status, output, .. } => {
                self.format_tool_result_event(status, output.as_ref())
            }
            GeminiEvent::Error { message, code, .. } => self.format_error_event(message, code),
            GeminiEvent::Result { status, stats, .. } => self.format_result_event(status, stats),
            GeminiEvent::Unknown => {
                // Use the generic unknown event formatter for consistent handling
                format_unknown_json_event(line, prefix, *c, self.verbosity.is_verbose())
            }
        };

        if output.is_empty() {
            None
        } else {
            Some(output)
        }
    }

    /// Format an Init event
    fn format_init_event(&self, session_id: Option<String>, model: Option<String>) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        // Reset streaming state on new session
        self.streaming_session.borrow_mut().on_message_start();
        self.last_rendered_content.borrow_mut().clear();
        let sid = session_id.unwrap_or_else(|| "unknown".to_string());
        // Set the current message ID for duplicate detection
        self.streaming_session
            .borrow_mut()
            .set_current_message_id(Some(sid.clone()));
        let model_str = model.unwrap_or_else(|| "unknown".to_string());
        format!(
            "{}[{}]{} {}Session started{} {}({:.8}..., {}){}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.cyan(),
            c.reset(),
            c.dim(),
            sid,
            model_str,
            c.reset()
        )
    }

    /// Format a Message event
    fn format_message_event(
        &self,
        role: Option<String>,
        content: Option<String>,
        delta: Option<bool>,
    ) -> String {
        use crate::json_parser::terminal::TerminalMode;

        let c = &self.colors;
        let prefix = &self.display_name;

        let role_str = role.unwrap_or_else(|| "unknown".to_string());
        let is_delta = delta.unwrap_or(false);

        if let Some(text) = content {
            if is_delta && role_str == "assistant" {
                let (show_prefix, accumulated_text, has_prefix) = {
                    let mut session = self.streaming_session.borrow_mut();
                    let show_prefix = session.on_text_delta(0, &text);

                    let accumulated_text = session
                        .get_accumulated(ContentType::Text, "0")
                        .unwrap_or("")
                        .to_string();

                    let sanitized_text = delta_display::sanitize_for_display(&accumulated_text);
                    if sanitized_text.is_empty() {
                        return String::new();
                    }

                    if session.is_content_hash_rendered(ContentType::Text, "0", &sanitized_text) {
                        return String::new();
                    }

                    let has_prefix = session.has_rendered_prefix(ContentType::Text, "0");

                    session.mark_rendered(ContentType::Text, "0");
                    session.mark_content_hash_rendered(ContentType::Text, "0", &sanitized_text);

                    (show_prefix, accumulated_text, has_prefix)
                };

                let terminal_mode = *self.terminal_mode.borrow();
                let key = "text:0";

                if show_prefix && !has_prefix {
                    let rendered = TextDeltaRenderer::render_first_delta(
                        &accumulated_text,
                        prefix,
                        *c,
                        terminal_mode,
                    );

                    let sanitized = delta_display::sanitize_for_display(&accumulated_text);
                    self.last_rendered_content
                        .borrow_mut()
                        .insert(key.to_string(), sanitized);

                    // First delta already emitted the full accumulated content, so we can stop here.
                    // Subsequent deltas will compute and emit only the new suffix.

                    return rendered;
                }

                let sanitized = delta_display::sanitize_for_display(&accumulated_text);
                let last_rendered = self
                    .last_rendered_content
                    .borrow()
                    .get(key)
                    .cloned()
                    .unwrap_or_default();

                let suffix = crate::json_parser::delta_display::compute_append_only_suffix(
                    &last_rendered,
                    sanitized.as_str(),
                );

                // Detect discontinuities in Gemini text deltas
                if suffix.is_empty() && !last_rendered.is_empty() && !sanitized.is_empty() {
                    #[cfg(debug_assertions)]
                    eprintln!(
                        "Warning: Delta discontinuity detected in Gemini text. \
                         Provider sent non-monotonic content. \
                         Last: {:?} (len={}), Current: {:?} (len={})",
                        &last_rendered[..last_rendered.len().min(40)],
                        last_rendered.len(),
                        &sanitized[..sanitized.len().min(40)],
                        sanitized.len()
                    );
                }

                self.last_rendered_content
                    .borrow_mut()
                    .insert(key.to_string(), sanitized.clone());

                return match terminal_mode {
                    TerminalMode::Full => format!("{}{}{}", c.white(), suffix, c.reset()),
                    TerminalMode::Basic | TerminalMode::None => String::new(),
                };
            } else if !is_delta && role_str == "assistant" {
                let session = self.streaming_session.borrow();
                let is_duplicate = session.get_current_message_id().map_or_else(
                    || session.has_any_streamed_content(),
                    |message_id| session.is_duplicate_final_message(message_id),
                );
                let was_streaming = session.has_any_streamed_content();
                let metrics = session.get_streaming_quality_metrics();
                drop(session);

                // Finalize the message (this marks it as displayed)
                let _was_in_block = self.streaming_session.borrow_mut().on_message_stop();

                let terminal_mode = *self.terminal_mode.borrow();

                // In non-TTY modes, per-delta output is suppressed. Flush accumulated assistant text
                // once at the completion boundary so piped/log output contains the assistant content.
                let text_flush_non_tty = match terminal_mode {
                    TerminalMode::Full => String::new(),
                    TerminalMode::Basic | TerminalMode::None => {
                        let session = self.streaming_session.borrow();
                        let mut out = String::new();
                        for key in session.accumulated_keys(ContentType::Text) {
                            let accumulated = session
                                .get_accumulated(ContentType::Text, &key)
                                .unwrap_or("");
                            let sanitized = delta_display::sanitize_for_display(accumulated);
                            if sanitized.is_empty() {
                                continue;
                            }

                            match terminal_mode {
                                TerminalMode::Basic => {
                                    let _ = writeln!(
                                        out,
                                        "{}[{}]{} {}{}{}",
                                        c.dim(),
                                        prefix,
                                        c.reset(),
                                        c.white(),
                                        sanitized,
                                        c.reset()
                                    );
                                }
                                TerminalMode::None => {
                                    let _ = writeln!(out, "[{prefix}] {sanitized}");
                                }
                                TerminalMode::Full => unreachable!(),
                            }
                        }
                        out
                    }
                };

                // If this is a duplicate or content was streamed, use TextDeltaRenderer for completion
                if is_duplicate || was_streaming {
                    let completion = TextDeltaRenderer::render_completion(terminal_mode);
                    let show_metrics = (self.verbosity.is_debug() || self.show_streaming_metrics)
                        && metrics.total_deltas > 0;
                    if show_metrics {
                        return format!(
                            "{}{}\n{}",
                            text_flush_non_tty,
                            completion,
                            metrics.format(*c)
                        );
                    }
                    return format!("{text_flush_non_tty}{completion}");
                }

                // Otherwise, show the full content (non-streaming path)
                let limit = self.verbosity.truncate_limit("text");
                let preview = truncate_text(&text, limit);

                return format!(
                    "{}[{}]{} {}{}{}\n",
                    c.dim(),
                    prefix,
                    c.reset(),
                    c.white(),
                    preview,
                    c.reset()
                );
            }
            // User or other role messages
            let limit = self.verbosity.truncate_limit("text");
            let preview = truncate_text(&text, limit);
            return format!(
                "{}[{}]{} {}{}:{} {}{}{}\n",
                c.dim(),
                prefix,
                c.reset(),
                c.blue(),
                role_str,
                c.reset(),
                c.dim(),
                preview,
                c.reset()
            );
        }
        String::new()
    }

    /// Format a `ToolUse` event
    fn format_tool_use_event(
        &self,
        tool_name: Option<String>,
        parameters: Option<&serde_json::Value>,
    ) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        let tool_name = tool_name.unwrap_or_else(|| "unknown".to_string());
        let mut out = format!(
            "{}[{}]{} {}Tool{}: {}{}{}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.magenta(),
            c.reset(),
            c.bold(),
            tool_name,
            c.reset()
        );
        if self.verbosity.show_tool_input() {
            if let Some(params) = parameters {
                let params_str = format_tool_input(params);
                let limit = self.verbosity.truncate_limit("tool_input");
                let preview = truncate_text(&params_str, limit);
                if !preview.is_empty() {
                    let _ = writeln!(
                        out,
                        "{}[{}]{} {}  └─ {}{}",
                        c.dim(),
                        prefix,
                        c.reset(),
                        c.dim(),
                        preview,
                        c.reset()
                    );
                }
            }
        }
        out
    }

    /// Format a `ToolResult` event
    fn format_tool_result_event(&self, status: Option<String>, output: Option<&String>) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        let status_str = status.unwrap_or_else(|| "unknown".to_string());
        let is_success = status_str == "success";
        let icon = if is_success { CHECK } else { CROSS };
        let color = if is_success { c.green() } else { c.red() };

        let mut out = format!(
            "{}[{}]{} {}{} Tool result{}\n",
            c.dim(),
            prefix,
            c.reset(),
            color,
            icon,
            c.reset()
        );

        if self.verbosity.is_verbose() {
            if let Some(output_text) = output {
                let limit = self.verbosity.truncate_limit("tool_result");
                let preview = truncate_text(output_text, limit);
                let _ = writeln!(
                    out,
                    "{}[{}]{} {}  └─ {}{}",
                    c.dim(),
                    prefix,
                    c.reset(),
                    c.dim(),
                    preview,
                    c.reset()
                );
            }
        }
        out
    }

    /// Format an `Error` event
    fn format_error_event(&self, message: Option<String>, code: Option<String>) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        let msg = message.unwrap_or_else(|| "unknown error".to_string());
        let code_str = code.map_or_else(String::new, |c| format!(" ({c})"));
        format!(
            "{}[{}]{} {}{} Error{}:{} {}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.red(),
            CROSS,
            code_str,
            c.reset(),
            msg
        )
    }

    /// Format a `Result` event
    fn format_result_event(
        &self,
        status: Option<String>,
        event_stats: Option<crate::json_parser::types::GeminiStats>,
    ) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        let status_result = status.unwrap_or_else(|| "unknown".to_string());
        let is_success = status_result == "success";
        let icon = if is_success { CHECK } else { CROSS };
        let color = if is_success { c.green() } else { c.red() };

        let stats_display = event_stats.map_or_else(String::new, |s| {
            let duration_s = s.duration_ms.unwrap_or(0) / 1000;
            let duration_m = duration_s / 60;
            let duration_s_rem = duration_s % 60;
            let input = s.input_tokens.unwrap_or(0);
            let output = s.output_tokens.unwrap_or(0);
            let tools = s.tool_calls.unwrap_or(0);
            format!("({duration_m}m {duration_s_rem}s, in:{input} out:{output}, {tools} tools)")
        });

        format!(
            "{}[{}]{} {}{} {}{} {}{}{}\n",
            c.dim(),
            prefix,
            c.reset(),
            color,
            icon,
            status_result,
            c.reset(),
            c.dim(),
            stats_display,
            c.reset()
        )
    }
}
