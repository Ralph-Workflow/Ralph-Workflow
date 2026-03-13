//! XSD validation for development result XML format.
//!
//! This module provides validation of XML output against the XSD schema
//! to ensure AI agent output conforms to the expected format for development results.
//!
//! Uses `quick_xml` for robust XML parsing with proper whitespace handling.
//!
//! # Module Organization
//!
//! - [`types`]: Type definitions (`DevelopmentResultElements`)
//! - [`validation`]: XML validation logic (`validate_development_result_xml`)

mod types;
mod validation;

#[cfg(test)]
pub use types::DevelopmentResultElements;
pub use validation::validate_continuation_development_result_xml;
pub use validation::validate_development_result_xml;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_valid_completed() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Fixed all bugs</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
        assert!(elements.is_completed());
        assert!(!elements.is_partial());
        assert!(!elements.is_failed());
    }

    #[test]
    fn test_validate_valid_partial() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Started fixing bugs</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "partial");
        assert!(elements.is_partial());
    }

    #[test]
    fn test_validate_valid_failed() {
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>Could not complete the task</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "failed");
        assert!(elements.is_failed());
    }

    #[test]
    fn test_validate_valid_with_all_optional_fields() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Implemented feature X</ralph-summary>
<ralph-files-changed>- src/main.rs
- src/utils.rs</ralph-files-changed>
<ralph-next-steps>Continue with testing</ralph-next-steps>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
        assert_eq!(elements.summary, "Implemented feature X");
        assert!(elements.files_changed.is_some());
        assert!(elements.files_changed.as_ref().unwrap().contains("main.rs"));
        assert_eq!(
            elements.next_steps,
            Some("Continue with testing".to_string())
        );
    }

    #[test]
    fn test_validate_missing_root_element() {
        let xml = r"Some random text without proper XML tags";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert_eq!(error.element_path, "ralph-development-result");
    }

    #[test]
    fn test_validate_missing_status() {
        let xml = r"<ralph-development-result>
<ralph-summary>No status</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-status"));
    }

    #[test]
    fn test_validate_missing_summary() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-summary"));
    }

    #[test]
    fn test_validate_invalid_status() {
        let xml = r"<ralph-development-result>
<ralph-status>invalid_status_value</ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert!(error.expected.contains("completed"));
    }

    #[test]
    fn test_validate_empty_status() {
        let xml = r"<ralph-development-result>
<ralph-status>   </ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
    }

    #[test]
    fn test_validate_empty_summary() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>   </ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
    }

    #[test]
    fn test_validate_duplicate_status() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-status>partial</ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(result.is_err());
    }

    #[test]
    fn test_validate_unexpected_element_is_now_tolerated() {
        // Unknown elements are now skipped (tolerant behavior) rather than causing errors.
        // The validator should parse successfully and ignore the unknown element.
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Test</ralph-summary>
<ralph-unknown>value</ralph-unknown>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Unknown elements should be tolerated (skipped), not rejected"
        );
    }

    #[test]
    fn test_tolerant_status_synonym_done_maps_to_completed() {
        let xml = r"<ralph-development-result>
<ralph-status>done</ralph-status>
<ralph-summary>Finished all work</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Synonym 'done' should be accepted as 'completed': {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(
            elements.status, "completed",
            "Synonym 'done' should be normalized to 'completed'"
        );
    }

    #[test]
    fn test_tolerant_status_case_insensitive() {
        let xml = r"<ralph-development-result>
<ralph-status>Completed</ralph-status>
<ralph-summary>Done</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Case-insensitive 'Completed' should be accepted: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(
            elements.status, "completed",
            "Case-insensitive 'Completed' should be normalized to lowercase 'completed'"
        );
    }

    #[test]
    fn test_tolerant_skips_unknown_elements() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Test</ralph-summary>
<ralph-analysis>extra info that should be skipped</ralph-analysis>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Unknown elements should be skipped, not rejected: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
    }

    #[test]
    fn test_tolerant_ignores_stray_text() {
        // Stray text between elements should be tolerated
        let xml = "<ralph-development-result>\n  some stray text  \n<ralph-status>completed</ralph-status>\n<ralph-summary>Done</ralph-summary>\n</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Stray text between elements should be tolerated: {result:?}"
        );
    }

    #[test]
    fn test_truly_unknown_status_still_rejected() {
        let xml = r"<ralph-development-result>
<ralph-status>banana</ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Truly unknown status 'banana' should still be rejected"
        );
        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error should reference ralph-status"
        );
    }

    #[test]
    fn test_tolerant_preserves_status_with_incomplete_optional_fields() {
        // Status and summary are present, unknown optional elements are skipped
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done</ralph-summary>
<ralph-unknown-extra>some data</ralph-unknown-extra>
<ralph-another-unknown>more data</ralph-another-unknown>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Unknown optional fields should be skipped: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
        assert_eq!(elements.summary, "Done");
    }

    #[test]
    fn test_validate_whitespace_handling() {
        // This is the key test - quick_xml should handle whitespace between elements
        let xml = "  <ralph-development-result>  \n  <ralph-status>completed</ralph-status>  \n  <ralph-summary>Test</ralph-summary>  \n  </ralph-development-result>  ";

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
    }

    #[test]
    fn test_validate_with_xml_declaration() {
        let xml = r#"<?xml version="1.0"?>
<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Test</ralph-summary>
</ralph-development-result>"#;

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
    }

    #[test]
    fn test_validate_cdata_wrapped_xml() {
        let xml = r#"<![CDATA[<?xml version="1.0"?>
<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done</ralph-summary>
</ralph-development-result>]]>"#;

        let result = validate_development_result_xml(xml);
        assert!(result.is_ok());
    }

    // =========================================================================
    // Additional status synonym tests
    // =========================================================================

    #[test]
    fn test_tolerant_status_synonym_succeeded_maps_to_completed() {
        let xml = r"<ralph-development-result>
<ralph-status>succeeded</ralph-status>
<ralph-summary>All work completed successfully</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Synonym 'succeeded' should be accepted as 'completed': {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
    }

    #[test]
    fn test_tolerant_status_synonym_finished_maps_to_completed() {
        let xml = r"<ralph-development-result>
<ralph-status>finished</ralph-status>
<ralph-summary>Task is finished</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Synonym 'finished' should be accepted as 'completed': {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
    }

    #[test]
    fn test_tolerant_status_synonym_failure_maps_to_failed() {
        let xml = r"<ralph-development-result>
<ralph-status>failure</ralph-status>
<ralph-summary>Task failed due to error</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Synonym 'failure' should be accepted as 'failed': {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "failed");
    }

    #[test]
    fn test_tolerant_status_synonym_in_progress_hyphen_maps_to_partial() {
        let xml = r"<ralph-development-result>
<ralph-status>in-progress</ralph-status>
<ralph-summary>Task is in progress</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Synonym 'in-progress' should be accepted as 'partial': {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "partial");
    }

    // =========================================================================
    // Self-closing unknown element tolerance tests
    // =========================================================================

    #[test]
    fn test_tolerant_skips_self_closing_unknown_element() {
        // Self-closing unknown elements (Event::Empty) should also be skipped
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done</ralph-summary>
<ralph-unknown/>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Self-closing unknown elements should be skipped, not rejected: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
    }

    #[test]
    fn test_tolerant_skips_multiple_self_closing_unknown_elements() {
        // Multiple self-closing unknown elements should all be skipped
        let xml = r"<ralph-development-result>
<ralph-meta/>
<ralph-status>completed</ralph-status>
<ralph-extra/>
<ralph-summary>Done</ralph-summary>
<ralph-tag/>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Multiple self-closing unknown elements should be skipped: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
    }

    // =========================================================================
    // Element reordering tolerance tests
    // =========================================================================

    #[test]
    fn test_tolerant_element_reordering_summary_before_status() {
        // summary before status should still parse correctly
        let xml = r"<ralph-development-result>
<ralph-summary>Task completed successfully</ralph-summary>
<ralph-status>completed</ralph-status>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Element reordering (summary before status) should be tolerated: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
        assert_eq!(elements.summary, "Task completed successfully");
    }

    // =========================================================================
    // Multiple unknown elements interspersed tests
    // =========================================================================

    #[test]
    fn test_tolerant_multiple_unknown_elements_interspersed() {
        // Multiple unknown elements interspersed between known elements should be skipped
        let xml = r"<ralph-development-result>
<ralph-preamble>Some preamble</ralph-preamble>
<ralph-status>completed</ralph-status>
<ralph-rationale>Some rationale</ralph-rationale>
<ralph-summary>Done</ralph-summary>
<ralph-conclusion>Some conclusion</ralph-conclusion>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Multiple unknown elements interspersed should be skipped: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
        assert_eq!(elements.summary, "Done");
    }

    // =========================================================================
    // Empty self-closing status element is rejected
    // =========================================================================

    #[test]
    fn test_tolerant_empty_self_closing_status_rejected() {
        // An empty self-closing status element (<ralph-status/>) should be rejected
        // because status is required and must have a value
        let xml = r"<ralph-development-result>
<ralph-status/>
<ralph-summary>Done</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Empty self-closing status element should be rejected (status is required)"
        );
        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error should reference ralph-status, got: {}",
            error.element_path
        );
    }

    #[test]
    fn test_continuation_validation_rejects_single_recovery_step() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because verification still fails.</ralph-summary>
<ralph-next-steps>1. Fix the blocker.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert_eq!(error.element_path, "ralph-next-steps");
        assert!(
            error.expected.contains("ordered recovery checklist"),
            "single-step continuation checklist should be rejected as incomplete"
        );
    }

    #[test]
    fn test_continuation_validation_rejects_checklist_without_plan_completion_step() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because verification still fails.</ralph-summary>
<ralph-next-steps>1. Fix the failing verification.
2. Re-run the focused continuation tests.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert_eq!(error.element_path, "ralph-next-steps");
        assert!(
            error.expected.contains("remaining plan")
                || error.suggestion.contains("remaining plan"),
            "continuation checklist should explicitly cover finishing the remaining plan"
        );
    }

    #[test]
    fn test_continuation_validation_accepts_full_recovery_checklist() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because verification still fails.</ralph-summary>
<ralph-next-steps>1. Fix the failing verification.
2. Re-run the focused continuation tests.
3. Finish the remaining plan and run repository verification.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(result.is_ok());
    }
}
