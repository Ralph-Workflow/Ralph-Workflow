//! Integration tests for fix agent XML validation.
//!
//! This module tests the XML extraction and XSD validation behavior for
//! the fix agent's output during the review-fix cycle.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All integration tests MUST follow the style guide defined in
//! **[`INTEGRATION_TESTS.md`](../INTEGRATION_TESTS.md)**.
//!
//! Before writing, modifying, or debugging any integration test, you MUST read
//! that document. Key principles:
//!
//! - Test **observable behavior**, not implementation details
//! - Mock only at **architectural boundaries** (filesystem, network, external APIs)
//! - Use `with_default_timeout()` wrapper for all tests
//! - NEVER use `cfg!(test)` branches in production code

use crate::test_timeout::with_default_timeout;

/// Test that valid `all_issues_addressed` status fix XML passes validation.
///
/// This verifies that when the fix agent produces valid XML with
/// `status="all_issues_addressed`", the validation succeeds and identifies
/// the completion status.
#[test]
fn test_fix_xml_valid_all_issues_addressed_status() {
    with_default_timeout(|| {
        // Setup: Create valid XML with all_issues_addressed status
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summary>All reported issues have been fixed</ralph-summary>
</ralph-fix-result>";

        // Execute: Validate the XML through the public API
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Verify OBSERVABLE behavior (validation passes)
        assert!(result.is_ok(), "Valid XML should pass validation");

        let elements = result.unwrap();
        assert_eq!(
            elements.status, "all_issues_addressed",
            "Should extract all_issues_addressed status"
        );
        assert_eq!(
            elements.summary,
            Some("All reported issues have been fixed".to_string()),
            "Should extract optional summary"
        );
        assert!(elements.is_complete(), "Should identify as complete");
        assert!(
            !elements.has_remaining_issues(),
            "Should not have remaining issues"
        );
        assert!(!elements.is_no_issues(), "Should not be no_issues_found");
    });
}

/// Test that valid `issues_remain` status fix XML passes validation.
///
/// This verifies that when the fix agent produces valid XML with
/// `status="issues_remain`", the validation succeeds and identifies
/// that more work is needed.
#[test]
fn test_fix_xml_valid_issues_remain_status() {
    with_default_timeout(|| {
        // Setup: Create valid XML with issues_remain status
        let xml = r"<ralph-fix-result>
<ralph-status>issues_remain</ralph-status>
<ralph-summary>Some issues fixed, but more work needed</ralph-summary>
</ralph-fix-result>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Verify validation passes and issues_remain status is detected
        assert!(
            result.is_ok(),
            "Valid issues_remain XML should pass validation"
        );

        let elements = result.unwrap();
        assert_eq!(
            elements.status, "issues_remain",
            "Should extract issues_remain status"
        );
        assert!(
            elements.has_remaining_issues(),
            "Should identify as having remaining issues"
        );
        assert!(!elements.is_complete(), "Should not be complete");
    });
}

/// Test that valid `no_issues_found` status fix XML passes validation.
///
/// This verifies that when the fix agent produces valid XML with
/// `status="no_issues_found`", the validation succeeds and identifies
/// that there were no issues to fix.
#[test]
fn test_fix_xml_valid_no_issues_found_status() {
    with_default_timeout(|| {
        // Setup: Create valid XML with no_issues_found status (summary optional)
        let xml = r"<ralph-fix-result>
<ralph-status>no_issues_found</ralph-status>
</ralph-fix-result>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Verify validation passes and no_issues_found status is detected
        assert!(
            result.is_ok(),
            "Valid no_issues_found XML should pass validation"
        );

        let elements = result.unwrap();
        assert_eq!(
            elements.status, "no_issues_found",
            "Should extract no_issues_found status"
        );
        assert!(
            elements.is_no_issues(),
            "Should identify as no issues found"
        );
        assert!(
            elements.is_complete(),
            "Should also be considered complete (no work needed)"
        );
        assert!(
            !elements.has_remaining_issues(),
            "Should not have remaining issues"
        );
        assert!(
            elements.summary.is_none(),
            "Optional summary should be None when not provided"
        );
    });
}

/// Test that invalid XML format produces specific XSD validation error.
///
/// This verifies that when the fix agent produces XML that fails
/// XSD validation, a specific error message is produced that can be fed
/// back to the agent for retry.
#[test]
fn test_fix_xml_missing_root_element_provides_specific_error() {
    with_default_timeout(|| {
        // Setup: Create content without proper XML tags
        let content = r"Some random text without proper XML tags";

        // Execute: Try to validate the content
        let result = ralph_workflow::validate_fix_result_xml(content);

        // Assert: Verify validation fails with specific error
        assert!(
            result.is_err(),
            "Missing root element should fail validation"
        );

        let error = result.unwrap_err();
        assert_eq!(
            error.element_path, "ralph-fix-result",
            "Error should identify missing root element"
        );
        assert!(
            error.expected.contains("ralph-fix-result"),
            "Error should indicate expected root element"
        );
        assert!(
            error.suggestion.contains("ralph-fix-result"),
            "Error should provide actionable suggestion"
        );
    });
}

/// Test that invalid status value produces specific XSD validation error.
///
/// This verifies that when the fix agent uses an invalid status value,
/// a specific error message identifies the valid options.
#[test]
fn test_fix_xml_invalid_status_provides_valid_options() {
    with_default_timeout(|| {
        // Setup: Create XML with invalid status value
        let xml = r"<ralph-fix-result>
<ralph-status>invalid_status</ralph-status>
</ralph-fix-result>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Verify validation fails with specific error about valid values
        assert!(result.is_err(), "Invalid status should fail validation");

        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error should identify status element, got: {}",
            error.element_path
        );
        assert!(
            error.expected.contains("all_issues_addressed")
                && error.expected.contains("issues_remain")
                && error.expected.contains("no_issues_found"),
            "Error should list all valid status values"
        );
        assert_eq!(
            error.found, "invalid_status",
            "Error should show what was provided"
        );
    });
}

/// Test that empty status produces specific error.
///
/// This verifies that when the fix agent includes an empty status element,
/// a specific error identifies the problem.
#[test]
fn test_fix_xml_empty_status_produces_error() {
    with_default_timeout(|| {
        // Setup: Create XML with empty status
        let xml = r"<ralph-fix-result>
<ralph-status>   </ralph-status>
</ralph-fix-result>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Verify validation fails
        assert!(result.is_err(), "Empty status should fail validation");

        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error should identify status element, got: {}",
            error.element_path
        );
        assert!(
            error.expected.contains("non-empty"),
            "Error should indicate status must be non-empty"
        );
    });
}

/// Test that XML extraction works from markdown code fence wrapped content.
///
/// This verifies that fix result XML can be extracted even when wrapped
/// in markdown code fences, which is a common AI output pattern.
#[test]
fn test_fix_xml_extraction_from_markdown_fence() {
    with_default_timeout(|| {
        // Setup: Create content with XML wrapped in markdown fence
        let content = r"Here's my fix status:

```xml
<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summary>All fixed</ralph-summary>
</ralph-fix-result>
```

That's all.";

        // Execute: Extract XML from the content
        let extracted = ralph_workflow::extract_fix_result_xml(content);

        // Assert: Verify XML is extracted and validates
        assert!(
            extracted.is_some(),
            "Should extract XML from markdown fence"
        );

        let xml = extracted.unwrap();
        let result = ralph_workflow::validate_fix_result_xml(&xml);
        assert!(result.is_ok(), "Extracted XML should validate");
    });
}

/// Test that XML extraction works from JSON string escaped content.
///
/// This verifies that fix result XML can be extracted even when
/// JSON-escaped as a string, which can happen in some output formats.
#[test]
fn test_fix_xml_extraction_from_json_string() {
    with_default_timeout(|| {
        // Setup: Create JSON with escaped XML string
        let content = r#"{"type":"result","result":"<ralph-fix-result>\n<ralph-status>all_issues_addressed<\/ralph-status>\n<ralph-summary>All fixed<\/ralph-summary>\n<\/ralph-fix-result>"}"#;

        // Execute: Extract XML from the JSON content
        let extracted = ralph_workflow::extract_fix_result_xml(content);

        // Assert: Verify XML is extracted and validates
        assert!(extracted.is_some(), "Should extract XML from JSON string");

        let xml = extracted.unwrap();
        let result = ralph_workflow::validate_fix_result_xml(&xml);
        assert!(result.is_ok(), "Extracted XML should validate");
    });
}

/// Test that XML is formatted nicely for display.
///
/// This verifies that valid XML is formatted in a user-friendly way
/// rather than displayed as raw XML.
#[test]
fn test_fix_xml_formatted_for_display() {
    with_default_timeout(|| {
        // Setup: Create valid XML
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summary>All issues have been successfully fixed</ralph-summary>
</ralph-fix-result>";

        // Execute: Format the XML for display
        let formatted = ralph_workflow::files::llm_output_extraction::format_xml_for_display(xml);

        // Assert: Verify output is formatted nicely (pretty-printed XML)
        assert!(
            formatted.contains("All issues have been successfully fixed"),
            "Should include summary"
        );
        assert!(
            formatted.contains("all_issues_addressed")
                || formatted.contains("All Issues Addressed"),
            "Should include status"
        );
        // format_xml_for_display returns pretty-printed XML (with indentation)
        assert!(
            formatted.contains("<ralph-"),
            "Should include XML tags (pretty-printed format)"
        );
    });
}

/// Test that duplicate elements produce specific error.
///
/// This verifies that when the fix agent includes duplicate elements,
/// a specific error identifies the problem.
#[test]
fn test_fix_xml_duplicate_status_produces_specific_error() {
    with_default_timeout(|| {
        // Setup: Create XML with duplicate status element
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-status>issues_remain</ralph-status>
</ralph-fix-result>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Verify validation fails with duplicate element error
        assert!(result.is_err(), "Duplicate status should fail validation");

        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error should identify duplicated element, got: {}",
            error.element_path
        );
        assert!(
            error.expected.contains("only one"),
            "Error should indicate only one element is allowed"
        );
        assert!(
            error.found.contains("duplicate"),
            "Error should indicate this is a duplicate"
        );
    });
}

/// Test that unexpected elements produce specific error.
///
/// This verifies that when the fix agent includes unknown elements,
/// the validator is now tolerant and skips them rather than failing.
/// Required elements (status) are still enforced.
#[test]
fn test_fix_xml_unexpected_element_provides_valid_options() {
    with_default_timeout(|| {
        // Setup: Create XML with unexpected element
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-unknown-field>Some value</ralph-unknown-field>
</ralph-fix-result>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Tolerant validator skips unknown elements instead of rejecting.
        // Required element (status) is present and valid.
        assert!(
            result.is_ok(),
            "Tolerant validator should skip unknown elements: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "all_issues_addressed");
    });
}

/// Test that missing closing tag produces specific error.
///
/// This verifies that when the fix agent's XML is missing the closing tag,
/// a specific error identifies the problem.
#[test]
fn test_fix_xml_missing_closing_tag_produces_error() {
    with_default_timeout(|| {
        // Setup: Create XML without closing tag
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Verify validation fails
        assert!(
            result.is_err(),
            "Missing closing tag should fail validation"
        );

        let error = result.unwrap_err();
        assert_eq!(
            error.element_path, "ralph-fix-result",
            "Error should identify root element"
        );
        assert!(
            error.expected.contains("closing") && error.expected.contains("</ralph-fix-result>"),
            "Error should indicate missing closing tag"
        );
    });
}

/// Test XSD validation error messages include all required information.
///
/// This verifies that XSD validation errors contain the information needed
/// to provide useful feedback to the AI agent for retry.
#[test]
fn test_fix_xsd_error_contains_all_required_information() {
    with_default_timeout(|| {
        // Setup: Create invalid XML (missing status element)
        let xml = r"<ralph-fix-result>
</ralph-fix-result>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Verify error contains all required fields
        assert!(result.is_err(), "Invalid XML should fail validation");

        let error = result.unwrap_err();

        // Verify error has element_path (identifies where the error is)
        assert!(
            !error.element_path.is_empty(),
            "Error should have element_path"
        );

        // Verify error has expected (what was expected)
        assert!(
            !error.expected.is_empty(),
            "Error should have expected field"
        );

        // Verify error has found (what was actually found)
        assert!(!error.found.is_empty(), "Error should have found field");

        // Verify error has suggestion (how to fix it)
        assert!(!error.suggestion.is_empty(), "Error should have suggestion");

        // Verify format_for_ai_retry produces a complete message (dumb-agent-proof format)
        let formatted = error.format_for_ai_retry();
        assert!(
            formatted.contains(&error.element_path),
            "Formatted error should include element_path"
        );
        // New format uses "What failed:" style for dumb-agent-proof contract
        assert!(
            formatted.contains("What failed"),
            "Formatted error should use dumb-agent-proof format"
        );
        assert!(
            formatted.contains("How to fix"),
            "Formatted error should include fix guidance"
        );
    });
}

/// Test that summary field is truly optional.
///
/// This verifies that the fix XML schema correctly handles the optional
/// summary field - it can be omitted without validation error.
#[test]
fn test_fix_xml_summary_is_optional() {
    with_default_timeout(|| {
        // Setup: Create minimal valid XML (only required status field)
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
</ralph-fix-result>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Verify validation passes and summary is None
        assert!(result.is_ok(), "Minimal valid XML should pass validation");

        let elements = result.unwrap();
        assert!(
            elements.summary.is_none(),
            "Optional summary should be None when not provided"
        );
        assert_eq!(
            elements.status, "all_issues_addressed",
            "Should extract status"
        );
    });
}

/// Test that whitespace-only summary is treated as missing.
///
/// This verifies that when the fix agent includes a summary with only
/// whitespace, it's treated as if no summary was provided.
#[test]
fn test_fix_xml_whitespace_only_summary_is_treated_as_missing() {
    with_default_timeout(|| {
        // Setup: Create XML with whitespace-only summary
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summary>   </ralph-summary>
</ralph-fix-result>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_fix_result_xml(xml);

        // Assert: Verify validation passes but summary is None
        assert!(
            result.is_ok(),
            "Whitespace-only summary should be acceptable"
        );

        let elements = result.unwrap();
        assert!(
            elements.summary.is_none(),
            "Whitespace-only summary should be filtered to None"
        );
    });
}
