// Claude event formatting methods.
//
// Contains all the format_*_event methods for the ClaudeParser.

use crate::common::truncate_text;
use crate::json_parser::delta_display::CLEAR_LINE;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::format_tool_input;
use crate::json_parser::types::AssistantMessage;
use crate::json_parser::types::ContentBlock;
use crate::logger::Colors;
use crate::logger::CHECK;
use crate::logger::CROSS;
use std::hash::DefaultHasher;
use std::hash::Hasher;

use super::ClaudeParser;

impl ClaudeParser {
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
            let mut out = format!(
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
                let _ = writeln!(
                    out,
                    "{}[{}]{} {}Working dir: {}{}",
                    c.dim(),
                    prefix,
                    c.reset(),
                    c.dim(),
                    cwd,
                    c.reset()
                );
            }
            out
        } else {
            let subtype_str = subtype.map_or("system", |s| s.as_str());

            if *self.terminal_mode.borrow() == TerminalMode::Full {
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

    fn extract_text_content_for_hash(
        message: Option<&AssistantMessage>,
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

    fn is_duplicate_assistant_message(&self, message: Option<&AssistantMessage>) -> bool {
        let session = self.streaming_session.borrow();

        let assistant_msg_id = message.and_then(|m| m.id.as_ref());

        if let Some(ast_msg_id) = assistant_msg_id {
            if session.is_duplicate_final_message(ast_msg_id) {
                return true;
            }

            if session.get_current_message_id() == Some(ast_msg_id) {
                if session.has_any_streamed_content() {
                    return true;
                }
            }
        }

        let content_for_hash = Self::extract_text_content_for_hash(message);
        if let Some((ref text_content, _)) = content_for_hash {
            if !text_content.is_empty() {
                let mut hasher = DefaultHasher::new();
                text_content.hash(&mut hasher);
                let content_hash = hasher.finish();

                if session.is_assistant_content_rendered(content_hash) {
                    return true;
                }
            }
        }

        if let Some((ref text_content, ref tool_names)) = content_for_hash {
            if !text_content.is_empty() {
                return session.is_duplicate_by_hash(text_content, Some(tool_names));
            }
        }

        session.has_any_streamed_content()
    }

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

    fn format_content_blocks(
        &self,
        out: &mut String,
        content: &[ContentBlock],
        prefix: &str,
        colors: Colors,
    ) {
        for block in content {
            match block {
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
            }
        }
    }

    fn format_assistant_event(&self, message: Option<&AssistantMessage>) -> String {
        if self.is_duplicate_assistant_message(message) {
            return String::new();
        }

        let mut out = String::new();
        if let Some(msg) = message {
            if let Some(content) = msg.content.as_ref() {
                self.format_content_blocks(&mut out, content, &self.display_name, self.colors);

                if !out.is_empty() {
                    let mut session = self.streaming_session.borrow_mut();

                    if let Some(ref message_id) = msg.id {
                        session.mark_message_pre_rendered(message_id);
                    }

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
