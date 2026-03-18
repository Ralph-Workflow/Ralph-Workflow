//! Continuation validation tests for XSD validation of development result XML format.

use crate::files::llm_output_extraction::xsd_validation_development_result::validate_continuation_development_result_xml;

#[test]
fn test_continuation_validation_accepts_single_recovery_step() {
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
