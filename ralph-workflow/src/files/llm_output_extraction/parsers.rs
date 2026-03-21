//! LLM Output Format Parsers
//!
//! This module contains format-specific parsers for extracting content from
//! various LLM CLI output formats.

use serde_json::Value as JsonValue;

use super::cleaning::remove_thought_process_patterns;
use super::types::OutputFormat;

/// Detect the output format from content analysis
pub fn detect_output_format(content: &str) -> OutputFormat {
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
pub fn extract_by_format(content: &str, format: OutputFormat) -> Option<String> {
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
