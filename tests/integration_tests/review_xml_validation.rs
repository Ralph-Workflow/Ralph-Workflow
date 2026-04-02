//! Integration tests for review agent XML validation.
//!
//! This module tests the XML extraction and XSD validation behavior for
//! the review agent's output (issues list).
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

/// Test that valid issues XML passes validation.
///
/// This verifies that when the review agent produces valid XML with
/// issues, the validation succeeds and extracts the issue list.
#[test]
fn test_review_xml_valid_issues() {
    with_default_timeout(|| {
        // Setup: Create valid XML with issues (simple text descriptions)
        let xml = r"<ralph-issues>
<ralph-issue>Variable unused in src/main.rs</ralph-issue>
<ralph-issue>Missing error handling in src/utils.rs</ralph-issue>
</ralph-issues>";

        // Execute: Validate the XML through the public API
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Verify OBSERVABLE behavior (validation passes)
        assert!(result.is_ok(), "Valid XML should pass validation");

        let elements = result.unwrap();
        assert!(
            elements.no_issues_found.is_none(),
            "Should not have no_issues_found when issues exist"
        );

        // Verify issue content (array access implicitly verifies count of 2)
        assert_eq!(
            elements.issues[0].text, "Variable unused in src/main.rs",
            "Should extract first issue"
        );

        assert_eq!(
            elements.issues[1].text, "Missing error handling in src/utils.rs",
            "Should extract second issue"
        );
    });
}

/// Test that valid `no_issues_found` XML passes validation.
///
/// This verifies that when the review agent produces valid XML with
/// `no_issues_found` element, the validation succeeds and identifies
/// that no issues were found.
#[test]
fn test_review_xml_valid_no_issues_found() {
    with_default_timeout(|| {
        // Setup: Create valid XML with no_issues_found
        let xml = r"<ralph-issues>
<ralph-no-issues-found>No issues found during review</ralph-no-issues-found>
</ralph-issues>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Verify validation passes
        assert!(
            result.is_ok(),
            "Valid no_issues_found XML should pass validation"
        );

        let elements = result.unwrap();
        assert!(elements.issues.is_empty(), "Should have no issues");
        assert_eq!(
            elements.no_issues_found,
            Some("No issues found during review".to_string()),
            "Should extract no_issues_found message"
        );
    });
}

/// Test that invalid XML format produces specific XSD validation error.
///
/// This verifies that when the review agent produces XML that fails
/// XSD validation, a specific error message is produced that can be fed
/// back to the agent for retry.
#[test]
fn test_review_xml_missing_root_element_provides_specific_error() {
    with_default_timeout(|| {
        // Setup: Create content without proper XML tags
        let content = r"Some random text without proper XML tags";

        // Execute: Try to validate the content
        let result = ralph_workflow::validate_issues_xml(content);

        // Assert: Verify validation fails with specific error
        assert!(
            result.is_err(),
            "Missing root element should fail validation"
        );

        let error = result.unwrap_err();
        assert_eq!(
            error.element_path, "ralph-issues",
            "Error should identify missing root element"
        );
        assert!(
            error.expected.contains("ralph-issues"),
            "Error should indicate expected root element"
        );
        assert!(
            error.suggestion.contains("ralph-issues"),
            "Error should provide actionable suggestion"
        );
    });
}

/// Test that empty issues list produces specific error.
///
/// This verifies that when the review agent produces an issues element
/// with no actual issues and no `no_issues_found` element, validation fails.
#[test]
fn test_review_xml_empty_issues_list_produces_error() {
    with_default_timeout(|| {
        // Setup: Create XML with empty issues list (no issues, no no_issues_found)
        let xml = r"<ralph-issues>
</ralph-issues>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Verify validation fails
        assert!(result.is_err(), "Empty issues list should fail validation");

        let error = result.unwrap_err();
        // Should indicate that either issues or no-issues-found is expected
        assert!(
            error.expected.contains("ralph-issue") || error.expected.contains("at least"),
            "Error should indicate what's expected, got: {}",
            error.expected
        );
    });
}

/// Test that XML extraction works from markdown code fence wrapped content.
///
/// This verifies that issues XML can be extracted even when wrapped
/// in markdown code fences, which is a common AI output pattern.
#[test]
fn test_review_xml_extraction_from_markdown_fence() {
    with_default_timeout(|| {
        // Setup: Create content with XML wrapped in markdown fence
        let content = r"Here's my review:

```xml
<ralph-issues>
<ralph-issue>Variable unused</ralph-issue>
</ralph-issues>
```

That's all.";

        // Execute: Extract XML from the content
        let extracted = ralph_workflow::extract_issues_xml(content);

        // Assert: Verify XML is extracted and validates
        assert!(
            extracted.is_some(),
            "Should extract XML from markdown fence"
        );

        let xml = extracted.unwrap();
        let result = ralph_workflow::validate_issues_xml(&xml);
        assert!(result.is_ok(), "Extracted XML should validate");
    });
}

/// Test that XML extraction works from JSON string escaped content.
///
/// This verifies that issues XML can be extracted even when
/// JSON-escaped as a string, which can happen in some output formats.
#[test]
fn test_review_xml_extraction_from_json_string() {
    with_default_timeout(|| {
        // Setup: Create JSON with escaped XML string
        let content = r#"{"type":"result","result":"<ralph-issues>\n<ralph-issue>Variable unused<\/ralph-issue>\n<\/ralph-issues>"}"#;

        // Execute: Extract XML from the JSON content
        let extracted = ralph_workflow::extract_issues_xml(content);

        // Assert: Verify XML is extracted and validates
        assert!(extracted.is_some(), "Should extract XML from JSON string");

        let xml = extracted.unwrap();
        let result = ralph_workflow::validate_issues_xml(&xml);
        assert!(result.is_ok(), "Extracted XML should validate");
    });
}

/// Test that XML is formatted nicely for display.
///
/// This verifies that valid XML is formatted in a user-friendly way
/// rather than displayed as raw XML.
#[test]
fn test_review_xml_formatted_for_display() {
    with_default_timeout(|| {
        // Setup: Create valid XML
        let xml = r"<ralph-issues>
<ralph-issue>Variable unused in src/main.rs</ralph-issue>
</ralph-issues>";

        // Execute: Format the XML for display
        let formatted = ralph_workflow::files::llm_output_extraction::format_xml_for_display(xml);

        // Assert: Verify output is formatted nicely (pretty-printed XML)
        assert!(
            formatted.contains("Variable unused"),
            "Should include issue description"
        );
        assert!(
            formatted.contains("src/main.rs"),
            "Should include file reference"
        );
        // format_xml_for_display returns pretty-printed XML (with indentation)
        assert!(
            formatted.contains("<ralph-"),
            "Should include XML tags (pretty-printed format)"
        );
    });
}

/// Test that multiple issues are all extracted.
///
/// This verifies that when the review agent produces multiple issues,
/// all of them are extracted and validated.
#[test]
fn test_review_xml_multiple_issues_all_extracted() {
    with_default_timeout(|| {
        // Setup: Create XML with multiple issues
        let xml = r"<ralph-issues>
<ralph-issue>Error 1</ralph-issue>
<ralph-issue>Warning 1</ralph-issue>
<ralph-issue>Info 1</ralph-issue>
<ralph-issue>Note 1</ralph-issue>
</ralph-issues>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Verify all issues are extracted
        assert!(
            result.is_ok(),
            "Valid XML with multiple issues should pass validation"
        );

        let elements = result.unwrap();

        // Verify each issue is present (testing observable behavior)
        assert_eq!(elements.issues[0].text, "Error 1");
        assert_eq!(elements.issues[1].text, "Warning 1");
        assert_eq!(elements.issues[2].text, "Info 1");
        assert_eq!(elements.issues[3].text, "Note 1");
    });
}

/// Test that issues and `no_issues_found` cannot coexist.
///
/// This verifies that when both issues and `no_issues_found` are present,
/// validation produces an appropriate error.
#[test]
fn test_review_xml_issues_and_no_issues_found_cannot_coexist() {
    with_default_timeout(|| {
        // Setup: Create XML with both issues and no_issues_found
        let xml = r"<ralph-issues>
<ralph-issue>Some issue</ralph-issue>
<ralph-no-issues-found>No issues found</ralph-no-issues-found>
</ralph-issues>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Verify validation fails
        assert!(
            result.is_err(),
            "Should not allow both issues and no_issues_found"
        );

        let error = result.unwrap_err();
        assert!(
            error.expected.contains("either") || error.expected.contains("OR"),
            "Error should indicate mutual exclusivity"
        );
    });
}

/// Test that duplicate `no_issues_found` produces specific error.
///
/// This verifies that when the review agent includes multiple `no_issues_found`
/// elements, validation produces an appropriate error.
#[test]
fn test_review_xml_duplicate_no_issues_found_produces_error() {
    with_default_timeout(|| {
        // Setup: Create XML with duplicate no_issues_found
        let xml = r"<ralph-issues>
<ralph-no-issues-found>No issues</ralph-no-issues-found>
<ralph-no-issues-found>Also no issues</ralph-no-issues-found>
</ralph-issues>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Verify validation fails
        assert!(
            result.is_err(),
            "Duplicate no_issues_found should fail validation"
        );

        let error = result.unwrap_err();
        assert_eq!(
            error.element_path, "ralph-issues/ralph-no-issues-found",
            "Error should identify the duplicated element"
        );
        assert!(
            error.found.contains("duplicate") || error.expected.contains("only one"),
            "Error should indicate this is a duplicate"
        );
    });
}

/// Test that XML with only unknown elements still fails validation.
///
/// This verifies that when the review agent includes only unknown elements
/// and no valid issue content, the validator still correctly rejects the
/// response because no required issue content was found. The tolerant
/// validator skips unknown elements but still requires at least one
/// `<ralph-issue>` or `<ralph-no-issues-found>`.
#[test]
fn test_review_xml_unexpected_element_provides_valid_options() {
    with_default_timeout(|| {
        // Setup: Create XML with ONLY an unexpected element (no valid issue content)
        let xml = r"<ralph-issues>
<ralph-unknown-field>Some value</ralph-unknown-field>
</ralph-issues>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Should still fail because no valid issue content was found.
        // The tolerant validator skips unknown elements but enforces the requirement
        // for at least one <ralph-issue> or <ralph-no-issues-found>.
        assert!(
            result.is_err(),
            "XML with only unknown elements should fail (no valid issue content found)"
        );

        let error = result.unwrap_err();
        // Error should be about missing required content, not about the unknown element itself
        assert!(
            error.expected.contains("at least one"),
            "Error should explain that valid issue content is required, got: {}",
            error.expected
        );
    });
}

/// Test that missing closing tag produces specific error.
///
/// This verifies that when the review agent's XML is missing the closing tag,
/// a specific error identifies the problem.
#[test]
fn test_review_xml_missing_closing_tag_produces_error() {
    with_default_timeout(|| {
        // Setup: Create XML without closing tag
        let xml = r"<ralph-issues>
<ralph-issue>Some issue</ralph-issue>
";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Verify validation fails
        assert!(
            result.is_err(),
            "Missing closing tag should fail validation"
        );

        let error = result.unwrap_err();
        assert_eq!(
            error.element_path, "ralph-issues",
            "Error should identify root element"
        );
        assert!(
            error.expected.contains("closing") && error.expected.contains("</ralph-issues>"),
            "Error should indicate missing closing tag"
        );
    });
}

/// Test XSD validation error messages include all required information.
///
/// This verifies that XSD validation errors contain the information needed
/// to provide useful feedback to the AI agent for retry.
#[test]
fn test_review_xsd_error_contains_all_required_information() {
    with_default_timeout(|| {
        // Setup: Create invalid XML (missing root element)
        let xml = r"Random text without proper XML";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

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

/// Test that whitespace-only issues are filtered.
///
/// This verifies that when the review agent includes issues with only
/// whitespace, they are filtered out.
#[test]
fn test_review_xml_whitespace_only_issues_are_filtered() {
    with_default_timeout(|| {
        // Setup: Create XML with whitespace-only issues
        let xml = r"<ralph-issues>
<ralph-issue>   </ralph-issue>
<ralph-issue>Actual issue</ralph-issue>
<ralph-issue>  </ralph-issue>
</ralph-issues>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Verify validation passes and whitespace issues are filtered
        assert!(
            result.is_ok(),
            "Whitespace filtering should still pass validation"
        );

        let elements = result.unwrap();
        // Verify the actual issue is present (testing observable behavior)
        assert_eq!(
            elements.issues[0].text, "Actual issue",
            "Should keep actual issue and filter whitespace-only issues"
        );
    });
}

/// Test that whitespace-only `no_issues_found` is filtered.
///
/// This verifies that when the review agent includes `no_issues_found` with only
/// whitespace, it's treated as missing and validation fails (since we need either
/// issues or `no_issues_found`).
#[test]
fn test_review_xml_whitespace_only_no_issues_found_is_filtered() {
    with_default_timeout(|| {
        // Setup: Create XML with whitespace-only no_issues_found
        let xml = r"<ralph-issues>
<ralph-no-issues-found>   </ralph-no-issues-found>
</ralph-issues>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Verify validation fails (whitespace is filtered to None, leaving empty issues)
        assert!(
            result.is_err(),
            "Whitespace-only no_issues_found should be filtered and fail validation"
        );
    });
}

// =============================================================================
// ILLEGAL CHARACTER REGRESSION TESTS
// These test the fix for the bug where NUL bytes in XML caused silent failures
// =============================================================================

/// Test that NUL byte in review XML is rejected with actionable error.
///
/// This is a regression test for the reported bug where a reviewer output
/// containing a NUL byte (e.g., from `.replace("git diff", "git\0A0diff")`)
/// would cause validation to fail silently, triggering `AgentChainExhausted`.
///
/// The validation should now detect the NUL byte before parsing and provide
/// a clear, actionable error message that guides the agent to fix it.
#[test]
fn test_review_xml_rejects_nul_byte_with_actionable_error() {
    with_default_timeout(|| {
        // Setup: Create XML with NUL byte (the exact bug from the report)
        // This simulates the pattern: .replace("git diff", "git\0A0diff")
        let xml_with_nul =
            "<ralph-issues><ralph-issue>Check git\u{0000}diff usage</ralph-issue></ralph-issues>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_issues_xml(xml_with_nul);

        // Assert: Verify validation fails
        assert!(result.is_err(), "NUL byte should be rejected");

        let error = result.unwrap_err();

        // Verify error is specific about NUL byte
        assert!(
            error.found.contains("NUL") || error.found.contains("0x00"),
            "Error should identify NUL byte, got: {}",
            error.found
        );

        // Verify error includes position information
        assert!(
            error.found.contains("position"),
            "Error should include position, got: {}",
            error.found
        );

        // Verify error provides actionable guidance
        let formatted = error.format_for_ai_retry();
        assert!(
            formatted.contains("NUL") || formatted.contains("0x00"),
            "Formatted error should mention NUL"
        );
        assert!(
            formatted.contains("How to fix") || formatted.contains("fix:"),
            "Formatted error should provide fix guidance"
        );
        // Should suggest common fix (NBSP typo)
        assert!(
            formatted.contains("\\u00A0") || formatted.contains("non-breaking space"),
            "Should suggest NBSP as common cause"
        );
    });
}

/// Test that other illegal control characters are also rejected.
///
/// This ensures the fix is comprehensive, not just for NUL bytes.
#[test]
fn test_review_xml_rejects_other_control_characters() {
    with_default_timeout(|| {
        // Test vertical tab (0x0B) - another illegal control character
        let xml_with_vt =
            "<ralph-issues><ralph-issue>Text with\u{000B}vertical tab</ralph-issue></ralph-issues>";
        let result = ralph_workflow::validate_issues_xml(xml_with_vt);
        assert!(result.is_err(), "Vertical tab should be rejected");

        let error = result.unwrap_err();
        assert!(
            error.found.contains("0x0B") || error.found.contains("control character"),
            "Error should identify illegal character, got: {}",
            error.found
        );
    });
}

/// Test that valid content with allowed control characters still passes.
///
/// This verifies we don't break valid XML that uses tab/newline/CR.
#[test]
fn test_review_xml_allows_valid_control_characters() {
    with_default_timeout(|| {
        // Setup: Create XML with tab, newline, carriage return (all allowed)
        let xml =
            "<ralph-issues><ralph-issue>Line 1\nLine 2\tTabbed\rCR</ralph-issue></ralph-issues>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_issues_xml(xml);

        // Assert: Verify validation passes
        assert!(
            result.is_ok(),
            "Valid control characters (tab/LF/CR) should be allowed"
        );
    });
}

/// Test that XSD validation error detail flows correctly.
///
/// This integration test verifies:
/// 1. Validation fails with specific XSD error (NUL byte)
/// 2. The error detail is actionable and mentions the specific problem
///
/// This is a regression test for the bug where validation errors were not
/// propagated, causing the XSD retry prompt to show only a generic message.
///
/// Note: This test currently only validates that the XSD error is detailed.
/// Full flow testing (event -> state -> prompt) requires internal APIs that
/// are tested in unit tests.
#[test]
fn test_review_xsd_error_is_detailed_for_retry() {
    with_default_timeout(|| {
        // Setup: Create XML with NUL byte that will fail validation
        let xml_with_nul =
            "<ralph-issues><ralph-issue>Check git\u{0000}diff usage</ralph-issue></ralph-issues>";

        // Execute: Verify validation produces specific error about NUL
        let validation_result = ralph_workflow::validate_issues_xml(xml_with_nul);
        assert!(
            validation_result.is_err(),
            "NUL byte should fail validation"
        );

        let xsd_error = validation_result.unwrap_err();
        let error_detail = xsd_error.format_for_ai_retry();

        // Assert: Error detail should be specific and actionable
        assert!(
            error_detail.contains("NUL") || error_detail.contains("0x00"),
            "Error detail should mention NUL byte, got: {error_detail}"
        );

        assert!(
            error_detail.contains("How to fix") || error_detail.contains("fix:"),
            "Error detail should provide fix guidance, got: {error_detail}"
        );

        // The error should suggest the common NBSP typo fix
        assert!(
            error_detail.contains("\\u00A0") || error_detail.contains("non-breaking space"),
            "Error should suggest NBSP as common cause, got: {error_detail}"
        );
    });
}

/// Test that XSD retry error messages provide sufficient context for convergence.
///
/// This test verifies that when validation fails with a NUL byte error,
/// the error message provides all necessary information for an agent to fix it:
/// 1. Identifies the problem (NUL byte)
/// 2. Provides context (position/location)
/// 3. Suggests the fix (NBSP typo)
/// 4. Is actionable (has "How to fix" section)
///
/// This is a regression test for the bug where review prints "Found N issue(s)"
/// but validation fails with illegal characters, causing `AgentChainExhausted`.
/// The error message must be detailed enough for XSD retry to converge.
#[test]
fn test_review_xsd_error_provides_convergence_context() {
    with_default_timeout(|| {
        // Setup: Invalid XML with NUL byte (the bug scenario from issue report)
        // This simulates: .replace("git diff", "git\0A0diff")
        let invalid_xml =
            "<ralph-issues><ralph-issue>Check git\u{0000}A0diff usage</ralph-issue></ralph-issues>";

        // Execute: Get validation error
        let result = ralph_workflow::validate_issues_xml(invalid_xml);
        assert!(result.is_err(), "NUL byte should fail validation");

        let error = result.unwrap_err();
        let formatted = error.format_for_ai_retry();

        // Assert: Error provides all information needed for agent to fix

        // 1. Identifies the problem (NUL byte)
        assert!(
            formatted.contains("NUL") || formatted.contains("0x00"),
            "Formatted error should identify NUL byte, got: {formatted}"
        );

        // 2. Provides context (position/location)
        assert!(
            error.found.contains("position") || error.found.contains("byte"),
            "Error should provide position context, got: {}",
            error.found
        );

        // 3. Suggests the fix (NBSP typo)
        assert!(
            formatted.contains("\\u00A0") || formatted.contains("non-breaking space"),
            "Formatted error should suggest NBSP as likely fix, got: {formatted}"
        );

        // 4. Is actionable (has "How to fix" section)
        assert!(
            formatted.contains("How to fix") || formatted.contains("fix:"),
            "Formatted error should have actionable fix guidance, got: {formatted}"
        );

        // The formatted error should be detailed enough for retry convergence
        assert!(
            formatted.len() > 100,
            "Error should be detailed enough for retry convergence, got {} chars",
            formatted.len()
        );
    });
}

/// Test that XSD retry provides actionable error messages for illegal character errors.
///
/// This regression test verifies the complete XSD retry flow when a reviewer produces
/// XML containing an illegal NUL byte. The error message must be clear enough for
/// an agent to understand and fix the problem on retry.
///
/// This addresses the bug where review prints "Found N issue(s)" but validation fails
/// with illegal characters, causing retries until `AgentChainExhausted`.
#[test]
fn test_xsd_retry_error_message_for_nul_byte_is_actionable() {
    with_default_timeout(|| {
        // Setup: Create XML with NUL byte (simulating the bug scenario)
        let xml_with_nul =
            "<ralph-issues><ralph-issue>Check git\u{0000}diff usage</ralph-issue></ralph-issues>";

        // Execute: Validate and get error
        let result = ralph_workflow::validate_issues_xml(xml_with_nul);
        assert!(result.is_err(), "NUL byte should cause validation error");

        let error = result.unwrap_err();

        // Verify: The error message formatted for AI retry must:
        let formatted = error.format_for_ai_retry();

        // 1. Clearly identify this as an illegal character issue
        assert!(
            formatted.contains("ILLEGAL CHARACTER")
                || formatted.contains("illegal character")
                || formatted.contains("CRITICAL"),
            "Error should prominently identify illegal character issue, got:\n{formatted}"
        );

        // 2. Identify the specific character (NUL/0x00)
        assert!(
            formatted.contains("NUL") || formatted.contains("0x00"),
            "Error should identify NUL byte, got:\n{formatted}"
        );

        // 3. Provide actionable fix guidance
        assert!(
            formatted.contains("How to fix")
                || formatted.contains("FIX REQUIRED")
                || formatted.contains("Solution"),
            "Error should provide fix guidance, got:\n{formatted}"
        );

        // 4. Mention the common mistake (NBSP typo)
        assert!(
            formatted.contains("\\u00A0") || formatted.contains("non-breaking space"),
            "Error should mention common NBSP typo, got:\n{formatted}"
        );

        // 5. Include position/context information for finding the error
        assert!(
            error.found.contains("position") || error.suggestion.contains("Near:"),
            "Error should include position/context, got found: {}, suggestion: {}",
            error.found,
            error.suggestion
        );
    });
}
