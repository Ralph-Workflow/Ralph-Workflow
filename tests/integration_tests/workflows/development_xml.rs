//! Development XML extraction and validation tests.
//!
//! These tests verify the development iteration XML functionality.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (parsing, validation)
//! - Tests are deterministic and isolated
//! - Tests behavior, not implementation details

use ralph_workflow::files::llm_output_extraction::{
    extract_development_result_xml, validate_continuation_development_result_xml,
    validate_development_result_xml,
};

use crate::test_timeout::with_default_timeout;

// ============================================================================
// Development XML Extraction Tests
// ============================================================================

/// Test that valid completed status XML is extracted and validated correctly.
#[test]
fn test_development_xml_valid_completed_status() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Implemented the feature with all tests passing</ralph-summary>
</ralph-development-result>";

        // Test extraction
        let extracted = extract_development_result_xml(xml);
        assert!(
            extracted.is_some(),
            "Should extract valid completed status XML"
        );

        // Test validation
        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(validated.is_ok(), "Should validate completed status XML");

        let elements = validated.unwrap();
        assert_eq!(elements.status, "completed");
        assert_eq!(
            elements.summary,
            "Implemented the feature with all tests passing"
        );
        assert!(elements.is_completed());
        assert!(!elements.is_partial());
        assert!(!elements.is_failed());
    });
}

/// Test that valid partial status XML is extracted and validated correctly.
#[test]
fn test_development_xml_valid_partial_status() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Started implementation, more work remains</ralph-summary>
<ralph-files-changed>- src/main.rs</ralph-files-changed>
<ralph-next-steps>Complete the remaining implementation tasks</ralph-next-steps>
</ralph-development-result>";

        // Test extraction
        let extracted = extract_development_result_xml(xml);
        assert!(
            extracted.is_some(),
            "Should extract valid partial status XML"
        );

        // Test validation
        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(validated.is_ok(), "Should validate partial status XML");

        let elements = validated.unwrap();
        assert_eq!(elements.status, "partial");
        assert!(elements.is_partial());
        assert_eq!(elements.files_changed, Some("- src/main.rs".to_string()));
        assert_eq!(
            elements.next_steps,
            Some("Complete the remaining implementation tasks".to_string())
        );
    });
}

/// Test that continuation extraction/validation keeps ordered recovery steps.
#[test]
fn test_continuation_development_xml_valid_partial_status() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan is not complete because continuation prompt tests still fail.</ralph-summary>
<ralph-next-steps>1. Update the continuation prompt.
2. Re-run prompt and XML tests.
3. Finish the remaining plan and verify the repository.</ralph-next-steps>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some(), "Should extract valid continuation XML");

        let validated = validate_continuation_development_result_xml(&extracted.unwrap());
        assert!(validated.is_ok(), "Should validate continuation XML");

        let elements = validated.unwrap();
        assert!(elements.is_partial());
        assert!(elements.files_changed.is_none());
        assert!(elements
            .next_steps
            .as_ref()
            .is_some_and(|steps| steps.contains("1. Update the continuation prompt.")));
    });
}

/// Test that valid failed status XML is extracted and validated correctly.
#[test]
fn test_development_xml_valid_failed_status() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>Could not complete due to missing dependency</ralph-summary>
</ralph-development-result>";

        // Test extraction
        let extracted = extract_development_result_xml(xml);
        assert!(
            extracted.is_some(),
            "Should extract valid failed status XML"
        );

        // Test validation
        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(validated.is_ok(), "Should validate failed status XML");

        let elements = validated.unwrap();
        assert_eq!(elements.status, "failed");
        assert!(elements.is_failed());
    });
}

/// Test that XML in markdown code fence is extracted correctly.
#[test]
fn test_development_xml_extracted_from_markdown_fence() {
    with_default_timeout(|| {
        let content = r"Here's my status:

```xml
<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>All done</ralph-summary>
</ralph-development-result>
```

That's it!";

        let extracted = extract_development_result_xml(content);
        assert!(
            extracted.is_some(),
            "Should extract XML from markdown fence"
        );

        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(validated.is_ok());
    });
}

/// Test that XML from NDJSON stream is extracted correctly.
#[test]
fn test_development_xml_extracted_from_ndjson() {
    with_default_timeout(|| {
        let content = r#"{"type":"result","result":"<ralph-development-result>\n<ralph-status>completed</ralph-status>\n<ralph-summary>All done</ralph-summary>\n</ralph-development-result>"}"#;

        let extracted = extract_development_result_xml(content);
        assert!(extracted.is_some(), "Should extract XML from NDJSON stream");

        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(validated.is_ok());
    });
}

/// Test that missing required element fails validation with clear error.
#[test]
fn test_development_xml_missing_status_fails_validation() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-summary>Missing status element</ralph-summary>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some(), "Should extract XML even if invalid");

        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(
            validated.is_err(),
            "Should fail validation without status element"
        );

        let error = validated.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error path should mention ralph-status, got: {}",
            error.element_path
        );
        assert!(error.expected.contains("required"));
    });
}

/// Test that missing summary element fails validation with clear error.
#[test]
fn test_development_xml_missing_summary_fails_validation() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some(), "Should extract XML even if invalid");

        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(
            validated.is_err(),
            "Should fail validation without summary element"
        );

        let error = validated.unwrap_err();
        assert!(
            error.element_path.contains("ralph-summary"),
            "Error path should mention ralph-summary, got: {}",
            error.element_path
        );
    });
}

/// Test that invalid status value fails validation with clear error.
#[test]
fn test_development_xml_invalid_status_fails_validation() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>invalid_status</ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some(), "Should extract XML even if invalid");

        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(
            validated.is_err(),
            "Should fail validation with invalid status"
        );

        let error = validated.unwrap_err();
        assert!(error.expected.contains("completed"));
        assert!(error.expected.contains("partial"));
        assert!(error.expected.contains("failed"));
    });
}

/// Test that duplicate elements fail validation with clear error.
#[test]
fn test_development_xml_duplicate_status_fails_validation() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-status>partial</ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some(), "Should extract XML even if invalid");

        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(
            validated.is_err(),
            "Should fail validation with duplicate status"
        );

        let error = validated.unwrap_err();
        assert!(error.expected.contains("only one"));
    });
}

/// Test that unexpected elements are tolerated by the lenient validator.
///
/// The validator now skips unknown child elements rather than rejecting them.
/// Required elements (status, summary) are still enforced.
#[test]
fn test_development_xml_unexpected_element_fails_validation() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Test</ralph-summary>
<ralph-unknown>value</ralph-unknown>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some(), "Should extract XML");

        let validated = validate_development_result_xml(&extracted.unwrap());
        // Tolerant validator skips unknown elements, so this now succeeds
        assert!(
            validated.is_ok(),
            "Tolerant validator should skip unknown elements and succeed: {validated:?}"
        );
        let elements = validated.unwrap();
        assert_eq!(elements.status, "completed");
    });
}

/// Test that empty status fails validation with clear error.
#[test]
fn test_development_xml_empty_status_fails_validation() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>   </ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some(), "Should extract XML even if invalid");

        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(
            validated.is_err(),
            "Should fail validation with empty status"
        );
    });
}

/// Test that empty/whitespace-only summary now succeeds with a fallback (tolerant parsing).
///
/// Previously, an empty summary was rejected. It now uses a fallback string
/// to remain tolerant of LLMs that emit empty summary tags.
#[test]
fn test_development_xml_empty_summary_succeeds_with_fallback() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>   </ralph-summary>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some(), "Should extract the XML");

        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(
            validated.is_ok(),
            "Empty summary should now succeed with a fallback: {:?}",
            validated.err()
        );

        let elements = validated.unwrap();
        assert_eq!(elements.status, "completed");
        assert!(
            !elements.summary.is_empty(),
            "Summary should have a non-empty fallback"
        );
    });
}

/// Test that plain text without XML returns None for extraction.
#[test]
fn test_development_xml_no_xml_returns_none() {
    with_default_timeout(|| {
        let content = "This is just plain text without any XML tags.";

        let extracted = extract_development_result_xml(content);
        assert!(extracted.is_none(), "Should return None when no XML found");
    });
}

/// Test that XSD validation errors are formatted correctly for display.
#[test]
fn test_development_xml_xsd_error_formatting() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml).unwrap();
        let validated = validate_development_result_xml(&extracted);

        assert!(validated.is_err());
        let error = validated.unwrap_err();

        // Verify error structure
        assert!(!error.element_path.is_empty());
        assert!(!error.expected.is_empty());
        assert!(!error.found.is_empty());
        assert!(!error.suggestion.is_empty());

        // Verify error message format contains key information
        let error_msg = format!(
            "{} - expected: {}, found: {}",
            error.element_path, error.expected, error.found
        );
        assert!(error_msg.contains("ralph-summary"));
    });
}

/// Test XML extraction with all optional fields present.
#[test]
fn test_development_xml_with_all_optional_fields() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Implemented full feature</ralph-summary>
<ralph-files-changed>- src/main.rs
- src/utils.rs
- tests/feature_test.rs</ralph-files-changed>
<ralph-next-steps>Run integration tests and deploy</ralph-next-steps>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some());

        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(validated.is_ok());

        let elements = validated.unwrap();
        assert_eq!(elements.status, "completed");
        assert_eq!(elements.summary, "Implemented full feature");
        assert_eq!(
            elements.files_changed,
            Some("- src/main.rs\n- src/utils.rs\n- tests/feature_test.rs".to_string())
        );
        assert_eq!(
            elements.next_steps,
            Some("Run integration tests and deploy".to_string())
        );
    });
}

/// Test that continuation XML tolerates ralph-files-changed (silently discarded).
#[test]
fn test_continuation_development_xml_rejects_bookkeeping_fields() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>The full plan is not complete because formatting still fails.</ralph-summary>
<ralph-files-changed>- src/main.rs
- src/lib.rs</ralph-files-changed>
<ralph-next-steps>1. Fix formatting.
2. Re-run focused validation tests.
3. Finish the remaining plan.</ralph-next-steps>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some(), "Should extract continuation XML");

        let validated = validate_continuation_development_result_xml(&extracted.unwrap());
        assert!(
            validated.is_ok(),
            "Continuation validation should now tolerate ralph-files-changed: {:?}",
            validated.err()
        );

        let elements = validated.unwrap();
        assert!(
            elements.files_changed.is_none(),
            "files_changed should be cleared in continuation mode"
        );
    });
}

/// Test that XML with only required fields is valid.
#[test]
fn test_development_xml_minimal_valid() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done</ralph-summary>
</ralph-development-result>";

        let extracted = extract_development_result_xml(xml);
        assert!(extracted.is_some());

        let validated = validate_development_result_xml(&extracted.unwrap());
        assert!(validated.is_ok());

        let elements = validated.unwrap();
        assert_eq!(elements.status, "completed");
        assert_eq!(elements.summary, "Done");
        assert!(elements.files_changed.is_none());
        assert!(elements.next_steps.is_none());
    });
}

/// Test that file writes work with the XSD files present.
#[test]
fn test_development_xsd_file_exists() {
    with_default_timeout(|| {
        // Verify that the XSD file exists and is readable
        let xsd_content = include_str!(
            "../../../ralph-workflow/src/files/llm_output_extraction/development_result.xsd"
        );
        assert!(xsd_content.contains("ralph-development-result"));
        assert!(xsd_content.contains("ralph-status"));
        assert!(xsd_content.contains("completed"));
        assert!(xsd_content.contains("partial"));
        assert!(xsd_content.contains("failed"));
    });
}
