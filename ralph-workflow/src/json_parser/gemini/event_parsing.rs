impl GeminiParser {
    fn parse_non_json_line(line: &str) -> Option<String> {
        let trimmed = line.trim();
        if !trimmed.is_empty() && !trimmed.starts_with('{') { Some(format!("{trimmed}\n")) }
        else { None }
    }

    fn dispatch_gemini_event_a(&self, event: &GeminiEvent) -> Option<String> {
        match event {
            GeminiEvent::Init { session_id, model, .. } => Some(self.format_init_event(session_id.clone(), model.clone())),
            GeminiEvent::Message { role, content, delta } => Some(self.format_message_event(role.clone(), content.clone(), *delta)),
            GeminiEvent::ToolUse { tool_name, parameters, .. } => Some(self.format_tool_use_event(tool_name.clone(), parameters.as_ref())),
            _ => None,
        }
    }

    fn dispatch_gemini_event_b(&self, event: &GeminiEvent, line: &str) -> String {
        match event {
            GeminiEvent::ToolResult { status, output, .. } => self.format_tool_result_event(status.clone(), output.as_ref()),
            GeminiEvent::Error { message, code, .. } => self.format_error_event(message.clone(), code.clone()),
            GeminiEvent::Result { status, stats, .. } => self.format_result_event(status.clone(), stats.clone()),
            GeminiEvent::Unknown => format_unknown_json_event(line, &self.display_name, self.colors, self.verbosity.is_verbose()),
            _ => String::new(),
        }
    }

    fn dispatch_gemini_event(&self, event: GeminiEvent, line: &str) -> String {
        self.dispatch_gemini_event_a(&event)
            .unwrap_or_else(|| self.dispatch_gemini_event_b(&event, line))
    }

    /// Parse and display a single Gemini JSON event
    ///
    /// Returns `Some(formatted_output)` for valid events, or None for:
    /// - Malformed JSON (non-JSON text passed through if meaningful)
    /// - Unknown event types
    /// - Empty or whitespace-only output
    pub(crate) fn parse_event(&self, line: &str) -> Option<String> {
        let Ok(event) = serde_json::from_str::<GeminiEvent>(line) else {
            return Self::parse_non_json_line(line);
        };
        let output = self.dispatch_gemini_event(event, line);
        if output.is_empty() { None } else { Some(output) }
    }

    /// Format an Init event
    fn format_init_event(&self, session_id: Option<String>, model: Option<String>) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        // Reset streaming state on new session
        self.state.with_session_mut(|session| {
            session.on_message_start();
            session.set_current_message_id(session_id.clone());
        });
        self.state.with_last_rendered_content_mut(|v| v.clear());
        let model_str = model.unwrap_or_else(|| "unknown".to_string());
        format!(
            "{}[{}]{} {}Session started{} {}({:.8}..., {}){}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.cyan(),
            c.reset(),
            c.dim(),
            session_id.unwrap_or_else(|| "unknown".to_string()),
            model_str,
            c.reset()
        )
    }

    fn update_gemini_text_session(&self, text: &str) -> (bool, String, bool) {
        self.state.with_session_mut(|session| {
            let show_prefix = session.on_text_delta(0, text);
            let accumulated_text = session.get_accumulated(ContentType::Text, "0").unwrap_or("").to_string();
            let sanitized_text = delta_display::sanitize_for_display(&accumulated_text);
            if sanitized_text.is_empty() { return (false, String::new(), false); }
            if session.is_content_hash_rendered(ContentType::Text, "0", &sanitized_text) { return (false, String::new(), false); }
            let has_prefix = session.has_rendered_prefix(ContentType::Text, "0");
            session.mark_rendered(ContentType::Text, "0");
            session.mark_content_hash_rendered(ContentType::Text, "0", &sanitized_text);
            (show_prefix, accumulated_text, has_prefix)
        })
    }

    fn render_gemini_text_suffix(&self, accumulated_text: &str) -> String {
        use crate::json_parser::terminal::TerminalMode;
        let c = &self.colors;
        let key = "text:0";
        let sanitized = delta_display::sanitize_for_display(accumulated_text);
        let last_rendered = self.state.last_rendered_content.borrow().get(key).cloned().unwrap_or_default();
        let sanitized_clone = sanitized.clone();
        let suffix = crate::json_parser::delta_display::compute_append_only_suffix(&last_rendered, &sanitized_clone);
        self.state.with_last_rendered_content_mut(|v| { v.insert(key.to_string(), sanitized); });
        match *self.state.terminal_mode.borrow() {
            TerminalMode::Full => format!("{}{}{}", c.white(), suffix, c.reset()),
            TerminalMode::Basic | TerminalMode::None => String::new(),
        }
    }

    fn render_first_delta_with_cache(&self, accumulated_text: &str) -> String {
        let terminal_mode = *self.state.terminal_mode.borrow();
        let rendered = TextDeltaRenderer::render_first_delta(accumulated_text, &self.display_name, self.colors, terminal_mode);
        let sanitized = delta_display::sanitize_for_display(accumulated_text);
        self.state.with_last_rendered_content_mut(|v| { v.insert("text:0".to_string(), sanitized); });
        rendered
    }

    fn format_assistant_delta_message(&self, text: &str) -> String {
        let (show_prefix, accumulated_text, has_prefix) = self.update_gemini_text_session(text);
        if accumulated_text.is_empty() { return String::new(); }
        if show_prefix && !has_prefix { return self.render_first_delta_with_cache(&accumulated_text); }
        self.render_gemini_text_suffix(&accumulated_text)
    }

    fn flush_non_tty_accumulated_text(&self, terminal_mode: crate::json_parser::terminal::TerminalMode) -> String {
        use crate::json_parser::terminal::TerminalMode;
        let c = &self.colors;
        let prefix = &self.display_name;
        let session = self.state.streaming_session.borrow();
        session.accumulated_keys(ContentType::Text).iter().filter_map(|key| {
            let accumulated = session.get_accumulated(ContentType::Text, key.as_str()).unwrap_or("");
            let sanitized = delta_display::sanitize_for_display(accumulated);
            if sanitized.is_empty() { return None; }
            Some(match terminal_mode {
                TerminalMode::Basic => format!("{}[{}]{} {}{}{}", c.dim(), prefix, c.reset(), c.white(), sanitized, c.reset()),
                TerminalMode::None => format!("[{prefix}] {sanitized}"),
                TerminalMode::Full => unreachable!(),
            })
        }).collect::<Vec<_>>().join("")
    }

    fn query_message_stop_state(&self) -> (bool, bool, crate::json_parser::health::StreamingQualityMetrics) {
        let session = self.state.streaming_session.borrow();
        let is_duplicate = session.get_current_message_id().map_or_else(
            || session.has_any_streamed_content(),
            |message_id| session.is_duplicate_final_message(message_id),
        );
        let was_streaming = session.has_any_streamed_content();
        let metrics = session.get_streaming_quality_metrics();
        (is_duplicate, was_streaming, metrics)
    }

    fn format_streaming_completion(
        &self,
        text_flush: &str,
        terminal_mode: crate::json_parser::terminal::TerminalMode,
        metrics: &crate::json_parser::health::StreamingQualityMetrics,
    ) -> String {
        let completion = TextDeltaRenderer::render_completion(terminal_mode);
        let show_metrics = (self.verbosity.is_debug() || self.show_streaming_metrics) && metrics.total_deltas > 0;
        if show_metrics { format!("{}{}\n{}", text_flush, completion, metrics.format(self.colors)) }
        else { format!("{text_flush}{completion}") }
    }

    fn compute_flush_for_terminal(
        &self,
        terminal_mode: crate::json_parser::terminal::TerminalMode,
    ) -> String {
        use crate::json_parser::terminal::TerminalMode;
        match terminal_mode {
            TerminalMode::Full => String::new(),
            TerminalMode::Basic | TerminalMode::None => self.flush_non_tty_accumulated_text(terminal_mode),
        }
    }

    fn format_assistant_complete_message(&self, text: &str) -> String {
        let (is_duplicate, was_streaming, metrics) = self.query_message_stop_state();
        let _was_in_block = self.state.with_session_mut(|session| session.on_message_stop());
        let terminal_mode = *self.state.terminal_mode.borrow();
        let text_flush = self.compute_flush_for_terminal(terminal_mode);
        if is_duplicate || was_streaming { return self.format_streaming_completion(&text_flush, terminal_mode, &metrics); }
        let preview = truncate_text(text, self.verbosity.truncate_limit("text"));
        format!("{}[{}]{} {}{}{}\n", self.colors.dim(), &self.display_name, self.colors.reset(), self.colors.white(), preview, self.colors.reset())
    }

    fn format_non_assistant_message(&self, role_str: &str, text: &str) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;
        let preview = truncate_text(text, self.verbosity.truncate_limit("text"));
        format!("{}[{}]{} {}{}:{} {}{}{}\n", c.dim(), prefix, c.reset(), c.blue(), role_str, c.reset(), c.dim(), preview, c.reset())
    }

    /// Format a Message event
    fn format_message_event(
        &self,
        role: Option<String>,
        content: Option<String>,
        delta: Option<bool>,
    ) -> String {
        let role_str = role.unwrap_or_else(|| "unknown".to_string());
        let is_delta = delta.unwrap_or(false);
        let Some(text) = content else { return String::new(); };
        match (is_delta, role_str.as_str()) {
            (true, "assistant") => self.format_assistant_delta_message(&text),
            (false, "assistant") => self.format_assistant_complete_message(&text),
            _ => self.format_non_assistant_message(&role_str, &text),
        }
    }

    fn format_detail_line(base: &str, prefix: &str, preview: &str, c: Colors) -> String {
        format!("{}{}[{}]{} {}  └─ {}{}", base, c.dim(), prefix, c.reset(), c.dim(), preview, c.reset())
    }

    fn format_tool_use_detail(&self, params: &serde_json::Value, out: &str) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;
        let preview = truncate_text(&format_tool_input(params), self.verbosity.truncate_limit("tool_input"));
        if preview.is_empty() { return out.to_string(); }
        Self::format_detail_line(out, prefix, &preview, *c)
    }

    fn format_tool_use_header(&self, tool_name: &str) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;
        format!("{}[{}]{} {}Tool{}: {}{}{}\n", c.dim(), prefix, c.reset(), c.magenta(), c.reset(), c.bold(), tool_name, c.reset())
    }

    fn append_tool_use_detail(&self, out: String, parameters: Option<&serde_json::Value>) -> String {
        if !self.verbosity.show_tool_input() { return out; }
        let Some(params) = parameters else { return out; };
        self.format_tool_use_detail(params, &out)
    }

    /// Format a `ToolUse` event
    fn format_tool_use_event(
        &self,
        tool_name: Option<String>,
        parameters: Option<&serde_json::Value>,
    ) -> String {
        let tool_name = tool_name.unwrap_or_else(|| "unknown".to_string());
        let out = self.format_tool_use_header(&tool_name);
        self.append_tool_use_detail(out, parameters)
    }

    fn format_tool_result_line(&self, is_success: bool) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;
        format!(
            "{}[{}]{} {}{} Tool result{}\n",
            c.dim(),
            prefix,
            c.reset(),
            if is_success { c.green() } else { c.red() },
            if is_success { CHECK } else { CROSS },
            c.reset()
        )
    }

    fn append_tool_result_detail(&self, out: String, output: Option<&String>) -> String {
        if !self.verbosity.is_verbose() { return out; }
        let Some(output_text) = output else { return out; };
        let preview = truncate_text(output_text, self.verbosity.truncate_limit("tool_result"));
        if preview.is_empty() { return out; }
        Self::format_detail_line(&out, &self.display_name, &preview, self.colors)
    }

    /// Format a `ToolResult` event
    fn format_tool_result_event(&self, status: Option<String>, output: Option<&String>) -> String {
        let status_str = status.unwrap_or_else(|| "unknown".to_string());
        let is_success = status_str == "success";
        let out = self.format_tool_result_line(is_success);
        self.append_tool_result_detail(out, output)
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

    fn format_gemini_stats(s: &crate::json_parser::types::GeminiStats) -> String {
        let duration_s = s.duration_ms.unwrap_or(0) / 1000;
        let duration_m = duration_s / 60;
        let duration_s_rem = duration_s % 60;
        let input = s.input_tokens.unwrap_or(0);
        let output = s.output_tokens.unwrap_or(0);
        let tools = s.tool_calls.unwrap_or(0);
        format!("({duration_m}m {duration_s_rem}s, in:{input} out:{output}, {tools} tools)")
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
        let stats_display = event_stats.as_ref().map_or_else(String::new, Self::format_gemini_stats);
        format!("{}[{}]{} {}{} {}{} {}{}{}\n", c.dim(), prefix, c.reset(), if is_success { c.green() } else { c.red() }, if is_success { CHECK } else { CROSS }, status_result, c.reset(), c.dim(), stats_display, c.reset())
    }
}
