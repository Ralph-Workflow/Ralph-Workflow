// Claude event formatting methods.
//
// Contains all the format_*_event methods for the ClaudeParser.

impl ClaudeParser {
    fn hash_string(text: &str) -> u64 {
        crate::json_parser::boundary::compute_hash_str(text)
    }

    /// Format a system event
    fn format_system_event(
        &self,
        subtype: Option<&String>,
        session_id: Option<String>,
        cwd: Option<String>,
    ) -> String {
        if subtype.map(std::string::String::as_str) == Some("init") {
            format_system_init_event(&self.display_name, self.colors, session_id, cwd)
        } else {
            let subtype_str = subtype.map_or("system", |s| s.as_str());
            let terminal_mode = *self.state.terminal_mode.borrow();
            format_system_other_event(&self.display_name, self.colors, subtype_str, terminal_mode)
        }
    }

}

fn format_system_init_event(
    prefix: &str,
    c: Colors,
    session_id: Option<String>,
    cwd: Option<String>,
) -> String {
    let sid = session_id.unwrap_or_else(|| "unknown".to_string());
    let base = format!(
        "{}[{}]{} {}Session started{} {}({:.8}...){}\n",
        c.dim(),
        prefix,
        c.reset(),
        c.cyan(),
        c.reset(),
        c.dim(),
        sid,
        c.reset()
    );
    if let Some(cwd) = cwd {
        let extra = format!(
            "{}[{}]{} {}Working dir: {}{}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.dim(),
            cwd,
            c.reset()
        );
        format!("{base}{extra}")
    } else {
        base
    }
}

fn format_system_other_event(
    prefix: &str,
    c: Colors,
    subtype_str: &str,
    terminal_mode: TerminalMode,
) -> String {
    // In full TTY mode, streaming output uses an in-place update pattern which can leave
    // the cursor positioned on an active line. System events (like `status`) can arrive
    // at any time; clearing the line defensively avoids leaving remnants (e.g. "statusead").
    if terminal_mode == TerminalMode::Full {
        use crate::json_parser::delta_display::CLEAR_LINE;
        format!(
            "{}\r{}[{}]{} {}{}{}\n",
            CLEAR_LINE,
            c.dim(),
            prefix,
            c.reset(),
            c.cyan(),
            subtype_str,
            c.reset()
        )
    } else {
        format!(
            "{}[{}]{} {}{}{}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.cyan(),
            subtype_str,
            c.reset()
        )
    }
}

impl ClaudeParser {
    /// Extract content from assistant message for hash-based deduplication.
    ///
    /// This includes both text and `tool_use` blocks, normalized for comparison.
    /// `Tool_use` blocks are serialized in a deterministic way (name + sorted input JSON)
    /// to ensure semantically identical tool calls produce the same hash.
    ///
    /// # Returns
    /// A tuple of (`normalized_content`, `tool_names_by_index`) where:
    /// - `normalized_content`: The concatenated normalized content (text + `tool_use` markers)
    /// - `tool_names_by_index`: Map from content block index to tool name (for `tool_use` blocks)
    fn extract_text_content_for_hash(
        message: Option<&crate::json_parser::types::AssistantMessage>,
    ) -> Option<(String, std::collections::HashMap<usize, String>)> {
        message?.content.as_ref().map(|content| {
            let (normalized_parts, tool_names): (
                Vec<String>,
                std::collections::HashMap<usize, String>,
            ) = content.iter().enumerate().fold(
                (Vec::new(), std::collections::HashMap::new()),
                |(mut parts, mut names), (index, block)| {
                    match block {
                        ContentBlock::Text { text } => {
                            if let Some(text) = text.as_deref() {
                                parts.push(text.to_string());
                            }
                        }
                        ContentBlock::ToolUse { name, input } => {
                            if let Some(name_str) = name.as_deref() {
                                names.insert(index, name_str.to_string());
                            }

                            let normalized = format!(
                                "TOOL_USE:{}:{}",
                                name.as_deref().unwrap_or(""),
                                input
                                    .as_ref()
                                    .map(|v| {
                                        if v.is_object() {
                                            serde_json::to_string(v).ok()
                                        } else if v.is_string() {
                                            v.as_str().map(std::string::ToString::to_string)
                                        } else {
                                            serde_json::to_string(v).ok()
                                        }
                                        .unwrap_or_default()
                                    })
                                    .unwrap_or_default()
                            );
                            parts.push(normalized);
                        }
                        _ => {}
                    }
                    (parts, names)
                },
            );

            (normalized_parts.join(""), tool_names)
        })
    }

    fn is_duplicate_by_message_id(
        message: Option<&crate::json_parser::types::AssistantMessage>,
        session: &StreamingSession,
    ) -> bool {
        let Some(ast_msg_id) = message.and_then(|m| m.id.as_ref()) else {
            return false;
        };
        session.is_duplicate_final_message(ast_msg_id)
            || (session.get_current_message_id() == Some(ast_msg_id)
                && session.has_any_streamed_content())
    }

    fn is_duplicate_by_content(
        content_for_hash: &Option<(String, std::collections::HashMap<usize, String>)>,
        session: &StreamingSession,
    ) -> bool {
        let Some((ref text, ref tool_names)) = *content_for_hash else {
            return false;
        };
        if text.is_empty() {
            return false;
        }
        session.is_assistant_content_rendered(Self::hash_string(text))
            || session.is_duplicate_by_hash(text, Some(tool_names))
    }

    /// Check if this assistant message is a duplicate of already-streamed content.
    fn is_duplicate_assistant_message(
        &self,
        message: Option<&crate::json_parser::types::AssistantMessage>,
    ) -> bool {
        let session = self.state.streaming_session.borrow();
        if Self::is_duplicate_by_message_id(message, &session) {
            return true;
        }
        let content_for_hash = Self::extract_text_content_for_hash(message);
        if Self::is_duplicate_by_content(&content_for_hash, &session) {
            return true;
        }
        session.has_any_streamed_content()
    }

    /// Format a text content block for assistant output.
    fn format_text_block(&self, out: &mut String, text: &str, prefix: &str, colors: Colors) {
        let limit = self.verbosity.truncate_limit("text");
        let preview = truncate_text(text, limit);
        let _ = writeln!(
            out,
            "{}[{}]{} {}{}{}",
            colors.dim(),
            prefix,
            colors.reset(),
            colors.white(),
            preview,
            colors.reset()
        );
    }

    fn format_tool_input_preview(
        &self,
        out: &mut String,
        input_val: &serde_json::Value,
        prefix: &str,
        colors: Colors,
    ) {
        let input_str = format_tool_input(input_val);
        let limit = self.verbosity.truncate_limit("tool_input");
        let preview = truncate_text(&input_str, limit);
        if !preview.is_empty() {
            let _ = writeln!(
                out,
                "{}[{}]{} {}  └─ {}{}",
                colors.dim(),
                prefix,
                colors.reset(),
                colors.dim(),
                preview,
                colors.reset()
            );
        }
    }

    /// Format a tool use content block for assistant output.
    fn format_tool_use_block(
        &self,
        out: &mut String,
        tool: Option<&String>,
        input: Option<&serde_json::Value>,
        prefix: &str,
        colors: Colors,
    ) {
        let tool_name = tool.cloned().unwrap_or_else(|| "unknown".to_string());
        let _ = writeln!(
            out,
            "{}[{}]{} {}Tool{}: {}{}{}",
            colors.dim(),
            prefix,
            colors.reset(),
            colors.magenta(),
            colors.reset(),
            colors.bold(),
            tool_name,
            colors.reset(),
        );
        self.maybe_append_tool_input_preview(out, input, prefix, colors);
    }

    fn maybe_append_tool_input_preview(
        &self,
        out: &mut String,
        input: Option<&serde_json::Value>,
        prefix: &str,
        colors: Colors,
    ) {
        if let Some(input_val) = input.filter(|_| self.verbosity.show_tool_input()) {
            self.format_tool_input_preview(out, input_val, prefix, colors);
        }
    }

    /// Format a tool result content block for assistant output.
    fn format_tool_result_block(
        &self,
        out: &mut String,
        content: &serde_json::Value,
        prefix: &str,
        colors: Colors,
    ) {
        let content_str = match content {
            serde_json::Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        let limit = self.verbosity.truncate_limit("tool_result");
        let preview = truncate_text(&content_str, limit);
        let _ = writeln!(
            out,
            "{}[{}]{} {}Result:{} {}",
            colors.dim(),
            prefix,
            colors.reset(),
            colors.dim(),
            colors.reset(),
            preview
        );
    }

    /// Format all content blocks from an assistant message.
    fn format_content_blocks(
        &self,
        out: &mut String,
        content: &[ContentBlock],
        prefix: &str,
        colors: Colors,
    ) {
        content.iter().for_each(|block| match block {
            ContentBlock::Text { text } => {
                if let Some(text) = text {
                    self.format_text_block(out, text, prefix, colors);
                }
            }
            ContentBlock::ToolUse { name, input } => {
                self.format_tool_use_block(out, name.as_ref(), input.as_ref(), prefix, colors);
            }
            ContentBlock::ToolResult { content } => {
                if let Some(content) = content {
                    self.format_tool_result_block(out, content, prefix, colors);
                }
            }
            ContentBlock::Unknown => {}
        });
    }

    fn mark_assistant_message_rendered(
        &self,
        message: Option<&crate::json_parser::types::AssistantMessage>,
        msg: &crate::json_parser::types::AssistantMessage,
    ) {
        self.state
            .with_session_mut(|session: &mut StreamingSession| {
                if let Some(ref message_id) = msg.id {
                    session.mark_message_pre_rendered(message_id);
                }
                if let Some((text_content, _)) = Self::extract_text_content_for_hash(message) {
                    if !text_content.is_empty() {
                        let content_hash =
                            crate::json_parser::boundary::compute_hash_str(&text_content);
                        session.mark_assistant_content_rendered(content_hash);
                    }
                }
            });
    }

    fn render_assistant_message(
        &self,
        message: Option<&crate::json_parser::types::AssistantMessage>,
    ) -> String {
        let mut out = String::new();
        let Some(msg) = message else { return out };
        let Some(content) = msg.content.as_ref() else { return out };
        self.format_content_blocks(&mut out, content, &self.display_name, self.colors);
        if !out.is_empty() {
            self.mark_assistant_message_rendered(message, msg);
        }
        out
    }

    /// Format an assistant event
    fn format_assistant_event(
        &self,
        message: Option<&crate::json_parser::types::AssistantMessage>,
    ) -> String {
        // CRITICAL FIX: When ANY content has been streamed via deltas,
        // the Assistant event should NOT display it again.
        // The Assistant event represents the "complete" message, but if we've
        // already shown the streaming deltas, showing it again causes duplication.
        if self.is_duplicate_assistant_message(message) {
            return String::new();
        }
        self.render_assistant_message(message)
    }

    /// Format a user event
    fn format_user_event(&self, message: Option<crate::json_parser::types::UserMessage>) -> String {
        self.extract_user_text(message)
            .map(|preview| {
                let c = &self.colors;
                let prefix = &self.display_name;
                format!(
                    "{}[{}]{} {}User{}: {}{}{}\n",
                    c.dim(),
                    prefix,
                    c.reset(),
                    c.blue(),
                    c.reset(),
                    c.dim(),
                    preview,
                    c.reset()
                )
            })
            .unwrap_or_default()
    }

    fn extract_user_text(
        &self,
        message: Option<crate::json_parser::types::UserMessage>,
    ) -> Option<String> {
        let content = message?.content?;
        if let Some(ContentBlock::Text { text: Some(text) }) = content.first() {
            let limit = self.verbosity.truncate_limit("user");
            Some(truncate_text(text, limit).to_string())
        } else {
            None
        }
    }

    fn format_result_success_line(
        &self,
        duration_m: u64,
        duration_s_rem: u64,
        turns: u32,
        cost: f64,
    ) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;
        format!(
            "{}[{}]{} {}{} Completed{} {}({}m {}s, {} turns, ${:.4}){}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.green(),
            CHECK,
            c.reset(),
            c.dim(),
            duration_m,
            duration_s_rem,
            turns,
            cost,
            c.reset()
        )
    }

    fn format_result_error_line(
        &self,
        subtype: Option<String>,
        error: Option<String>,
        duration_m: u64,
        duration_s_rem: u64,
    ) -> String {
        format_result_error_line_impl(
            &self.display_name,
            self.colors,
            subtype,
            error,
            duration_m,
            duration_s_rem,
        )
    }

    fn append_result_summary(&self, base: &str, summary: &str) -> String {
        let c = &self.colors;
        let limit = self.verbosity.truncate_limit("result");
        let preview = truncate_text(summary, limit);
        format!(
            "{}\n{}Result summary:{}\n{}{}{}",
            base,
            c.bold(),
            c.reset(),
            c.dim(),
            preview,
            c.reset()
        )
    }

    /// Format a result event
    fn format_result_event(
        &self,
        subtype: Option<String>,
        duration_ms: Option<u64>,
        total_cost_usd: Option<f64>,
        num_turns: Option<u32>,
        result: Option<String>,
        error: Option<String>,
    ) -> String {
        let duration_total_secs = duration_ms.unwrap_or(0) / 1000;
        let duration_m = duration_total_secs / 60;
        let duration_s_rem = duration_total_secs % 60;
        let cost = total_cost_usd.unwrap_or(0.0);
        let turns = num_turns.unwrap_or(0);

        let base = if subtype.as_deref() == Some("success") {
            self.format_result_success_line(duration_m, duration_s_rem, turns, cost)
        } else {
            self.format_result_error_line(subtype, error, duration_m, duration_s_rem)
        };

        result
            .as_deref()
            .map_or(base.clone(), |r| self.append_result_summary(&base, r))
    }
}

fn format_result_error_line_impl(
    prefix: &str,
    c: Colors,
    subtype: Option<String>,
    error: Option<String>,
    duration_m: u64,
    duration_s_rem: u64,
) -> String {
    let err = error.unwrap_or_else(|| "unknown error".to_string());
    let subtype_str = subtype.unwrap_or_else(|| "error".to_string());
    format!(
        "{}[{}]{} {}{} {}{}: {} {}({}m {}s){}\n",
        c.dim(),
        prefix,
        c.reset(),
        c.red(),
        CROSS,
        subtype_str,
        c.reset(),
        err,
        c.dim(),
        duration_m,
        duration_s_rem,
        c.reset()
    )
}
