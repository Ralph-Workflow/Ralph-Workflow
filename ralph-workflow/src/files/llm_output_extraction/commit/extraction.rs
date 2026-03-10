//! Commit message extraction from LLM output.
//!
//! Provides `CommitExtractionResult` and `try_extract_xml_commit_with_trace`
//! for extracting commit messages from AI agent XML output.

use super::rendering::render_final_commit_message;
use crate::common::truncate_text;
use crate::files::llm_output_extraction::xml_extraction::extract_xml_commit;
use crate::files::llm_output_extraction::xsd_validation::validate_xml_against_xsd;

/// Result of commit message extraction.
///
/// This struct wraps a successfully extracted commit message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitExtractionResult(String);

impl CommitExtractionResult {
    /// Create a new extraction result with the given message.
    #[must_use]
    pub const fn new(message: String) -> Self {
        Self(message)
    }

    /// Convert into the inner message string with final escape sequence cleanup.
    ///
    /// This applies the final rendering step to ensure no escape sequences leak through
    /// to the actual commit message.
    #[must_use]
    pub fn into_message(self) -> String {
        render_final_commit_message(&self.0)
    }
}

/// Try to extract a commit message from the XML format, with a trace string for debugging.
///
/// This uses flexible XML extraction (direct tags, fenced blocks, escaped JSON strings, embedded
/// text) and validates the resulting XML against the commit XSD.
///
/// Returns: (message, `skip_reason`, files, `trace_detail`)
/// - message: Some(msg) if commit message found
/// - `skip_reason`: Some(reason) if AI determined no commit needed
/// - files: Vec of file paths to selectively commit (empty means commit all)
/// - `trace_detail`: Diagnostic string explaining extraction result
#[must_use]
pub fn try_extract_xml_commit_with_trace(
    content: &str,
) -> (Option<String>, Option<String>, Vec<String>, String) {
    // Try flexible XML extraction that handles various AI embedding patterns.
    // If extraction fails, use the raw content directly - XSD validation will
    // provide a clear error message explaining what's wrong (e.g., missing
    // <ralph-commit> root element) that can be sent back to the AI for retry.
    let (xml_block, extraction_pattern) = extract_xml_commit(content).map_or_else(
        || {
            // No XML tags found - use raw content and let XSD validation
            // produce an informative error for the AI to retry
            (content.to_string(), "raw content (no XML tags found)")
        },
        |xml| {
            // Detect which extraction pattern was used for logging
            let pattern = if content.trim().starts_with("<ralph-commit>") {
                "direct XML"
            } else if content.contains("```xml") || content.contains("```\n<ralph-commit>") {
                "markdown code fence"
            } else if content.contains("{\"result\":") || content.contains("\"result\":") {
                "JSON string"
            } else {
                "embedded search"
            };
            (xml, pattern)
        },
    );

    // Run XSD validation - this will catch both malformed XML and missing elements
    let xsd_result = validate_xml_against_xsd(&xml_block);

    match xsd_result {
        Ok(elements) => {
            // Check for skip first
            if let Some(reason) = elements.skip_reason {
                return (
                    None,
                    Some(reason.clone()),
                    vec![],
                    format!("Found <ralph-skip> via {extraction_pattern}, reason: '{reason}'"),
                );
            }

            let files = elements.files.clone();

            // Format the commit message using parsed elements
            let body = elements.format_body();
            let message = if body.is_empty() {
                elements.subject
            } else {
                format!("{}\n\n{}", elements.subject, body)
            };

            // Determine body presence for logging
            let has_body = message.lines().count() > 1;

            // Use character-based truncation for UTF-8 safety
            let message_preview = {
                let escaped = message.replace('\n', "\\n");
                truncate_text(&escaped, 83) // ~80 chars + "..."
            };

            let files_note = if files.is_empty() {
                String::new()
            } else {
                format!(", files={}", files.len())
            };

            (
                Some(message),
                None,
                files,
                format!(
                    "Found <ralph-commit> via {}, XSD validation passed, body={}{}, message: '{}'",
                    extraction_pattern,
                    if has_body { "present" } else { "absent" },
                    files_note,
                    message_preview
                ),
            )
        }
        Err(e) => {
            // XSD validation failed - return error with details for AI retry
            let error_msg = e.format_for_ai_retry();
            (
                None,
                None,
                vec![],
                format!("XSD validation failed: {error_msg}"),
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // =========================================================================
    // Tests for CommitExtractionResult
    // =========================================================================

    #[test]
    fn test_commit_extraction_result_into_message() {
        let result = CommitExtractionResult::new("feat: add feature".to_string());
        assert_eq!(result.into_message(), "feat: add feature");
    }

    // =========================================================================
    // Tests for XML extraction (try_extract_xml_commit_with_trace)
    // =========================================================================

    #[test]
    fn test_xml_extract_basic_subject_only() {
        // Test basic XML extraction with subject only
        let content = r"<ralph-commit>
<ralph-subject>feat: add new feature</ralph-subject>
</ralph-commit>";
        let (result, skip, _files, reason) = try_extract_xml_commit_with_trace(content);
        assert!(
            result.is_some(),
            "Should extract from basic XML. Reason: {reason}"
        );
        assert!(skip.is_none());
        assert_eq!(result.unwrap(), "feat: add new feature");
    }

    #[test]
    fn test_xml_extract_with_body() {
        // Test XML extraction with subject and body
        let content = r"<ralph-commit>
<ralph-subject>feat(auth): add OAuth2 login flow</ralph-subject>
<ralph-body>Implement Google and GitHub OAuth providers.
Add session management for OAuth tokens.</ralph-body>
</ralph-commit>";
        let (result, skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_some(), "Should extract from XML with body");
        assert!(skip.is_none());
        let msg = result.unwrap();
        assert!(msg.starts_with("feat(auth): add OAuth2 login flow"));
        assert!(msg.contains("Implement Google and GitHub OAuth providers"));
        assert!(msg.contains("Add session management"));
    }

    #[test]
    fn test_xml_extract_with_empty_body() {
        // Test XML extraction with empty body tags
        let content = r"<ralph-commit>
<ralph-subject>fix: resolve bug</ralph-subject>
<ralph-body></ralph-body>
</ralph-commit>";
        let (result, skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_some(), "Should extract even with empty body");
        assert!(skip.is_none());
        // Empty body should be treated as no body
        assert_eq!(result.unwrap(), "fix: resolve bug");
    }

    #[test]
    fn test_xml_extract_ignores_preamble() {
        // Test that content before <ralph-commit> is ignored
        let content = r"Here is the commit message based on my analysis:

Looking at the diff, I can see...

<ralph-commit>
<ralph-subject>refactor: simplify logic</ralph-subject>
</ralph-commit>

That's all!";
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_some(), "Should ignore preamble and extract XML");
        assert_eq!(result.unwrap(), "refactor: simplify logic");
    }

    #[test]
    fn test_xml_extract_fails_missing_tags() {
        // Test that extraction fails when tags are missing
        let content = "Just some text without XML tags";
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_none(), "Should fail when XML tags are missing");
    }

    #[test]
    fn test_xml_extract_fails_invalid_commit_type() {
        // Test that extraction fails for invalid conventional commit types
        let content = r"<ralph-commit>
<ralph-subject>invalid: not a real type</ralph-subject>
</ralph-commit>";
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_none(), "Should reject invalid commit type");
    }

    #[test]
    fn test_xml_extract_fails_missing_subject() {
        // Test that extraction fails when subject is missing
        let content = r"<ralph-commit>
<ralph-body>Just a body, no subject</ralph-body>
</ralph-commit>";
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_none(), "Should fail when subject is missing");
    }

    #[test]
    fn test_xml_extract_fails_empty_subject() {
        // Test that extraction fails when subject is empty
        let content = r"<ralph-commit>
<ralph-subject></ralph-subject>
</ralph-commit>";
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_none(), "Should fail when subject is empty");
    }

    #[test]
    fn test_xml_extract_handles_whitespace_in_subject() {
        // Test that whitespace around subject is trimmed
        let content = r"<ralph-commit>
<ralph-subject>   docs: update readme   </ralph-subject>
</ralph-commit>";
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_some(), "Should handle whitespace in subject");
        assert_eq!(result.unwrap(), "docs: update readme");
    }

    #[test]
    fn test_xml_extract_with_breaking_change() {
        // Test XML extraction with breaking change indicator
        let content = r"<ralph-commit>
<ralph-subject>feat!: drop Python 3.7 support</ralph-subject>
<ralph-body>BREAKING CHANGE: Minimum Python version is now 3.8.</ralph-body>
</ralph-commit>";
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_some(), "Should handle breaking change indicator");
        let msg = result.unwrap();
        assert!(msg.starts_with("feat!:"));
        assert!(msg.contains("BREAKING CHANGE"));
    }

    #[test]
    fn test_xml_extract_with_scope() {
        // Test XML extraction with scope
        let content = r"<ralph-commit>
<ralph-subject>test(parser): add coverage for edge cases</ralph-subject>
</ralph-commit>";
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_some(), "Should handle scope in subject");
        assert_eq!(result.unwrap(), "test(parser): add coverage for edge cases");
    }

    #[test]
    fn test_xml_extract_body_preserves_newlines() {
        // Test that newlines in body are preserved
        let content = r"<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
<ralph-body>Line 1
Line 2
Line 3</ralph-body>
</ralph-commit>";
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_some(), "Should preserve newlines in body");
        let msg = result.unwrap();
        assert!(msg.contains("Line 1\nLine 2\nLine 3"));
    }

    #[test]
    fn test_xml_extract_fails_malformed_tags() {
        // Test that extraction fails for malformed tags (end before start)
        let content = r"</ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
<ralph-commit>";
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(result.is_none(), "Should fail for malformed tags");
    }

    #[test]
    fn test_xml_extract_handles_markdown_code_fence() {
        // Test that XML inside markdown code fence is extracted
        let content = r"```xml
<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
</ralph-commit>
```";
        // The XML extractor looks for tags directly, so this should still work
        // since the tags are present in the content
        let (result, _skip, _files, _) = try_extract_xml_commit_with_trace(content);
        assert!(
            result.is_some(),
            "Should extract from XML even inside code fence"
        );
    }

    #[test]
    fn test_xml_extract_with_thinking_preamble() {
        // Test that thinking preamble is ignored
        let log_content = r"[Claude] Thinking: Looking at this diff, I need to analyze...

<ralph-commit>
<ralph-subject>feat(pipeline): add recovery mechanism</ralph-subject>
<ralph-body>When commit validation fails, attempt to salvage valid message.</ralph-body>
</ralph-commit>";

        let (result, _skip, _files, _reason) = try_extract_xml_commit_with_trace(log_content);
        assert!(result.is_some());
        let msg = result.unwrap();
        assert!(msg.starts_with("feat(pipeline):"));
    }

    // Test that validates XSD functionality using the integrated validation
    #[test]
    fn test_xsd_validation_integrated_in_extraction() {
        // The XSD validation is called within try_extract_xml_commit_with_trace
        // This test ensures that path is exercised
        let xml = r"Some text before
<ralph-commit>
<ralph-subject>fix: resolve bug</ralph-subject>
</ralph-commit>
Some text after";
        let (msg, _skip, _files, trace) = try_extract_xml_commit_with_trace(xml);
        assert!(msg.is_some(), "Should extract valid message");
        // The trace should contain XSD validation result
        assert!(trace.contains("XSD"), "Trace should mention XSD validation");
    }
}
