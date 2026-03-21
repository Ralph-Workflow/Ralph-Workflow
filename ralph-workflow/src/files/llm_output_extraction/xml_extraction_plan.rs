//! Flexible XML extraction module for AI-generated development plans.
//!
//! This module uses the Strategy pattern to extract XML plans from various
//! AI output formats. Each extraction strategy is encapsulated in its own
//! struct implementing the `XmlExtractionStrategy` trait.

use crate::files::llm_output_extraction::cleaning::unescape_json_strings_aggressive;

/// Strategy trait for XML extraction.
///
/// Each strategy encapsulates a specific extraction algorithm for a particular
/// output format (direct XML, markdown fences, JSON strings, etc.).
trait XmlExtractionStrategy {
    /// Attempt to extract XML from the content.
    ///
    /// Returns `Some(xml)` if extraction succeeds, `None` otherwise.
    fn extract(&self, content: &str) -> Option<String>;
}

/// Extracts XML by searching for plan tags within content.
fn extract_plan_tags(content: &str) -> Option<String> {
    let start = content.find("<ralph-plan>")?;
    let end = content.find("</ralph-plan>")?;

    if start >= end {
        return None;
    }

    let xml_end = end + "</ralph-plan>".len();
    let extracted = &content[start..xml_end];

    // Unescape JSON string escape sequences (e.g., \n -> newline)
    let unescaped = unescape_json_strings_aggressive(extracted);

    Some(unescaped)
}

/// Strategy for direct XML extraction when content starts with `<ralph-plan>`.
struct DirectXmlStrategy;

impl XmlExtractionStrategy for DirectXmlStrategy {
    fn extract(&self, content: &str) -> Option<String> {
        let trimmed = content.trim();
        if trimmed.starts_with("<ralph-plan>") {
            extract_plan_tags(trimmed)
        } else {
            None
        }
    }
}

/// Strategy for extracting XML from markdown code fences.
struct MarkdownFenceStrategy;

impl MarkdownFenceStrategy {
    fn extract_from_fence(content: &str, fence_marker: &str) -> Option<String> {
        let start = content.find(fence_marker)?;
        let after_fence = &content[start + fence_marker.len()..];
        let end = after_fence.find("```")?;
        let fence_content = after_fence[..end].trim();
        extract_plan_tags(fence_content)
    }
}

impl XmlExtractionStrategy for MarkdownFenceStrategy {
    fn extract(&self, content: &str) -> Option<String> {
        // Try ```xml fence first, then generic ``` fence
        Self::extract_from_fence(content, "```xml")
            .or_else(|| Self::extract_from_fence(content, "```"))
    }
}

/// Strategy for extracting XML from `OpenCode` NDJSON streams.
///
/// `OpenCode` outputs text in multiple `{"type":"text","part":{"text":"..."}}` events.
/// This strategy accumulates all text fragments and extracts XML from the result.
struct OpenCodeStrategy;

impl OpenCodeStrategy {
    fn accumulate_text(content: &str) -> String {
        content
            .lines()
            .map(str::trim)
            .filter(|line| line.starts_with('{'))
            .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
            .filter(|json| json.get("type").and_then(|v| v.as_str()) == Some("text"))
            .filter_map(|json| {
                json.get("part")
                    .and_then(|p| p.get("text"))
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
            })
            .collect()
    }
}

impl XmlExtractionStrategy for OpenCodeStrategy {
    fn extract(&self, content: &str) -> Option<String> {
        let accumulated = Self::accumulate_text(content);
        if accumulated.is_empty() {
            return None;
        }
        extract_plan_tags(&accumulated)
    }
}

/// Strategy for extracting XML from Claude/Codex JSON result fields.
///
/// These formats use `{"type":"result","result":"..."}` or similar flat structures.
struct JsonResultStrategy;

impl JsonResultStrategy {
    fn try_extract_from_value(value: &str) -> Option<String> {
        extract_plan_tags(value)
            .or_else(|| extract_plan_tags(&unescape_json_strings_aggressive(value)))
    }
}

impl XmlExtractionStrategy for JsonResultStrategy {
    fn extract(&self, content: &str) -> Option<String> {
        content
            .lines()
            .map(str::trim)
            .filter(|line| line.starts_with('{'))
            .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
            .find_map(|json| {
                ["result", "content", "message", "output", "text"]
                    .iter()
                    .find_map(|field| {
                        json.get(field)
                            .and_then(|v| v.as_str())
                            .and_then(Self::try_extract_from_value)
                    })
            })
    }
}

/// Strategy for embedded XML search (most permissive fallback).
///
/// Searches for `<ralph-plan>` tags anywhere in the content.
struct EmbeddedXmlStrategy;

impl XmlExtractionStrategy for EmbeddedXmlStrategy {
    fn extract(&self, content: &str) -> Option<String> {
        extract_plan_tags(content)
    }
}

/// Extract XML plan from AI output using a chain of extraction strategies.
///
/// Strategies are tried in order from most specific to most permissive:
///
/// 1. **Direct XML**: Content starts with `<ralph-plan>` tag
/// 2. **Markdown fence**: XML wrapped in ```xml or ``` fences
/// 3. **`OpenCode` NDJSON**: Accumulated text from `{"type":"text","part":{"text":"..."}}` events
/// 4. **JSON result**: XML in `result`, `content`, `message`, `output`, or `text` fields
/// 5. **Embedded search**: Look for `<ralph-plan>` anywhere in content
///
/// # Arguments
///
/// * `content` - The raw AI agent output
///
/// # Returns
///
/// * `Some(xml_content)` - The extracted XML content including tags
/// * `None` - No valid XML plan found
#[must_use]
pub fn extract_plan_xml(content: &str) -> Option<String> {
    let strategies: &[&dyn XmlExtractionStrategy] = &[
        &DirectXmlStrategy,
        &MarkdownFenceStrategy,
        &OpenCodeStrategy,
        &JsonResultStrategy,
        &EmbeddedXmlStrategy,
    ];

    for strategy in strategies {
        if let Some(xml) = strategy.extract(content) {
            return Some(xml);
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_direct_xml_strategy() {
        let content = "<ralph-plan>
<ralph-summary>Summary</ralph-summary>
<ralph-implementation-steps>1. Step</ralph-implementation-steps>
</ralph-plan>";
        let result = extract_plan_xml(content);
        assert!(result.is_some());
        assert_eq!(result.unwrap(), content);
    }

    #[test]
    fn test_markdown_fence_strategy() {
        let content = r"Here's the plan:

```xml
<ralph-plan>
<ralph-summary>Summary</ralph-summary>
<ralph-implementation-steps>1. Step</ralph-implementation-steps>
</ralph-plan>
```

Done!";
        let result = extract_plan_xml(content);
        assert!(result.is_some());
        assert!(result.unwrap().contains("<ralph-plan>"));
    }

    #[test]
    fn test_opencode_strategy_multiple_events() {
        let content = r#"{"type":"step_start","timestamp":1234567890,"sessionID":"test","part":{"id":"1"}}
{"type":"text","timestamp":1234567891,"sessionID":"test","part":{"text":"<ralph-plan>"}}
{"type":"text","timestamp":1234567892,"sessionID":"test","part":{"text":"\n<ralph-summary>Summary from OpenCode</ralph-summary>"}}
{"type":"text","timestamp":1234567893,"sessionID":"test","part":{"text":"\n<ralph-implementation-steps>1. First step</ralph-implementation-steps>"}}
{"type":"text","timestamp":1234567894,"sessionID":"test","part":{"text":"\n</ralph-plan>"}}
{"type":"step_finish","timestamp":1234567895,"sessionID":"test","part":{"reason":"end_turn"}}"#;
        let result = extract_plan_xml(content);
        assert!(result.is_some());
        let xml = result.unwrap();
        assert!(xml.contains("<ralph-plan>"));
        assert!(xml.contains("<ralph-summary>Summary from OpenCode</ralph-summary>"));
        assert!(
            xml.contains("<ralph-implementation-steps>1. First step</ralph-implementation-steps>")
        );
        assert!(xml.contains("</ralph-plan>"));
    }

    #[test]
    fn test_opencode_strategy_single_event() {
        let content = r#"{"type":"text","timestamp":1234567891,"sessionID":"test","part":{"text":"<ralph-plan>\n<ralph-summary>Summary</ralph-summary>\n<ralph-implementation-steps>1. Step</ralph-implementation-steps>\n</ralph-plan>"}}"#;
        let result = extract_plan_xml(content);
        assert!(result.is_some());
        let xml = result.unwrap();
        assert!(xml.contains("<ralph-plan>"));
        assert!(xml.contains("</ralph-plan>"));
    }

    #[test]
    fn test_json_result_strategy() {
        let content = r#"{"type":"result","result":"<ralph-plan>\n<ralph-summary>Summary</ralph-summary>\n<ralph-implementation-steps>1. Step</ralph-implementation-steps>\n</ralph-plan>"}"#;
        let result = extract_plan_xml(content);
        assert!(result.is_some());
        assert!(result.unwrap().contains("<ralph-plan>"));
    }

    #[test]
    fn test_embedded_xml_strategy() {
        let content = r"Based on my analysis:

<ralph-plan>
<ralph-summary>Summary</ralph-summary>
<ralph-implementation-steps>1. Step</ralph-implementation-steps>
</ralph-plan>

That's the plan!";
        let result = extract_plan_xml(content);
        assert!(result.is_some());
    }

    #[test]
    fn test_no_xml_returns_none() {
        let content = "This is just plain text without any XML tags.";
        let result = extract_plan_xml(content);
        assert!(result.is_none());
    }

    #[test]
    fn test_opencode_accumulate_ignores_non_json_and_non_text_events() {
        // This test validates that accumulate_text uses functional iteration
        // and correctly filters only text events while ignoring malformed JSON
        let content = r#"not json at all
{"type":"step_start","part":{}}
{"type":"text","part":{"text":"<ralph-plan>"}}
random garbage line
{"type":"text","part":{"text":"</ralph-plan>"}}"#;

        let accumulated = OpenCodeStrategy::accumulate_text(content);
        assert_eq!(accumulated, "<ralph-plan></ralph-plan>");
    }

    #[test]
    fn test_json_result_strategy_searches_multiple_fields() {
        // Test that JsonResultStrategy scans multiple field names and returns first match
        let content = r#"{"type":"metadata","content":"not xml"}
{"type":"result","result":"<ralph-plan><ralph-summary>Found in result</ralph-summary></ralph-plan>"}"#;
        let result = extract_plan_xml(content);
        assert!(result.is_some());
        assert!(result.unwrap().contains("Found in result"));
    }
}
