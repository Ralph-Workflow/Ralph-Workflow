//! Commit message extraction from strict XML documents.

use super::rendering::render_final_commit_message;
use crate::common::truncate_text;
use crate::files::llm_output_extraction::xsd_validation::validate_xml_against_xsd;
use crate::reducer::state::pipeline::ExcludedFile;

fn extract_commit_message(
    elements: crate::files::llm_output_extraction::xsd_validation::CommitMessageElements,
    extraction_pattern: &str,
) -> (
    Option<String>,
    Option<String>,
    Vec<String>,
    Vec<ExcludedFile>,
    String,
) {
    if let Some(reason) = elements.skip_reason {
        return (
            None,
            Some(reason.clone()),
            vec![],
            vec![],
            format!("Found <ralph-skip> via {extraction_pattern}, reason: '{reason}'"),
        );
    }

    let files = elements.files.clone();
    let excluded_files = elements.excluded_files.clone();
    let body = elements.format_body();
    let message = if body.is_empty() {
        elements.subject
    } else {
        format!("{}\n\n{}", elements.subject, body)
    };

    let has_body = message.lines().count() > 1;
    let message_preview = {
        let escaped = message.replace('\n', "\\n");
        truncate_text(&escaped, 83)
    };
    let files_note = if files.is_empty() {
        String::new()
    } else {
        format!(", files={}", files.len())
    };
    let excluded_note = if excluded_files.is_empty() {
        String::new()
    } else {
        format!(", excluded={}", excluded_files.len())
    };

    (
        Some(message),
        None,
        files,
        excluded_files,
        format!(
            "Found <ralph-commit> via {}, XSD validation passed, body={}{}{}, message: '{}'",
            extraction_pattern,
            if has_body { "present" } else { "absent" },
            files_note,
            excluded_note,
            message_preview
        ),
    )
}

/// Result of commit message extraction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitExtractionResult(String);

impl CommitExtractionResult {
    #[must_use]
    pub const fn new(message: String) -> Self {
        Self(message)
    }

    #[must_use]
    pub fn into_message(self) -> String {
        render_final_commit_message(&self.0)
    }
}

/// Extract a commit message from a strict XML document.
#[must_use]
pub fn try_extract_xml_commit_document_with_trace(
    content: &str,
) -> (
    Option<String>,
    Option<String>,
    Vec<String>,
    Vec<ExcludedFile>,
    String,
) {
    match validate_xml_against_xsd(content.trim()) {
        Ok(elements) => extract_commit_message(elements, "strict XML document"),
        Err(e) => {
            let error_msg = e.format_for_ai_retry();
            (
                None,
                None,
                vec![],
                vec![],
                format!("XSD validation failed: {error_msg}"),
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_commit_extraction_result_into_message() {
        let result = CommitExtractionResult::new("feat: add feature".to_string());
        assert_eq!(result.into_message(), "feat: add feature");
    }

    #[test]
    fn test_xml_extract_basic_subject_only() {
        let content = r"<ralph-commit>
<ralph-subject>feat: add new feature</ralph-subject>
</ralph-commit>";
        let (result, skip, _files, _excluded, reason) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(
            result.is_some(),
            "Should extract from basic XML. Reason: {reason}"
        );
        assert!(skip.is_none());
        assert_eq!(result.unwrap(), "feat: add new feature");
    }

    #[test]
    fn test_xml_extract_with_body() {
        let content = r"<ralph-commit>
<ralph-subject>feat(auth): add OAuth2 login flow</ralph-subject>
<ralph-body>Implement Google and GitHub OAuth providers.
Add session management for OAuth tokens.</ralph-body>
</ralph-commit>";
        let (result, skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_some(), "Should extract from XML with body");
        assert!(skip.is_none());
        let msg = result.unwrap();
        assert!(msg.starts_with("feat(auth): add OAuth2 login flow"));
        assert!(msg.contains("Implement Google and GitHub OAuth providers"));
        assert!(msg.contains("Add session management"));
    }

    #[test]
    fn test_xml_extract_with_empty_body() {
        let content = r"<ralph-commit>
<ralph-subject>fix: resolve bug</ralph-subject>
<ralph-body></ralph-body>
</ralph-commit>";
        let (result, skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_some(), "Should extract even with empty body");
        assert!(skip.is_none());
        assert_eq!(result.unwrap(), "fix: resolve bug");
    }

    #[test]
    fn test_xml_extract_fails_missing_tags() {
        let content = "Just some text without XML tags";
        let (result, _skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_none(), "Should fail when XML tags are missing");
    }

    #[test]
    fn test_xml_extract_fails_invalid_commit_type() {
        let content = r"<ralph-commit>
<ralph-subject>invalid: not a real type</ralph-subject>
</ralph-commit>";
        let (result, _skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_none(), "Should reject invalid commit type");
    }

    #[test]
    fn test_xml_extract_fails_missing_subject() {
        let content = r"<ralph-commit>
<ralph-body>Just a body, no subject</ralph-body>
</ralph-commit>";
        let (result, _skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_none(), "Should fail when subject is missing");
    }

    #[test]
    fn test_xml_extract_fails_empty_subject() {
        let content = r"<ralph-commit>
<ralph-subject></ralph-subject>
</ralph-commit>";
        let (result, _skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_none(), "Should fail when subject is empty");
    }

    #[test]
    fn test_xml_extract_handles_whitespace_in_subject() {
        let content = r"<ralph-commit>
<ralph-subject>   docs: update readme   </ralph-subject>
</ralph-commit>";
        let (result, _skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_some(), "Should handle whitespace in subject");
        assert_eq!(result.unwrap(), "docs: update readme");
    }

    #[test]
    fn test_xml_extract_with_breaking_change() {
        let content = r"<ralph-commit>
<ralph-subject>feat!: drop Python 3.7 support</ralph-subject>
<ralph-body>BREAKING CHANGE: Minimum Python version is now 3.8.</ralph-body>
</ralph-commit>";
        let (result, _skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_some(), "Should handle breaking change indicator");
        let msg = result.unwrap();
        assert!(msg.starts_with("feat!:"));
        assert!(msg.contains("BREAKING CHANGE"));
    }

    #[test]
    fn test_xml_extract_with_scope() {
        let content = r"<ralph-commit>
<ralph-subject>test(parser): add coverage for edge cases</ralph-subject>
</ralph-commit>";
        let (result, _skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_some(), "Should handle scope in subject");
        assert_eq!(result.unwrap(), "test(parser): add coverage for edge cases");
    }

    #[test]
    fn test_xml_extract_body_preserves_newlines() {
        let content = r"<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
<ralph-body>Line 1
Line 2
Line 3</ralph-body>
</ralph-commit>";
        let (result, _skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_some(), "Should preserve newlines in body");
        let msg = result.unwrap();
        assert!(msg.contains("Line 1\nLine 2\nLine 3"));
    }

    #[test]
    fn test_xml_extract_fails_malformed_tags() {
        let content = r"</ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
<ralph-commit>";
        let (result, _skip, _files, _excluded, _) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_none(), "Should fail for malformed tags");
    }

    #[test]
    fn test_xsd_validation_integrated_in_extraction() {
        let xml = r"<ralph-commit>
<ralph-subject>fix: resolve bug</ralph-subject>
</ralph-commit>";
        let (msg, _skip, _files, _excluded, trace) =
            try_extract_xml_commit_document_with_trace(xml);
        assert!(msg.is_some(), "Should extract valid message");
        assert!(trace.contains("XSD"), "Trace should mention XSD validation");
    }

    #[test]
    fn test_xml_extract_with_excluded_files_returns_them() {
        let content = r#"<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
<ralph-files>
<ralph-file>src/main.rs</ralph-file>
</ralph-files>
<ralph-excluded-files>
<ralph-excluded-file reason="deferred">src/other.rs</ralph-excluded-file>
</ralph-excluded-files>
</ralph-commit>"#;
        let (result, _skip, files, excluded, _trace) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(result.is_some(), "Should extract commit message");
        assert_eq!(files.len(), 1);
        assert_eq!(excluded.len(), 1);
        assert_eq!(excluded[0].path, "src/other.rs");
        assert!(matches!(
            excluded[0].reason,
            crate::reducer::state::pipeline::ExcludedFileReason::Deferred
        ));
    }

    #[test]
    fn test_xml_extract_without_excluded_files_returns_empty_vec() {
        let content = r"<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
</ralph-commit>";
        let (_result, _skip, _files, excluded, _trace) =
            try_extract_xml_commit_document_with_trace(content);
        assert!(excluded.is_empty(), "Should return empty excluded_files");
    }

    #[test]
    fn test_xml_document_extract_rejects_markdown_wrapped_xml() {
        let content =
            "```xml\n<ralph-commit><ralph-subject>fix: wrapped</ralph-subject></ralph-commit>\n```";

        let (result, skip, _files, _excluded, trace) =
            try_extract_xml_commit_document_with_trace(content);

        assert!(
            result.is_none(),
            "strict extractor should reject wrapped xml"
        );
        assert!(skip.is_none());
        assert!(trace.contains("XSD validation failed"));
    }
}
