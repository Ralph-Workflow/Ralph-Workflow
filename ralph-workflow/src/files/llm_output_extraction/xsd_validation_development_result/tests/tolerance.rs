//! Tolerance tests for XSD validation of development result XML format.

#[cfg(test)]
use crate::validate_development_result_xml;

#[test]
fn test_validate_unexpected_element_is_now_tolerated() {
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
    let xml = "<ralph-development-result>\n  some stray text  \n<ralph-status>completed</ralph-status>\n<ralph-summary>Done</ralph-summary>\n</ralph-development-result>";

    let result = validate_development_result_xml(xml);
    assert!(
        result.is_ok(),
        "Stray text between elements should be tolerated: {result:?}"
    );
}

#[test]
fn test_tolerant_preserves_status_with_incomplete_optional_fields() {
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

#[test]
fn test_tolerant_skips_self_closing_unknown_element() {
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

#[test]
fn test_tolerant_element_reordering_summary_before_status() {
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

#[test]
fn test_tolerant_multiple_unknown_elements_interspersed() {
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
    assert_eq!(elements.summary, "Actual summary");
}
