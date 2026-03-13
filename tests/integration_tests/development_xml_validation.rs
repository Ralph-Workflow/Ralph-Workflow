//! Integration tests for development agent XML validation.
//!
//! This module tests the XML extraction and XSD validation behavior for
//! the development agent's output.
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

/// Test that valid completed status development XML passes validation.
///
/// This verifies that when the development agent produces valid XML with
/// status="completed", the validation succeeds and extracts the expected elements.
#[test]
fn test_development_xml_valid_completed_status() {
    with_default_timeout(|| {
        // Setup: Create valid XML with completed status
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Implemented the feature successfully</ralph-summary>
</ralph-development-result>";

        // Execute: Validate the XML through the public API
        let result = ralph_workflow::validate_development_result_xml(xml);

        // Assert: Verify OBSERVABLE behavior (validation passes)
        assert!(result.is_ok(), "Valid XML should pass validation");

        let elements = result.unwrap();
        assert_eq!(
            elements.status, "completed",
            "Should extract completed status"
        );
        assert_eq!(
            elements.summary, "Implemented the feature successfully",
            "Should extract summary"
        );
        assert!(elements.is_completed(), "Should identify as completed");
        assert!(!elements.is_partial(), "Should not be partial");
        assert!(!elements.is_failed(), "Should not be failed");
    });
}

/// Test that valid partial status development XML passes validation.
///
/// This verifies that when the development agent produces valid XML with
/// status="partial", the validation succeeds and identifies the partial status.
#[test]
fn test_development_xml_valid_partial_status() {
    with_default_timeout(|| {
        // Setup: Create valid XML with partial status
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Started implementation, more work needed</ralph-summary>
<ralph-files-changed>- src/main.rs
- src/utils.rs</ralph-files-changed>
</ralph-development-result>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

        // Assert: Verify validation passes and partial status is detected
        assert!(result.is_ok(), "Valid partial XML should pass validation");

        let elements = result.unwrap();
        assert_eq!(elements.status, "partial", "Should extract partial status");
        assert!(elements.is_partial(), "Should identify as partial");
        assert!(!elements.is_completed(), "Should not be completed");
        assert_eq!(
            elements.files_changed,
            Some("- src/main.rs\n- src/utils.rs".to_string()),
            "Should extract optional files changed"
        );
    });
}

/// Test that valid failed status development XML passes validation.
///
/// This verifies that when the development agent produces valid XML with
/// status="failed", the validation succeeds and identifies the failed status.
#[test]
fn test_development_xml_valid_failed_status() {
    with_default_timeout(|| {
        // Setup: Create valid XML with failed status
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>Could not complete the task due to errors</ralph-summary>
<ralph-next-steps>Review error logs and retry</ralph-next-steps>
</ralph-development-result>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

        // Assert: Verify validation passes and failed status is detected
        assert!(result.is_ok(), "Valid failed XML should pass validation");

        let elements = result.unwrap();
        assert_eq!(elements.status, "failed", "Should extract failed status");
        assert!(elements.is_failed(), "Should identify as failed");
        assert!(!elements.is_completed(), "Should not be completed");
        assert_eq!(
            elements.next_steps,
            Some("Review error logs and retry".to_string()),
            "Should extract optional next steps"
        );
    });
}

/// Test that continuation XML requires a blocker summary plus ordered recovery steps.
#[test]
fn test_continuation_development_xml_requires_recovery_only_contract() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan is not complete because the continuation prompt still allows file-bookkeeping sections.</ralph-summary>
<ralph-next-steps>1. Remove file-bookkeeping from the continuation prompt.
2. Re-run prompt and XML validation tests.
3. Finish the remaining plan and run repository verification.</ralph-next-steps>
</ralph-development-result>";

        let result = ralph_workflow::validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Valid continuation XML should pass validation"
        );

        let elements = result.unwrap();
        assert!(elements.is_partial());
        assert!(elements.files_changed.is_none());
        assert_eq!(
            elements.next_steps,
            Some(
                "1. Remove file-bookkeeping from the continuation prompt.\n2. Re-run prompt and XML validation tests.\n3. Finish the remaining plan and run repository verification.".to_string()
            )
        );
    });
}

/// Test that continuation XML rejects bookkeeping-heavy payloads.
#[test]
fn test_continuation_development_xml_rejects_files_changed() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>The full plan is not complete because clippy still fails.</ralph-summary>
<ralph-files-changed>- src/main.rs</ralph-files-changed>
<ralph-next-steps>1. Fix the clippy failure.
2. Re-run focused tests.
3. Complete the remaining plan.</ralph-next-steps>
</ralph-development-result>";

        let result = ralph_workflow::validate_continuation_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Continuation XML should reject file bookkeeping"
        );

        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-files-changed"));
    });
}

/// Test that continuation XML rejects empty file-bookkeeping elements.
#[test]
fn test_continuation_development_xml_rejects_empty_files_changed_element() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>The full plan was not completed because clippy still fails.</ralph-summary>
<ralph-files-changed />
<ralph-next-steps>1. Fix the clippy failure.
2. Re-run focused tests.
3. Complete the remaining plan.</ralph-next-steps>
</ralph-development-result>";

        let result = ralph_workflow::validate_continuation_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Continuation XML should reject even empty file bookkeeping elements"
        );

        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-files-changed"));
    });
}

/// Test that continuation XML rejects summaries without a blocker explanation.
#[test]
fn test_continuation_development_xml_rejects_summary_without_blocker() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Updated the continuation prompt and added tests.</ralph-summary>
<ralph-next-steps>1. Finish the validator updates.
2. Re-run focused continuation tests.
3. Finish the remaining plan and run repository verification.</ralph-next-steps>
</ralph-development-result>";

        let result = ralph_workflow::validate_continuation_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Continuation XML should reject summaries that do not explain what blocked full-plan completion"
        );

        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-summary"));
        assert!(error
            .suggestion
            .contains("why the full plan was not completed"));
    });
}

/// Test that continuation XML rejects plan-scope summaries that still omit the blocker.
#[test]
fn test_continuation_development_xml_rejects_summary_without_specific_blocker() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed.</ralph-summary>
<ralph-next-steps>1. Implement the missing validator guard.
2. Re-run the focused continuation tests.
3. Finish the remaining plan and run repository verification.</ralph-next-steps>
</ralph-development-result>";

        let result = ralph_workflow::validate_continuation_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Continuation XML should reject summaries that mention the plan but do not explain the blocker"
        );

        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-summary"));
        assert!(error.expected.contains("blocker-focused"));
    });
}

/// Test that continuation XML rejects bookkeeping-heavy ordered next steps.
#[test]
fn test_continuation_development_xml_rejects_bookkeeping_heavy_next_steps() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>The full plan was not completed because validator changes are still missing.</ralph-summary>
<ralph-next-steps>1. Files changed: ralph-workflow/src/prompts/developer/tests.rs.
2. Tests run: cargo test -p ralph-workflow --lib prompts::developer::tests.
3. Work completed: prompt test updates.</ralph-next-steps>
</ralph-development-result>";

        let result = ralph_workflow::validate_continuation_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Continuation XML should reject bookkeeping-heavy ordered next steps"
        );

        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-next-steps"));
        assert!(error.suggestion.contains("recovery checklist"));
    });
}

/// Test that continuation XML rejects blocker summaries scoped only to a local stuck step.
#[test]
fn test_continuation_development_xml_rejects_local_step_summary() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>Could not finish this test because mocks are broken.</ralph-summary>
<ralph-next-steps>1. Fix the broken mock setup.
2. Re-run the failing test.
3. Finish the remaining plan and run repository verification.</ralph-next-steps>
</ralph-development-result>";

        let result = ralph_workflow::validate_continuation_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Continuation XML should reject summaries that explain only a local stuck step instead of the full-plan blocker"
        );

        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-summary"));
        assert!(error.expected.contains("full plan"));
    });
}

/// Test that continuation XML rejects vague ordered steps that do not recover the remaining plan.
#[test]
fn test_continuation_development_xml_rejects_vague_ordered_steps() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because validation logic is still missing.</ralph-summary>
<ralph-next-steps>1. Keep investigating.
2. Try another fix.
3. Continue later.</ralph-next-steps>
</ralph-development-result>";

        let result = ralph_workflow::validate_continuation_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Continuation XML should reject vague ordered steps that are not actionable recovery work"
        );

        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-next-steps"));
        assert!(error.expected.contains("recovery checklist"));
    });
}

/// Test that continuation XML rejects a single recovery step, even when concrete.
#[test]
fn test_continuation_development_xml_rejects_single_recovery_step() {
    with_default_timeout(|| {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because one focused validation fix still needs to land.</ralph-summary>
<ralph-next-steps>1. Implement the missing validation guard, then finish the remaining plan and run repository verification.</ralph-next-steps>
</ralph-development-result>";

        let result = ralph_workflow::validate_continuation_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Continuation XML should reject a single ordered recovery step because the continuation contract requires a checklist"
        );

        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-next-steps"));
        assert!(error.expected.contains("at least two numbered steps"));
    });
}

/// Test that invalid XML format produces specific XSD validation error.
///
/// This verifies that when the development agent produces XML that fails
/// XSD validation, a specific error message is produced that can be fed
/// back to the agent for retry.
#[test]
fn test_development_xml_invalid_format_provides_specific_error() {
    with_default_timeout(|| {
        // Setup: Create XML with missing required element (no summary)
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
</ralph-development-result>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

        // Assert: Verify validation fails with specific error
        assert!(result.is_err(), "Missing summary should fail validation");

        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-summary"),
            "Error should identify missing element, got: {}",
            error.element_path
        );
        assert!(
            error.expected.contains("required"),
            "Error should indicate element is required"
        );
        assert!(
            error.suggestion.contains("ralph-summary"),
            "Error should provide actionable suggestion"
        );

        // Verify the error can be formatted for AI retry
        let formatted_for_ai = error.format_for_ai_retry();
        assert!(
            formatted_for_ai.contains("ralph-summary"),
            "Formatted error should include element name"
        );
        assert!(
            formatted_for_ai.contains("expected"),
            "Formatted error should include what was expected"
        );
        assert!(
            formatted_for_ai.contains("found"),
            "Formatted error should include what was found"
        );
    });
}

/// Test that invalid status value produces specific XSD validation error.
///
/// This verifies that when the development agent uses an invalid status value,
/// a specific error message identifies the valid options.
#[test]
fn test_development_xml_invalid_status_provides_valid_options() {
    with_default_timeout(|| {
        // Setup: Create XML with invalid status value
        let xml = r"<ralph-development-result>
<ralph-status>invalid_status</ralph-status>
<ralph-summary>Some summary</ralph-summary>
</ralph-development-result>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

        // Assert: Verify validation fails with specific error about valid values
        assert!(result.is_err(), "Invalid status should fail validation");

        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error should identify status element, got: {}",
            error.element_path
        );
        assert!(
            error.expected.contains("completed")
                && error.expected.contains("partial")
                && error.expected.contains("failed"),
            "Error should list all valid status values"
        );
        assert_eq!(
            error.found, "invalid_status",
            "Error should show what was provided"
        );
    });
}

/// Test that XML extraction works from markdown code fence wrapped content.
///
/// This verifies that development XML can be extracted even when wrapped
/// in markdown code fences, which is a common AI output pattern.
#[test]
fn test_development_xml_extraction_from_markdown_fence() {
    with_default_timeout(|| {
        // Setup: Create content with XML wrapped in markdown fence
        let content = r"Here's my status:

```xml
<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done</ralph-summary>
</ralph-development-result>
```

That's all.";

        // Execute: Extract XML from the content
        let extracted = ralph_workflow::extract_development_result_xml(content);

        // Assert: Verify XML is extracted and validates
        assert!(
            extracted.is_some(),
            "Should extract XML from markdown fence"
        );

        let xml = extracted.unwrap();
        let result = ralph_workflow::validate_development_result_xml(&xml);
        assert!(result.is_ok(), "Extracted XML should validate");
    });
}

/// Test that XML extraction works from JSON string escaped content.
///
/// This verifies that development XML can be extracted even when
/// JSON-escaped as a string, which can happen in some output formats.
#[test]
fn test_development_xml_extraction_from_json_string() {
    with_default_timeout(|| {
        // Setup: Create JSON with escaped XML string
        let content = r#"{"type":"result","result":"<ralph-development-result>\n<ralph-status>completed</ralph-status>\n<ralph-summary>Done<\/ralph-summary>\n<\/ralph-development-result>"}"#;

        // Execute: Extract XML from the JSON content
        let extracted = ralph_workflow::extract_development_result_xml(content);

        // Assert: Verify XML is extracted and validates
        assert!(extracted.is_some(), "Should extract XML from JSON string");

        let xml = extracted.unwrap();
        let result = ralph_workflow::validate_development_result_xml(&xml);
        assert!(result.is_ok(), "Extracted XML should validate");
    });
}

/// Test that XML is formatted nicely for display.
///
/// This verifies that valid XML is formatted in a user-friendly way
/// rather than displayed as raw XML.
#[test]
fn test_development_xml_formatted_for_display() {
    with_default_timeout(|| {
        // Setup: Create valid XML
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Implemented feature X</ralph-summary>
<ralph-files-changed>- src/main.rs
- src/utils.rs</ralph-files-changed>
<ralph-next-steps>Continue with testing</ralph-next-steps>
</ralph-development-result>";

        // Execute: Format the XML for display
        let formatted = ralph_workflow::files::llm_output_extraction::format_xml_for_display(xml);

        // Assert: Verify output is formatted nicely (pretty-printed XML)
        assert!(
            formatted.contains("Implemented feature X"),
            "Should include summary"
        );
        assert!(formatted.contains("completed"), "Should include status");
        // format_xml_for_display returns pretty-printed XML (with indentation)
        // The content should still have the XML tags
        assert!(
            formatted.contains("<ralph-"),
            "Should include XML tags (pretty-printed format)"
        );
    });
}

/// Test that all optional fields can be omitted.
///
/// This verifies that the development XML schema correctly handles
/// optional fields (files-changed, next-steps).
#[test]
fn test_development_xml_optional_fields_can_be_omitted() {
    with_default_timeout(|| {
        // Setup: Create minimal valid XML (only required fields)
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done</ralph-summary>
</ralph-development-result>";

        // Execute: Validate the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

        // Assert: Verify validation passes and optional fields are None
        assert!(result.is_ok(), "Minimal valid XML should pass validation");

        let elements = result.unwrap();
        assert!(
            elements.files_changed.is_none(),
            "Optional files-changed should be None"
        );
        assert!(
            elements.next_steps.is_none(),
            "Optional next-steps should be None"
        );
    });
}

/// Test that duplicate elements produce specific error.
///
/// This verifies that when the development agent includes duplicate elements,
/// a specific error identifies the problem.
#[test]
fn test_development_xml_duplicate_elements_produce_specific_error() {
    with_default_timeout(|| {
        // Setup: Create XML with duplicate status element
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-status>partial</ralph-status>
<ralph-summary>Some summary</ralph-summary>
</ralph-development-result>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

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
/// This verifies that when the development agent includes unknown elements,
/// the validator is now tolerant and skips them rather than failing.
/// Required elements (status, summary) are still enforced.
#[test]
fn test_development_xml_unexpected_element_provides_valid_options() {
    with_default_timeout(|| {
        // Setup: Create XML with unexpected element
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Some summary</ralph-summary>
<ralph-unknown-element>Some value</ralph-unknown-element>
</ralph-development-result>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

        // Assert: Tolerant validator skips unknown elements instead of rejecting.
        // Required elements (status, summary) are present and valid.
        assert!(
            result.is_ok(),
            "Tolerant validator should skip unknown elements: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
        assert_eq!(elements.summary, "Some summary");
    });
}

/// Test that text inside root but outside child elements is tolerated.
///
/// This verifies that when the development agent includes loose text
/// inside the root element but outside any child tags, the tolerant
/// validator ignores it rather than rejecting the response.
#[test]
fn test_development_xml_text_outside_child_tags_produces_error() {
    with_default_timeout(|| {
        // Setup: Create XML with text inside root element but outside child elements
        let xml = r"<ralph-development-result>
Some loose text that shouldn't be here
<ralph-status>completed</ralph-status>
<ralph-summary>Some summary</ralph-summary>
</ralph-development-result>";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

        // Assert: Tolerant validator ignores stray text between elements.
        // Required elements (status, summary) are present and valid.
        assert!(
            result.is_ok(),
            "Tolerant validator should ignore stray text between elements: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
    });
}

/// Test XSD validation error messages include all required information.
///
/// This verifies that XSD validation errors contain the information needed
/// to provide useful feedback to the AI agent for retry.
#[test]
fn test_development_xsd_error_contains_all_required_information() {
    with_default_timeout(|| {
        // Setup: Create invalid XML (missing root element)
        let xml = r"Random text without proper XML";

        // Execute: Try to validate the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

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

        // Verify format_for_ai_retry produces a complete message
        let formatted = error.format_for_ai_retry();
        assert!(
            formatted.contains(&error.element_path),
            "Formatted error should include element_path"
        );
        assert!(
            formatted.contains(&error.expected),
            "Formatted error should include expected"
        );
        assert!(
            formatted.contains(&error.found),
            "Formatted error should include found"
        );
    });
}
