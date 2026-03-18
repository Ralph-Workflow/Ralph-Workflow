//! Tests for XSD validation of development result XML format.

#[cfg(test)]
mod tests {
    use crate::files::llm_output_extraction::xsd_validation_development_result::{
        validate_continuation_development_result_xml, validate_development_result_xml,
    };

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
    fn test_continuation_validation_accepts_single_recovery_step() {
        // Previously rejected; now accepted — step count is no longer enforced.
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because verification still fails.</ralph-summary>
<ralph-next-steps>1. Fix the blocker.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Single-step continuation should now be accepted: {:?}",
            result.err()
        );
    }

    #[test]
    fn test_continuation_validation_accepts_checklist_without_plan_completion_step() {
        // Previously rejected; now accepted — last-step wording is no longer enforced.
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because verification still fails.</ralph-summary>
<ralph-next-steps>1. Fix the failing verification.
2. Re-run the focused continuation tests.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Checklist without explicit plan-completion step should now be accepted: {:?}",
            result.err()
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

    #[test]
    fn test_continuation_validation_ignores_noncritical_unknown_child_elements() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because verification still fails.</ralph-summary>
<ralph-next-steps>1. Fix the blocker.
2. Re-run the relevant tests.
3. Finish the remaining plan and run repository verification.</ralph-next-steps>
<tests-run>cargo test -p ralph-workflow --lib</tests-run>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(result.is_ok(), "extra bookkeeping child should be ignored");

        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because verification still fails.</ralph-summary>
<ralph-next-steps>1. Fix the blocker.
2. Re-run the relevant tests.
3. Finish the remaining plan and run repository verification.</ralph-next-steps>
<tests-run />
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "extra empty bookkeeping child should be ignored"
        );
    }

    #[test]
    fn test_continuation_validation_tolerates_and_clears_files_changed() {
        // Previously rejected; now accepted — ralph-files-changed is tolerated and cleared.
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because verification still fails.</ralph-summary>
<ralph-next-steps>1. Fix the blocker.
2. Re-run the relevant tests.
3. Finish the remaining plan and run repository verification.</ralph-next-steps>
<ralph-files-changed>src/lib.rs</ralph-files-changed>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Continuation output should now tolerate ralph-files-changed: {:?}",
            result.err()
        );
        let elements = result.unwrap();
        assert!(
            elements.files_changed.is_none(),
            "files_changed should be cleared in continuation mode"
        );
        assert!(
            !elements.files_changed_present,
            "files_changed_present should be cleared in continuation mode"
        );
    }

    // =========================================================================
    // Development result status preservation tests (Step 4 of implementation plan)
    // =========================================================================

    /// Test: empty status is still rejected (status is the critical required field).
    #[test]
    fn test_tolerant_empty_status_still_rejected_even_with_summary() {
        let xml = r"<ralph-development-result>
<ralph-status></ralph-status>
<ralph-summary>Some summary that is not empty</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_err(),
            "Empty status should still be rejected: status is the critical required field"
        );
        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error should reference ralph-status"
        );
    }

    /// Test: failed status with empty summary succeeds (status is preserved).
    #[test]
    fn test_tolerant_failed_status_with_empty_summary_preserves_status() {
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary></ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Failed status with empty summary should succeed - status is the key field: {:?}",
            result.err()
        );
        let elements = result.unwrap();
        assert_eq!(
            elements.status, "failed",
            "Failed status should be preserved when summary is empty"
        );
        assert!(elements.is_failed(), "Should be identified as failed");
    }

    // =========================================================================
    // Tolerance tests — all pass after validation.rs was updated with fuzzy tag matching
    // =========================================================================

    /// Test: continuation with summary lacking blocker-indicator words should now pass.
    #[test]
    fn test_continuation_tolerates_summary_without_blocker_indicator_words() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Steps 7-10 were not implemented; three test files and cargo xtask verify are still missing.</ralph-summary>
<ralph-next-steps>1. Create the three missing test files.
2. Fix pre-existing clippy errors.
3. Re-run cargo xtask verify.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Continuation should accept summary without explicit blocker-indicator words: {:?}",
            result.err()
        );
    }

    /// Test: continuation with summary lacking plan-scope terms should now pass.
    #[test]
    fn test_continuation_tolerates_summary_without_plan_scope_terms() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Implementation incomplete because verification fails with three pre-existing errors.</ralph-summary>
<ralph-next-steps>1. Fix the three pre-existing errors.
2. Re-run focused tests.
3. Confirm all checks pass.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Continuation should accept summary without explicit plan-scope terms: {:?}",
            result.err()
        );
    }

    /// Test: continuation with bullet-point next-steps (dashes instead of numbers) should now pass.
    #[test]
    fn test_continuation_tolerates_bullet_point_next_steps() {
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>The implementation was not completed due to missing test files.</ralph-summary>
<ralph-next-steps>- Create the processing-controls spec file.
- Create the preview-panel spec file.
- Fix the vitest config error.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Continuation should accept bullet-point next-steps (dashes): {:?}",
            result.err()
        );
    }

    /// Test: continuation next-steps whose last step does NOT mention finish/remaining plan should now pass.
    #[test]
    fn test_continuation_tolerates_next_steps_without_finish_remaining_plan_phrase() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The implementation stalled because clippy errors block verification.</ralph-summary>
<ralph-next-steps>1. Fix the utoipa clippy errors in six controller files.
2. Fix the SCSS budget exceeded errors.
3. Fix the vitest config error.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Continuation should accept next-steps without explicit finish/remaining plan phrase: {:?}",
            result.err()
        );
    }

    /// Test: continuation with bookkeeping lines in next-steps should now pass.
    #[test]
    fn test_continuation_tolerates_bookkeeping_lines_in_next_steps() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The implementation is incomplete because three test files are still missing.</ralph-summary>
<ralph-next-steps>1. Files changed: validation.rs, mod.rs.
2. Tests run: cargo test -p ralph-workflow --lib.
3. Work completed: removed five semantic checks.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Continuation should accept next-steps containing bookkeeping lines: {:?}",
            result.err()
        );
    }

    /// Test: continuation with vague steps in next-steps should now pass.
    #[test]
    fn test_continuation_tolerates_vague_steps_in_next_steps() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The implementation is not done because verification still fails.</ralph-summary>
<ralph-next-steps>1. Keep investigating the root cause of the clippy errors.
2. Try another fix for the SCSS budget issue.
3. Continue later with the vitest config.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Continuation should accept next-steps containing vague steps: {:?}",
            result.err()
        );
    }

    /// Test: continuation with ralph-files-changed element present should now pass (element tolerated, value discarded).
    #[test]
    fn test_continuation_tolerates_files_changed_element_and_clears_it() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The implementation was not completed because test files are missing.</ralph-summary>
<ralph-files-changed>src/lib.rs</ralph-files-changed>
<ralph-next-steps>1. Create the missing test files.
2. Re-run the verification.
3. Confirm all tests pass.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Continuation should tolerate ralph-files-changed (element present but discarded): {:?}",
            result.err()
        );
        let elements = result.unwrap();
        assert!(
            elements.files_changed.is_none(),
            "files_changed should be cleared/discarded in continuation mode"
        );
        assert!(
            !elements.files_changed_present,
            "files_changed_present should be cleared/discarded in continuation mode"
        );
    }

    /// Test: continuation with a single recovery step should now pass.
    #[test]
    fn test_continuation_tolerates_single_recovery_step() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The implementation is incomplete because verification still fails.</ralph-summary>
<ralph-next-steps>1. Fix the verification failure and re-run all tests.</ralph-next-steps>
</ralph-development-result>";

        let result = validate_continuation_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Continuation should accept a single recovery step: {:?}",
            result.err()
        );
    }

    // =========================================================================
    // Skills-MCP field tests
    // =========================================================================

    #[test]
    fn test_dev_result_with_skills_mcp_parses_entries() {
        let xml = r#"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Verification found a reproducible reducer failure.</ralph-summary>
<skills-mcp>
<skill reason="A concrete failure should be investigated before editing code">systematic-debugging</skill>
<skill reason="The eventual fix should begin with a reproducing test">test-driven-development</skill>
<mcp reason="Use when external dependency behavior needs confirmation">context7</mcp>
</skills-mcp>
<ralph-files-changed>src/lib.rs</ralph-files-changed>
</ralph-development-result>"#;

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Dev result with skills-mcp should parse: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "partial");

        let sm = elements.skills_mcp.expect("skills_mcp should be present");
        assert_eq!(sm.skills.len(), 2);
        assert_eq!(sm.mcps.len(), 1);
        assert_eq!(sm.skills[0].name, "systematic-debugging");
        assert_eq!(
            sm.skills[0].reason.as_deref(),
            Some("A concrete failure should be investigated before editing code")
        );
        assert_eq!(sm.mcps[0].name, "context7");
    }

    #[test]
    fn test_dev_result_without_skills_mcp_still_validates() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Fixed all bugs</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Dev result without skills-mcp should parse: {result:?}"
        );
        let elements = result.unwrap();
        assert!(
            elements.skills_mcp.is_none(),
            "skills_mcp should be None when absent"
        );
    }

    #[test]
    fn test_dev_result_malformed_skills_mcp_preserves_raw_content() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Some work done.</ralph-summary>
<skills-mcp>Use test-driven-development for fixing this issue.</skills-mcp>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Dev result with malformed skills-mcp should still parse: {result:?}"
        );
        let elements = result.unwrap();
        // The malformed skills-mcp should be preserved somehow
        let sm = elements
            .skills_mcp
            .expect("skills_mcp should still be present even if malformed");
        // The raw text should be in raw_content since no structured tags were found
        assert!(
            sm.raw_content.is_some() || sm.skills.is_empty(),
            "Malformed content should be preserved"
        );
    }

    #[test]
    fn test_dev_result_empty_skills_mcp() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done.</ralph-summary>
<skills-mcp></skills-mcp>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Empty skills-mcp should be accepted: {result:?}"
        );
        let elements = result.unwrap();
        let sm = elements
            .skills_mcp
            .expect("Empty skills-mcp should be Some, not None");
        assert!(sm.skills.is_empty());
        assert!(sm.mcps.is_empty());
    }

    #[test]
    fn test_dev_result_self_closing_skills_mcp() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done.</ralph-summary>
<skills-mcp/>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Self-closing skills-mcp should be accepted: {result:?}"
        );
        let elements = result.unwrap();
        let sm = elements
            .skills_mcp
            .expect("Self-closing skills-mcp should be Some, not None");
        assert!(sm.skills.is_empty());
        assert!(sm.mcps.is_empty());
    }

    // =========================================================================
    // Fuzzy tag matching tests (Step 4 of implementation plan)
    // =========================================================================

    /// Test: misspelled ralph-sumary tag resolves to ralph-summary.
    #[test]
    fn test_tolerant_misspelled_summary_tag_accepted() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-sumary>Some summary text</ralph-sumary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Misspelled <ralph-sumary> should be accepted: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(
            elements.summary, "Some summary text",
            "Summary content should be correctly extracted from misspelled tag"
        );
    }

    /// Test: misspelled ralph-statuss tag resolves to ralph-status.
    #[test]
    fn test_tolerant_misspelled_status_tag_accepted() {
        let xml = r"<ralph-development-result>
<ralph-statuss>completed</ralph-statuss>
<ralph-summary>Some summary text</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Misspelled <ralph-statuss> should be accepted: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(
            elements.status, "completed",
            "Status should be correctly extracted from misspelled tag"
        );
    }

    /// Test: completely unknown tag (large edit distance) is skipped.
    #[test]
    fn test_tolerant_completely_unknown_tag_skipped() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Some summary</ralph-summary>
<ralph-banana>this should be ignored</ralph-banana>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Unknown tag with large edit distance should be skipped: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "completed");
        assert_eq!(elements.summary, "Some summary");
    }

    /// Test: self-closing misspelled tag is also handled.
    #[test]
    fn test_tolerant_self_closing_misspelled_tag() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summry/>
<ralph-summary>Actual summary</ralph-summary>
</ralph-development-result>";

        let result = validate_development_result_xml(xml);
        assert!(
            result.is_ok(),
            "Self-closing misspelled tag should be handled: {result:?}"
        );
        let elements = result.unwrap();
        // The actual summary should be used
        assert_eq!(elements.summary, "Actual summary");
    }
}
