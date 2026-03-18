// Claude event formatting methods.
//
// Contains all the format_*_event methods for the ClaudeParser.

impl ClaudeParser {
    fn hash_string(text: &str) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        text.hash(&mut hasher);
        hasher.finish()
    }

    /// Format a system event
    fn format_system_event(
        &self,
        subtype: Option<&String>,
        session_id: Option<String>,
        cwd: Option<String>,
    ) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        if subtype.map(std::string::String::as_str) == Some("init") {
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
        } else {
            let subtype_str = subtype.map_or("system", |s| s.as_str());

            // In full TTY mode, streaming output uses an in-place update pattern which can leave
            // the cursor positioned on an active line. System events (like `status`) can arrive
            // at any time; clearing the line defensively avoids leaving remnants (e.g. "statusead").
            if *self.terminal_mode.borrow() == TerminalMode::Full {
                use crate::json_parser::delta_display::CLEAR_LINE;
                format!(
                    "{CLEAR_LINE}\r{}[{}]{} {}{}{}\n",
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
    }

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
            let mut normalized_parts = Vec::new();
            let mut tool_names = std::collections::HashMap::new();

            for (index, block) in content.iter().enumerate() {
                match block {
                    ContentBlock::Text { text } => {
                        if let Some(text) = text.as_deref() {
                            normalized_parts.push(text.to_string());
                        }
                    }
                    ContentBlock::ToolUse { name, input } => {
                        if let Some(name_str) = name.as_deref() {
                            tool_names.insert(index, name_str.to_string());
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
                        normalized_parts.push(normalized);
                    }
                    _ => {}
                }
            }

            (normalized_parts.join(""), tool_names)
        })
    }

    /// Check if this assistant message is a duplicate of already-streamed content.
    fn is_duplicate_assistant_message(
        &self,
        message: Option<&crate::json_parser::types::AssistantMessage>,
    ) -> bool {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};

        let session = self.streaming_session.borrow();

        // Extract message_id from the assistant message
        let assistant_msg_id = message.and_then(|m| m.id.as_ref());

        // Check if this assistant event has a message_id that matches the current streaming message
        // If it does, and we have streamed content, then this assistant event is a duplicate
        // because the content was already streamed via deltas.
        if let Some(ast_msg_id) = assistant_msg_id {
            // Check if message was already marked as displayed (after message_stop)
            if session.is_duplicate_final_message(ast_msg_id) {
                return true;
            }

            // Check if the assistant message_id matches the current streaming message_id
            if session.get_current_message_id() == Some(ast_msg_id) {
                // Same message - check if we have streamed any content
                // If yes, the assistant event is a duplicate
                if session.has_any_streamed_content() {
                    return true;
                }
            }
        }

        // Check if this exact assistant content has been rendered before
        // This handles the case where GLM sends multiple assistant events during
        // streaming with the same content but different message_ids
        let content_for_hash = Self::extract_text_content_for_hash(message);
        if let Some((ref text_content, _)) = content_for_hash {
            if !text_content.is_empty() {
                // Compute hash of the assistant event content
                let content_hash = Self::hash_string(text_content);

                // Check if this content was already rendered
                if session.is_assistant_content_rendered(content_hash) {
                    return true;
                }
            }
        }

        // If no message_id match, fall back to hash-based deduplication
        // extract_text_content_for_hash now returns (content, tool_names_by_index)
        if let Some((ref text_content, ref tool_names)) = content_for_hash {
            if !text_content.is_empty() {
                return session.is_duplicate_by_hash(text_content, Some(tool_names));
            }
        }

        // Fallback to coarse check
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

        // Show tool input details at Normal and above (not just Verbose)
        // Tool inputs provide crucial context for understanding agent actions
        if self.verbosity.show_tool_input() {
            if let Some(input_val) = input {
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

    /// Format an assistant event
    fn format_assistant_event(
        &self,
        message: Option<&crate::json_parser::types::AssistantMessage>,
    ) -> String {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};

        // CRITICAL FIX: When ANY content has been streamed via deltas,
        // the Assistant event should NOT display it again.
        // The Assistant event represents the "complete" message, but if we've
        // already shown the streaming deltas, showing it again causes duplication.
        if self.is_duplicate_assistant_message(message) {
            return String::new();
        }

        let mut out = String::new();
        if let Some(msg) = message {
            if let Some(content) = msg.content.as_ref() {
                self.format_content_blocks(&mut out, content, &self.display_name, self.colors);

                // If we successfully rendered content, mark it as rendered
                if !out.is_empty() {
                    let mut session = self.streaming_session.borrow_mut();

                    // Mark the message as pre-rendered so that ALL subsequent streaming
                    // deltas for this message are suppressed.
                    // This handles the case where assistant event arrives BEFORE streaming starts.
                    if let Some(ref message_id) = msg.id {
                        session.mark_message_pre_rendered(message_id);
                    }

                    // Mark the assistant content as rendered by hash to prevent duplicate
                    // assistant events with the same content but different message_ids
                    if let Some((text_content, _)) = Self::extract_text_content_for_hash(message) {
                        if !text_content.is_empty() {
                            let mut hasher = DefaultHasher::new();
                            text_content.hash(&mut hasher);
                            let content_hash = hasher.finish();
                            session.mark_assistant_content_rendered(content_hash);
                        }
                    }
                }
            }
        }
        out
    }

    /// Format a user event
    fn format_user_event(&self, message: Option<crate::json_parser::types::UserMessage>) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        if let Some(msg) = message {
            if let Some(content) = msg.content {
                if let Some(ContentBlock::Text { text: Some(text) }) = content.first() {
                    let limit = self.verbosity.truncate_limit("user");
                    let preview = truncate_text(text, limit);
                    return format!(
                        "{}[{}]{} {}User{}: {}{}{}\n",
                        c.dim(),
                        prefix,
                        c.reset(),
                        c.blue(),
                        c.reset(),
                        c.dim(),
                        preview,
                        c.reset()
                    );
                }
            }
        }
        String::new()
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
        let c = &self.colors;
        let prefix = &self.display_name;

        let duration_total_secs = duration_ms.unwrap_or(0) / 1000;
        let duration_m = duration_total_secs / 60;
        let duration_s_rem = duration_total_secs % 60;
        let cost = total_cost_usd.unwrap_or(0.0);
        let turns = num_turns.unwrap_or(0);

        let mut out = if subtype.as_deref() == Some("success") {
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
        } else {
            let err = error.unwrap_or_else(|| "unknown error".to_string());
            format!(
                "{}[{}]{} {}{} {}{}: {} {}({}m {}s){}\n",
                c.dim(),
                prefix,
                c.reset(),
                c.red(),
                CROSS,
                subtype.unwrap_or_else(|| "error".to_string()),
                c.reset(),
                err,
                c.dim(),
                duration_m,
                duration_s_rem,
                c.reset()
            )
        };

        if let Some(result) = result {
            let limit = self.verbosity.truncate_limit("result");
            let preview = truncate_text(&result, limit);
            let _ = writeln!(
                out,
                "\n{}Result summary:{}\n{}{}{}",
                c.bold(),
                c.reset(),
                c.dim(),
                preview,
                c.reset()
            );
        }
        out
    }
}
