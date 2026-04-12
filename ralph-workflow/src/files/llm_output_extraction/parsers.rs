//! LLM Output Format Parsers
//!
//! This module contains format-specific parsers for extracting content from
//! various LLM CLI output formats.

use serde_json::Value as JsonValue;

use super::cleaning::remove_thought_process_patterns;
use super::types::OutputFormat;

/// Detect the output format from content analysis
pub(super) fn detect_output_format(content: &str) -> OutputFormat {
    // Check if it looks like JSONL
    let first_line = content.lines().next().unwrap_or("");
    if !first_line.trim().starts_with('{') {
        return OutputFormat::Generic;
    }

    // Try to parse first JSON line and detect format
    if let Ok(json) = serde_json::from_str::<JsonValue>(first_line) {
        if let Some(type_field) = json.get("type").and_then(|v| v.as_str()) {
            return match type_field {
                // Claude format indicators
                "system" | "assistant" | "user" | "result" => {
                    // Check for Claude-specific subtype
                    if json.get("subtype").is_some() || json.get("session_id").is_some() {
                        OutputFormat::Claude
                    } else if json.get("event_type").is_some() {
                        OutputFormat::OpenCode
                    } else {
                        OutputFormat::Claude
                    }
                }
                // Codex format indicators
                "thread.started" | "turn.started" | "turn.completed" | "turn.failed"
                | "item.started" | "item.completed" => OutputFormat::Codex,
                // Gemini format indicators
                "init" | "message" => {
                    if json.get("model").is_some() || json.get("role").is_some() {
                        OutputFormat::Gemini
                    } else {
                        OutputFormat::Claude
                    }
                }
                // OpenCode format indicators
                "step_start" | "step_finish" | "tool_use" | "text" => {
                    if json.get("sessionID").is_some() || json.get("part").is_some() {
                        OutputFormat::OpenCode
                    } else {
                        OutputFormat::Generic
                    }
                }
                _ => OutputFormat::Generic,
            };
        }
    }

    OutputFormat::Generic
}

/// Extract content using the specified format's strategy
pub(super) fn extract_by_format(content: &str, format: OutputFormat) -> Option<String> {
    match format {
        OutputFormat::Claude => extract_claude_result(content),
        OutputFormat::Codex => extract_codex_result(content),
        OutputFormat::Gemini => extract_gemini_result(content),
        OutputFormat::OpenCode => extract_opencode_result(content),
        OutputFormat::Generic => None, // Generic doesn't use JSON extraction
    }
}

/// Extract result from Claude CLI NDJSON output.
///
/// Claude outputs JSONL with various event types. The result is in:
/// - `{"type": "result", "result": "..."}` - primary result event
/// - `{"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}}` - assistant messages
fn extract_claude_result(content: &str) -> Option<String> {
    let mut last_result: Option<String> = None;
    let mut last_assistant_text: Option<String> = None;

    for line in content.lines() {
        let line = line.trim();
        if !line.starts_with('{') {
            continue;
        }

        if let Ok(json) = serde_json::from_str::<JsonValue>(line) {
            if let Some(type_field) = json.get("type").and_then(|v| v.as_str()) {
                match type_field {
                    "result" => {
                        // Primary result event - highest priority
                        if let Some(result) = json.get("result").and_then(|v| v.as_str()) {
                            if !result.trim().is_empty() {
                                // Apply thought process filtering to result field content
                                let filtered = remove_thought_process_patterns(result);
                                last_result = Some(filtered);
                            }
                        }
                    }
                    "assistant" => {
                        // Extract text from assistant message content blocks
                        if let Some(message) = json.get("message") {
                            if let Some(content_arr) =
                                message.get("content").and_then(|v| v.as_array())
                            {
                                for block in content_arr {
                                    let block_type = block.get("type").and_then(|v| v.as_str());
                                    // Skip thinking/reasoning blocks - only extract text content
                                    if block_type == Some("thinking")
                                        || block_type == Some("reasoning")
                                    {
                                        continue;
                                    }
                                    if block_type == Some("text") {
                                        if let Some(text) =
                                            block.get("text").and_then(|v| v.as_str())
                                        {
                                            if !text.trim().is_empty() {
                                                last_assistant_text = Some(text.to_string());
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
            // Also check for simple {"result": "..."} format (legacy/other agents)
            else if let Some(result) = json.get("result").and_then(|v| v.as_str()) {
                if !result.trim().is_empty() {
                    // Apply thought process filtering to result field content
                    let filtered = remove_thought_process_patterns(result);
                    last_result = Some(filtered);
                }
            }
        }
    }

    // Prefer explicit result event, fall back to last assistant text
    last_result.or(last_assistant_text)
}

/// Extract agent message text from a Codex item.completed event JSON.
///
/// Returns `Some(text)` if the JSON contains a valid agent message with non-empty text.
fn extract_codex_message_text(json: &JsonValue) -> Option<&str> {
    let type_field = json.get("type")?.as_str()?;
    if type_field != "item.completed" {
        return None;
    }

    let item = json.get("item")?;
    if item.get("type")?.as_str()? != "agent_message" {
        return None;
    }

    let text = item.get("text")?.as_str()?;
    if text.trim().is_empty() {
        return None;
    }

    Some(text)
}

/// Extract result from Codex CLI NDJSON output.
///
/// Codex outputs JSONL with item events. The result comes from:
/// - `{"type": "item.completed", "item": {"type": "agent_message", "text": "..."}}`
fn extract_codex_result(content: &str) -> Option<String> {
    let mut last_message: Option<String> = None;

    for line in content.lines() {
        let line = line.trim();
        if !line.starts_with('{') {
            continue;
        }

        let Ok(json) = serde_json::from_str::<JsonValue>(line) else {
            continue;
        };

        if let Some(text) = extract_codex_message_text(&json) {
            let filtered = remove_thought_process_patterns(text);
            last_message = Some(filtered);
        }
    }

    last_message
}

/// Extract result from Gemini CLI NDJSON output.
///
/// Gemini outputs JSONL with message events. The result comes from:
/// - `{"type": "message", "role": "assistant", "content": "..."}`
/// - `{"type": "result", ...}` may contain final stats but not the actual output
fn extract_gemini_result(content: &str) -> Option<String> {
    let mut last_assistant_content: Option<String> = None;

    for line in content.lines() {
        let line = line.trim();
        if !line.starts_with('{') {
            continue;
        }

        if let Ok(json) = serde_json::from_str::<JsonValue>(line) {
            let is_assistant_message = json.get("type").and_then(|v| v.as_str()) == Some("message")
                && json.get("role").and_then(|v| v.as_str()) == Some("assistant");

            if is_assistant_message {
                if let Some(msg_content) = json.get("content").and_then(|v| v.as_str()) {
                    if !msg_content.trim().is_empty() {
                        // For streaming, accumulate or replace based on delta flag
                        if json.get("delta").and_then(serde_json::Value::as_bool) == Some(true) {
                            // Delta message - accumulate
                            if let Some(ref mut existing) = last_assistant_content {
                                existing.push_str(msg_content);
                            } else {
                                last_assistant_content = Some(msg_content.to_string());
                            }
                        } else {
                            // Full message - replace
                            last_assistant_content = Some(msg_content.to_string());
                        }
                    }
                }
            }
        }
    }

    // Apply thought process filtering to the final accumulated content
    last_assistant_content.map(|content| remove_thought_process_patterns(&content))
}

/// Extract text from an `OpenCode` text event JSON.
///
/// Returns `Some(text)` if the JSON contains a valid text part with non-empty content.
fn extract_opencode_text_part(json: &JsonValue) -> Option<&str> {
    let type_field = json.get("type")?.as_str()?;
    if type_field != "text" {
        return None;
    }

    let part = json.get("part")?;
    let text = part.get("text")?.as_str()?;
    if text.trim().is_empty() {
        return None;
    }

    Some(text)
}

/// Extract result from `OpenCode` CLI NDJSON output.
///
/// `OpenCode` outputs JSONL with nested part structures. The result comes from:
/// - `{"type": "text", "part": {"text": "..."}}`
fn extract_opencode_result(content: &str) -> Option<String> {
    let accumulated_text = content
        .lines()
        .map(str::trim)
        .filter(|line| line.starts_with('{'))
        .filter_map(|line| serde_json::from_str::<JsonValue>(line).ok())
        .filter_map(|json| extract_opencode_text_part(&json).map(|text| text.trim().to_string()))
        .collect::<Vec<_>>()
        .join(" ");

    // Apply thought process filtering to the accumulated text
    if accumulated_text.is_empty() {
        None
    } else {
        Some(remove_thought_process_patterns(&accumulated_text))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::files::llm_output_extraction::types::OutputFormat;

    // --- detect_output_format ---

    #[test]
    fn detect_output_format_empty_is_generic() {
        assert_eq!(detect_output_format(""), OutputFormat::Generic);
    }

    #[test]
    fn detect_output_format_plain_text_is_generic() {
        assert_eq!(
            detect_output_format("Hello world, no JSON here."),
            OutputFormat::Generic
        );
    }

    #[test]
    fn detect_output_format_claude_result_event() {
        let line = r#"{"type":"result","result":"done","session_id":"abc"}"#;
        assert_eq!(detect_output_format(line), OutputFormat::Claude);
    }

    #[test]
    fn detect_output_format_codex_turn_started() {
        let line = r#"{"type":"turn.started","id":"t1"}"#;
        assert_eq!(detect_output_format(line), OutputFormat::Codex);
    }

    #[test]
    fn detect_output_format_codex_item_completed() {
        let line = r#"{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}"#;
        assert_eq!(detect_output_format(line), OutputFormat::Codex);
    }

    #[test]
    fn detect_output_format_opencode_text_with_session_id() {
        let line = r#"{"type":"text","part":{"text":"hi"},"sessionID":"s1"}"#;
        assert_eq!(detect_output_format(line), OutputFormat::OpenCode);
    }

    // --- extract_by_format ---

    #[test]
    fn extract_by_format_generic_returns_none() {
        let result = extract_by_format("some plain text", OutputFormat::Generic);
        assert_eq!(result, None);
    }

    #[test]
    fn extract_by_format_claude_extracts_result() {
        let content = r#"{"type":"result","result":"final answer","subtype":"success"}"#;
        let result = extract_by_format(content, OutputFormat::Claude);
        assert_eq!(result, Some("final answer".to_string()));
    }

    #[test]
    fn extract_by_format_codex_extracts_agent_message() {
        let content =
            r#"{"type":"item.completed","item":{"type":"agent_message","text":"codex answer"}}"#;
        let result = extract_by_format(content, OutputFormat::Codex);
        assert_eq!(result, Some("codex answer".to_string()));
    }

    #[test]
    fn extract_by_format_opencode_extracts_text_part() {
        let content = r#"{"type":"text","part":{"text":"opencode output"}}"#;
        let result = extract_by_format(content, OutputFormat::OpenCode);
        assert_eq!(result, Some("opencode output".to_string()));
    }

    // --- extract_opencode_text_part (private helper via extract_by_format) ---

    #[test]
    fn extract_by_format_opencode_skips_non_text_events() {
        let content = concat!(
            r#"{"type":"step_start","part":{"text":"ignored"}}"#,
            "\n",
            r#"{"type":"text","part":{"text":"real output"}}"#,
        );
        let result = extract_by_format(content, OutputFormat::OpenCode);
        assert_eq!(result, Some("real output".to_string()));
    }

    #[test]
    fn extract_by_format_opencode_empty_text_returns_none() {
        let content = r#"{"type":"text","part":{"text":"   "}}"#;
        let result = extract_by_format(content, OutputFormat::OpenCode);
        assert_eq!(result, None);
    }

    #[test]
    fn extract_by_format_opencode_joins_multiple_text_parts() {
        let content = concat!(
            r#"{"type":"text","part":{"text":"first"}}"#,
            "\n",
            r#"{"type":"text","part":{"text":"second"}}"#,
        );
        let result = extract_by_format(content, OutputFormat::OpenCode);
        assert_eq!(result, Some("first second".to_string()));
    }

    // --- extract_codex_message_text (private helper via extract_by_format) ---

    #[test]
    fn extract_by_format_codex_skips_non_item_completed() {
        let content = concat!(
            r#"{"type":"turn.started"}"#,
            "\n",
            r#"{"type":"item.completed","item":{"type":"agent_message","text":"the answer"}}"#,
        );
        let result = extract_by_format(content, OutputFormat::Codex);
        assert_eq!(result, Some("the answer".to_string()));
    }

    #[test]
    fn extract_by_format_codex_skips_non_agent_message_items() {
        let content = r#"{"type":"item.completed","item":{"type":"tool_call","text":"ignored"}}"#;
        let result = extract_by_format(content, OutputFormat::Codex);
        assert_eq!(result, None);
    }

    // --- Claude extraction ---

    #[test]
    fn extract_by_format_claude_prefers_result_over_assistant() {
        let content = concat!(
            r#"{"type":"assistant","message":{"content":[{"type":"text","text":"assistant text"}]}}"#,
            "\n",
            r#"{"type":"result","result":"final result","subtype":"success"}"#,
        );
        let result = extract_by_format(content, OutputFormat::Claude);
        assert_eq!(result, Some("final result".to_string()));
    }

    #[test]
    fn extract_by_format_claude_falls_back_to_assistant_text() {
        let content = r#"{"type":"assistant","message":{"content":[{"type":"text","text":"assistant answer"}]}}"#;
        let result = extract_by_format(content, OutputFormat::Claude);
        assert_eq!(result, Some("assistant answer".to_string()));
    }

    #[test]
    fn extract_by_format_claude_skips_thinking_blocks() {
        let content = r#"{"type":"assistant","message":{"content":[{"type":"thinking","text":"internal reasoning"},{"type":"text","text":"visible answer"}]}}"#;
        let result = extract_by_format(content, OutputFormat::Claude);
        assert_eq!(result, Some("visible answer".to_string()));
    }

    // --- Gemini extraction ---

    #[test]
    fn extract_by_format_gemini_extracts_assistant_content() {
        let content =
            r#"{"type":"message","role":"assistant","model":"gemini","content":"gemini answer"}"#;
        let result = extract_by_format(content, OutputFormat::Gemini);
        assert_eq!(result, Some("gemini answer".to_string()));
    }

    #[test]
    fn extract_by_format_gemini_skips_non_assistant_messages() {
        let content =
            r#"{"type":"message","role":"user","model":"gemini","content":"user question"}"#;
        let result = extract_by_format(content, OutputFormat::Gemini);
        assert_eq!(result, None);
    }
}

#[cfg(test)]
mod proptest_parsers {
    use super::{detect_output_format, extract_by_format};
    use crate::files::llm_output_extraction::types::OutputFormat;
    use proptest::prelude::*;

    proptest! {
        /// `detect_output_format` must never panic on any string input.
        #[test]
        fn detect_output_format_never_panics(s in ".*") {
            let _ = detect_output_format(&s);
        }

        /// `extract_by_format` with Claude format must never panic on any string input.
        #[test]
        fn extract_by_format_claude_never_panics(s in ".*") {
            let _ = extract_by_format(&s, OutputFormat::Claude);
        }

        /// `extract_by_format` with Codex format must never panic on any string input.
        #[test]
        fn extract_by_format_codex_never_panics(s in ".*") {
            let _ = extract_by_format(&s, OutputFormat::Codex);
        }

        /// `extract_by_format` with Gemini format must never panic on any string input.
        #[test]
        fn extract_by_format_gemini_never_panics(s in ".*") {
            let _ = extract_by_format(&s, OutputFormat::Gemini);
        }

        /// `extract_by_format` with OpenCode format must never panic on any string input.
        #[test]
        fn extract_by_format_opencode_never_panics(s in ".*") {
            let _ = extract_by_format(&s, OutputFormat::OpenCode);
        }

        /// `extract_by_format` with Generic format must never panic and always returns None.
        #[test]
        fn extract_by_format_generic_always_none(s in ".*") {
            let result = extract_by_format(&s, OutputFormat::Generic);
            prop_assert_eq!(result, None);
        }

        /// Plain text (no leading `{`) always detects as Generic.
        #[test]
        fn detect_output_format_plain_text_is_generic(s in "[^{].*") {
            prop_assert_eq!(detect_output_format(&s), OutputFormat::Generic);
        }
    }
}
