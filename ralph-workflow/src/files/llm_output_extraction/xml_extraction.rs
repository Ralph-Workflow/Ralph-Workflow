//! Flexible XML extraction helpers.
//!
//! Extracts `<ralph-commit>...</ralph-commit>` blocks from common embedding patterns.

use crate::files::llm_output_extraction::cleaning::unescape_json_strings_aggressive;

/// Extract an XML commit message from AI output using multiple strategies.
#[must_use]
pub fn extract_xml_commit(content: &str) -> Option<String> {
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

/// Strategy 1: Extract XML that starts with `<ralph-commit>` tag.
///
/// This is the most efficient strategy for well-formed output where
/// the AI agent output starts directly with the XML.
fn try_extract_direct_xml(content: &str) -> Option<String> {
    let trimmed = content.trim();

    // Check if content starts with the opening tag
    if !trimmed.starts_with("<ralph-commit>") {
        return None;
    }

    // Find the closing tag
    let start = trimmed.find("<ralph-commit>")?;
    let end = trimmed.find("</ralph-commit>")?;

    if start >= end {
        return None;
    }

    // Extract including both tags
    let xml_end = end + "</ralph-commit>".len();
    Some(trimmed[start..xml_end].to_string())
}

/// Strategy 2: Extract XML from markdown code fences.
///
/// Handles:
/// - ```xml ... ```
/// - ``` ... ```
/// - May have leading/trailing whitespace
fn try_extract_from_markdown_fence(content: &str) -> Option<String> {
    // Pattern 1: ```xml fence
    if let Some(start) = content.find("```xml") {
        let after_fence = &content[start + 6..]; // Skip ```xml

        // Find the end of the code fence
        if let Some(end) = after_fence.find("```") {
            let fence_content = after_fence[..end].trim();
            // Look for ralph-commit tags within the fence
            if let Some(xml) = extract_ralph_commit_from_content(fence_content) {
                return Some(xml);
            }
        }
    }

    // Pattern 2: Generic ``` fence (no language specified)
    if let Some(start) = content.find("```") {
        let after_fence = &content[start + 3..]; // Skip ```

        // Find the end of the code fence
        if let Some(end) = after_fence.find("```") {
            let fence_content = after_fence[..end].trim();
            // Look for ralph-commit tags within the fence
            if let Some(xml) = extract_ralph_commit_from_content(fence_content) {
                return Some(xml);
            }
        }
    }

    None
}

/// Strategy 3: Extract XML from JSON strings (escaped).
///
/// Handles:
/// - NDJSON stream with result field containing escaped XML
/// - Direct JSON with escaped XML in string values
fn try_extract_from_json_string(content: &str) -> Option<String> {
    // Pattern 1: NDJSON stream with result field
    // {"type":"result","result":"<ralph-commit>...<\/ralph-commit>"}
    let ndjson_result = content
        .lines()
        .filter_map(|line| {
            let trimmed = line.trim();
            if !trimmed.starts_with('{') {
                return None;
            }

            let json = serde_json::from_str::<serde_json::Value>(trimmed).ok()?;
            json.get("result")
                .and_then(|v| v.as_str())
                .map(String::from)
        })
        .find_map(|result| {
            extract_ralph_commit_from_content(&result).or_else(|| {
                let unescaped = unescape_json_strings_aggressive(&result);
                extract_ralph_commit_from_content(&unescaped)
            })
        });

    if ndjson_result.is_some() {
        return ndjson_result;
    }

    // Also try content/message fields for NDJSON
    let ndjson_fields_result = content
        .lines()
        .filter_map(|line| {
            let trimmed = line.trim();
            if !trimmed.starts_with('{') {
                return None;
            }

            serde_json::from_str::<serde_json::Value>(trimmed).ok()
        })
        .flat_map(|json| {
            ["content", "message", "output", "text"]
                .into_iter()
                .filter_map(move |field_name| {
                    json.get(field_name)
                        .and_then(|v| v.as_str())
                        .map(|field_value| {
                            (
                                String::from(field_value),
                                unescape_json_strings_aggressive(field_value),
                            )
                        })
                })
        })
        .find_map(|(field_value, unescaped)| {
            extract_ralph_commit_from_content(&field_value)
                .or_else(|| extract_ralph_commit_from_content(&unescaped))
        });

    if ndjson_fields_result.is_some() {
        return ndjson_fields_result;
    }

    // Pattern 2: Direct JSON object (not NDJSON)
    let trimmed = content.trim();
    if trimmed.starts_with('{') && trimmed.contains(r#""result""#) {
        if let Ok(json) = serde_json::from_str::<serde_json::Value>(trimmed) {
            if let Some(result) = json.get("result").and_then(|v| v.as_str()) {
                return extract_ralph_commit_from_content(result).or_else(|| {
                    let unescaped = unescape_json_strings_aggressive(result);
                    extract_ralph_commit_from_content(&unescaped)
                });
            }
        }
    }

    None
}

/// Strategy 4: Search for XML tags anywhere in content.
///
/// This is the most permissive strategy - it looks for the tags
/// anywhere in the content, regardless of what comes before or after.
fn try_extract_embedded_xml(content: &str) -> Option<String> {
    extract_ralph_commit_from_content(content)
}

/// Extract `<ralph-commit>...</ralph-commit>` from arbitrary content.
///
/// This helper function searches for the commit tags within any text,
/// handling cases where the AI agent embedded XML in analysis text.
fn extract_ralph_commit_from_content(content: &str) -> Option<String> {
    let start = content.find("<ralph-commit>")?;
    let end = content.find("</ralph-commit>")?;

    if start >= end {
        return None;
    }

    // Extract including both tags
    let xml_end = end + "</ralph-commit>".len();
    let extracted = &content[start..xml_end];

    // Unescape JSON string escape sequences (e.g., \n -> newline)
    let unescaped = unescape_json_strings_aggressive(extracted);

    Some(unescaped)
}

#[cfg(test)]
mod tests {
    use super::*;

    // =========================================================================
    // Strategy 1: Direct XML tests
    // =========================================================================

    #[test]
    fn test_extract_direct_xml_basic() {
        let content = r"<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
</ralph-commit>";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        assert_eq!(result.unwrap(), content);
    }

    #[test]
    fn test_extract_direct_xml_with_whitespace() {
        let content = r"
<ralph-commit>
<ralph-subject>fix: bug</ralph-subject>
</ralph-commit>
  ";
        let expected = r"<ralph-commit>
<ralph-subject>fix: bug</ralph-subject>
</ralph-commit>";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        // Whitespace trimmed but content preserved
        assert!(result.unwrap().trim() == expected.trim());
    }

    #[test]
    fn test_extract_direct_xml_not_at_start_fails() {
        // Content that doesn't start with <ralph-commit>
        // should fail for direct extraction but may succeed with other strategies
        let content = r"Here is the commit:
<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
</ralph-commit>";
        // Should still extract via embedded search (Strategy 4)
        let result = extract_xml_commit(content);
        assert!(result.is_some());
    }

    // =========================================================================
    // Strategy 2: Markdown fence tests
    // =========================================================================

    #[test]
    fn test_extract_from_xml_fence() {
        let content = r"Here's the commit message:

```xml
<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
</ralph-commit>
```

That's it!";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        let extracted = result.unwrap();
        assert!(extracted.contains("<ralph-commit>"));
        assert!(extracted.contains("</ralph-commit>"));
    }

    #[test]
    fn test_extract_from_generic_fence() {
        let content = r"```
<ralph-commit>
<ralph-subject>fix: resolve bug</ralph-subject>
</ralph-commit>
```";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        assert!(result.unwrap().contains("<ralph-commit>"));
    }

    #[test]
    fn test_extract_fence_with_body() {
        let content = r"```xml
<ralph-commit>
<ralph-subject>docs: update readme</ralph-subject>
<ralph-body>Add usage examples.
Update installation instructions.</ralph-body>
</ralph-commit>
```";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        let extracted = result.unwrap();
        assert!(extracted.contains("<ralph-body>"));
        assert!(extracted.contains("usage examples"));
    }

    // =========================================================================
    // Strategy 3: JSON string tests
    // =========================================================================

    #[test]
    fn test_extract_from_ndjson_result() {
        let content = r#"{"type":"result","result":"<ralph-commit>\n<ralph-subject>feat: add</ralph-subject>\n</ralph-commit>"}"#;
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        let extracted = result.unwrap();
        assert!(extracted.contains("<ralph-commit>"));
        assert!(extracted.contains("feat: add"));
    }

    #[test]
    fn test_extract_from_escaped_json() {
        // Double-escaped XML in JSON
        let content = r#"{"type":"result","result":"<ralph-commit>\\n<ralph-subject>fix: bug</ralph-subject>\\n</ralph-commit>"}"#;
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        let extracted = result.unwrap();
        assert!(extracted.contains("<ralph-commit>"));
        assert!(extracted.contains("fix: bug"));
    }

    #[test]
    fn test_extract_from_json_content_field() {
        let content = r#"{"type":"message","content":"<ralph-commit><ralph-subject>chore: update</ralph-subject></ralph-commit>"}"#;
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        assert!(result.unwrap().contains("chore: update"));
    }

    #[test]
    fn test_extract_from_ndjson_stream() {
        let content = r#"{"type":"stream_event","event":"start"}
{"type":"result","result":"<ralph-commit>\n<ralph-subject>test: add coverage</ralph-subject>\n</ralph-commit>"}
{"type":"stream_event","event":"end"}"#;
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        assert!(result.unwrap().contains("test: add coverage"));
    }

    // =========================================================================
    // Strategy 4: Embedded search tests
    // =========================================================================

    #[test]
    fn test_extract_embedded_in_analysis() {
        let content = r"Looking at the diff, I can see the following changes:

The main files modified are parser.rs and extractor.rs.

Based on this analysis, here's the commit message:

<ralph-commit>
<ralph-subject>refactor: simplify parsing logic</ralph-subject>
</ralph-commit>

Let me know if you need any changes!";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        let extracted = result.unwrap();
        assert!(extracted.contains("<ralph-commit>"));
        assert!(extracted.contains("refactor: simplify"));
    }

    #[test]
    fn test_extract_embedded_with_preamble_and_postscript() {
        let content = r"# Analysis

After reviewing the diff...

## Commit Message

<ralph-commit>
<ralph-subject>feat: add new feature</ralph-subject>
<ralph-body>Implementation details here.</ralph-body>
</ralph-commit>

## Summary

This commit adds the requested feature.";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        let extracted = result.unwrap();
        assert!(extracted.contains("<ralph-body>"));
        assert!(extracted.contains("Implementation details"));
    }

    #[test]
    fn test_extract_embedded_multiple_xml_blocks() {
        // Should extract the first (or only) valid block
        let content = r"Some text...

<ralph-commit>
<ralph-subject>first: message</ralph-subject>
</ralph-commit>

More analysis...

<ralph-commit>
<ralph-subject>second: message</ralph-subject>
</ralph-commit>";

        let result = extract_xml_commit(content);
        assert!(result.is_some());
        let extracted = result.unwrap();
        // Should extract the first occurrence
        assert!(extracted.contains("first: message"));
    }

    // =========================================================================
    // Edge cases and error conditions
    // =========================================================================

    #[test]
    fn test_extract_no_xml_returns_none() {
        let content = r"This is just plain text without any XML tags.";
        let result = extract_xml_commit(content);
        assert!(result.is_none());
    }

    #[test]
    fn test_extract_malformed_xml_no_closing_tag() {
        let content = r"<ralph-commit>
<ralph-subject>feat: add</ralph-subject>";
        let result = extract_xml_commit(content);
        // Should return None since closing tag is missing
        assert!(result.is_none());
    }

    #[test]
    fn test_extract_malformed_xml_closing_before_opening() {
        let content = r"</ralph-commit>
<ralph-subject>feat: add</ralph-subject>
<ralph-commit>";
        let result = extract_xml_commit(content);
        assert!(result.is_none());
    }

    #[test]
    fn test_extract_empty_content() {
        let result = extract_xml_commit("");
        assert!(result.is_none());
    }

    #[test]
    fn test_extract_only_opening_tag() {
        let content = r"<ralph-commit>
<ralph-subject>feat: add</ralph-subject>";
        let result = extract_xml_commit(content);
        assert!(result.is_none());
    }

    #[test]
    fn test_extract_nested_fence_and_embedded() {
        // XML in a fence, embedded in analysis text
        let content = r"Based on my analysis:

```xml
<ralph-commit>
<ralph-subject>fix: resolve edge case</ralph-subject>
</ralph-commit>
```

This should be extractable.";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        assert!(result.unwrap().contains("fix: resolve"));
    }

    #[test]
    fn test_extract_detailed_body_format() {
        let content = r"<ralph-commit>
<ralph-subject>feat: add authentication</ralph-subject>
<ralph-body-summary>Implement OAuth2 login flow</ralph-body-summary>
<ralph-body-details>Add support for Google and GitHub providers.
Include session management for tokens.</ralph-body-details>
<ralph-body-footer>Breaks compatibility with v1.0 API</ralph-body-footer>
</ralph-commit>";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        let extracted = result.unwrap();
        assert!(extracted.contains("<ralph-body-summary>"));
        assert!(extracted.contains("<ralph-body-details>"));
        assert!(extracted.contains("<ralph-body-footer>"));
    }

    #[test]
    fn test_extract_with_scope() {
        let content = r"<ralph-commit>
<ralph-subject>fix(parser): handle edge case in parsing</ralph-subject>
</ralph-commit>";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        assert!(result.unwrap().contains("fix(parser):"));
    }

    #[test]
    fn test_extract_with_breaking_change() {
        let content = r"<ralph-commit>
<ralph-subject>feat!: remove deprecated API</ralph-subject>
<ralph-body>BREAKING CHANGE: The old API is no longer supported.</ralph-body>
</ralph-commit>";
        let result = extract_xml_commit(content);
        assert!(result.is_some());
        let extracted = result.unwrap();
        assert!(extracted.contains("feat!:"));
        assert!(extracted.contains("BREAKING CHANGE"));
    }
}
