//! Flexible XML extraction module for AI-generated fix results.
//!
//! This module provides robust extraction of XML fix results from various
//! AI output formats. AI agents may embed XML in unpredictable ways.

use crate::files::llm_output_extraction::cleaning::unescape_json_strings_aggressive;

/// Extract XML fix result from AI output using multiple strategies.
///
/// # Strategies (tried in order)
///
/// 1. **Direct extraction**: Content starts with `<ralph-fix-result>` tag
/// 2. **Markdown code fence**: XML wrapped in ```xml or ``` fences
/// 3. **JSON string**: XML escaped in a JSON string value
/// 4. **Embedded search**: Look for `<ralph-fix-result>` anywhere in content
///
/// # Arguments
///
/// * `content` - The raw AI agent output
///
/// # Returns
///
/// * `Some(xml_content)` - The extracted XML content including tags
/// * `None` - No valid XML fix result found
#[must_use]
pub fn extract_fix_result_xml(content: &str) -> Option<String> {
    // Strategy 1: Direct XML at start (most efficient)
    if let Some(xml) = try_extract_direct_xml(content) {
        return Some(xml);
    }

    // Strategy 2: XML in markdown code fence
    if let Some(xml) = try_extract_from_markdown_fence(content) {
        return Some(xml);
    }

    // Strategy 3: XML in JSON string (escaped)
    if let Some(xml) = try_extract_from_json_string(content) {
        return Some(xml);
    }

    // Strategy 4: Search for tags anywhere (most permissive)
    try_extract_embedded_xml(content)
}

/// Strategy 1: Extract XML that starts with `<ralph-fix-result>` tag.
fn try_extract_direct_xml(content: &str) -> Option<String> {
    let trimmed = content.trim();

    if !trimmed.starts_with("<ralph-fix-result>") {
        return None;
    }

    let start = trimmed.find("<ralph-fix-result>")?;
    let end = trimmed.find("</ralph-fix-result>")?;

    if start >= end {
        return None;
    }

    let xml_end = end + "</ralph-fix-result>".len();
    Some(trimmed[start..xml_end].to_string())
}

/// Strategy 2: Extract XML from markdown code fences.
fn try_extract_from_markdown_fence(content: &str) -> Option<String> {
    // Pattern 1: ```xml fence
    if let Some(start) = content.find("```xml") {
        let after_fence = &content[start + 6..];

        if let Some(end) = after_fence.find("```") {
            let fence_content = after_fence[..end].trim();
            if let Some(xml) = extract_ralph_fix_result_from_content(fence_content) {
                return Some(xml);
            }
        }
    }

    // Pattern 2: Generic ``` fence (no language specified)
    if let Some(start) = content.find("```") {
        let after_fence = &content[start + 3..];

        if let Some(end) = after_fence.find("```") {
            let fence_content = after_fence[..end].trim();
            if let Some(xml) = extract_ralph_fix_result_from_content(fence_content) {
                return Some(xml);
            }
        }
    }

    None
}

/// Strategy 3: Extract XML from JSON strings (escaped).
fn try_extract_from_json_string(content: &str) -> Option<String> {
    // Helper to try extracting from a field value (both raw and unescaped)
    let try_extract_field = |value: &str| {
        extract_ralph_fix_result_from_content(value).or_else(|| {
            let unescaped = unescape_json_strings_aggressive(value);
            extract_ralph_fix_result_from_content(&unescaped)
        })
    };

    // Pattern 1: NDJSON stream - scan lines for JSON objects with XML in any field
    let ndjson_result = content
        .lines()
        .map(str::trim)
        .filter(|line| line.starts_with('{'))
        .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
        .find_map(|json| {
            // Try "result" field first, then other common fields
            ["result", "content", "message", "output", "text"]
                .iter()
                .find_map(|field_name| {
                    json.get(field_name)
                        .and_then(|v| v.as_str())
                        .and_then(try_extract_field)
                })
        });

    if ndjson_result.is_some() {
        return ndjson_result;
    }

    // Pattern 2: Direct JSON object (not NDJSON)
    let trimmed = content.trim();
    if trimmed.starts_with('{') && trimmed.contains(r#""result""#) {
        if let Ok(json) = serde_json::from_str::<serde_json::Value>(trimmed) {
            if let Some(result) = json.get("result").and_then(|v| v.as_str()) {
                return try_extract_field(result);
            }
        }
    }

    None
}

/// Strategy 4: Search for XML tags anywhere in content.
fn try_extract_embedded_xml(content: &str) -> Option<String> {
    extract_ralph_fix_result_from_content(content)
}

/// Extract `<ralph-fix-result>...</ralph-fix-result>` from arbitrary content.
fn extract_ralph_fix_result_from_content(content: &str) -> Option<String> {
    let start = content.find("<ralph-fix-result>")?;
    let end = content.find("</ralph-fix-result>")?;

    if start >= end {
        return None;
    }

    let xml_end = end + "</ralph-fix-result>".len();
    let extracted = &content[start..xml_end];

    // Unescape JSON string escape sequences (e.g., \n -> newline)
    let unescaped = unescape_json_strings_aggressive(extracted);

    Some(unescaped)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_direct_xml_basic() {
        let content = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
</ralph-fix-result>";
        let result = extract_fix_result_xml(content);
        assert!(result.is_some());
        assert_eq!(result.unwrap(), content);
    }

    #[test]
    fn test_extract_from_xml_fence() {
        let content = r"Here's the result:

```xml
<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
</ralph-fix-result>
```

Done!";
        let result = extract_fix_result_xml(content);
        assert!(result.is_some());
        assert!(result.unwrap().contains("<ralph-fix-result>"));
    }

    #[test]
    fn test_extract_from_ndjson_result() {
        let content = r#"{"type":"result","result":"<ralph-fix-result>\n<ralph-status>all_issues_addressed</ralph-status>\n</ralph-fix-result>"}"#;
        let result = extract_fix_result_xml(content);
        assert!(result.is_some());
        assert!(result.unwrap().contains("<ralph-fix-result>"));
    }

    #[test]
    fn test_extract_embedded_in_analysis() {
        let content = r"Based on my fixes:

<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
</ralph-fix-result>

That's all!";
        let result = extract_fix_result_xml(content);
        assert!(result.is_some());
    }

    #[test]
    fn test_extract_no_xml_returns_none() {
        let content = r"This is just plain text without any XML tags.";
        let result = extract_fix_result_xml(content);
        assert!(result.is_none());
    }

    #[test]
    fn test_extract_from_json_content_field() {
        // Test that JSON extraction searches content field
        let content = r#"{"type":"metadata","content":"<ralph-fix-result><ralph-status>all_issues_addressed</ralph-status></ralph-fix-result>"}"#;
        let result = extract_fix_result_xml(content);
        assert!(result.is_some());
        assert!(result
            .unwrap()
            .contains("<ralph-status>all_issues_addressed</ralph-status>"));
    }
}
