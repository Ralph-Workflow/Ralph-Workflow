//! Tests for edge cases, malformed XML, error message quality, and depends-on.
//!
//! These tests verify that the parser handles edge cases, invalid XML gracefully,
//! provides actionable error messages, and handles step dependencies correctly.

use super::*;
use crate::files::llm_output_extraction::xsd_validation::XsdErrorType;

// ═══════════════════════════════════════════════════════════════════════════
// EDGE CASES AND MALFORMED XML TESTS
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn test_empty_xml() {
    let result = validate_plan_xml("");
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert_eq!(err.error_type, XsdErrorType::MissingRequiredElement);
}

#[test]
fn test_whitespace_only_xml() {
    let result = validate_plan_xml("   \n\t  \n  ");
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert_eq!(err.error_type, XsdErrorType::MissingRequiredElement);
}

#[test]
fn test_unclosed_tag() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Test</title>
<content><paragraph>Test</paragraph></content>
</ralph-implementation-steps>"#;

    let result = validate_plan_xml(xml);
    assert!(result.is_err());
}

#[test]
fn test_xml_with_preamble_text() {
    let xml = r#"Here is the plan:

<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Test</title>
<content><paragraph>Test</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="test.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>R</risk><mitigation>M</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>M</method><expected-outcome>O</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    // The validator should handle preamble text gracefully
    let result = validate_plan_xml(xml);
    // This may fail or succeed depending on implementation - we just want no panic
    // If it fails, the error should be meaningful
    if let Err(err) = &result {
        assert!(!err.suggestion.is_empty());
    }
}

#[test]
fn test_missing_context_in_summary() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Test</title>
<content><paragraph>Test</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="test.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>R</risk><mitigation>M</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>M</method><expected-outcome>O</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(err.element_path.contains("context"));
}

#[test]
fn test_missing_scope_items_in_summary() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test context</context>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Test</title>
<content><paragraph>Test</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="test.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>R</risk><mitigation>M</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>M</method><expected-outcome>O</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(err.element_path.contains("scope-items"));
}

// ═══════════════════════════════════════════════════════════════════════════
// ERROR MESSAGE QUALITY TESTS
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn test_error_message_includes_element_path() {
    let xml = r"<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item>item 1</scope-item>
</scope-items>
</ralph-summary>
</ralph-plan>";

    let result = validate_plan_xml(xml);
    assert!(result.is_err());
    let err = result.unwrap_err();
    let retry_msg = err.format_for_ai_retry();

    // Error message should be specific and actionable
    assert!(retry_msg.contains("scope-items"));
    assert!(retry_msg.contains("How to fix"));
}

#[test]
fn test_error_message_includes_what_was_found() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item>one</scope-item>
<scope-item>two</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Test</title>
<content><paragraph>Test</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="test.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>R</risk><mitigation>M</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>M</method><expected-outcome>O</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(result.is_err());
    let err = result.unwrap_err();

    // Should tell us what was found (2 items)
    assert!(err.found.contains('2'));
    // Should tell us what was expected (3 minimum)
    assert!(err.expected.contains('3'));
}

#[test]
fn test_file_change_step_without_target_files_is_reclassified_as_action() {
    // Previously tested error messages for file-change without target-files.
    // Now that behavior is tolerant (reclassify as action), test the new behavior.
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change">
<title>Test</title>
<content><paragraph>Test</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="test.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>R</risk><mitigation>M</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>M</method><expected-outcome>O</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "file-change step without target-files should be reclassified as action: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(
        plan.steps[0].kind,
        StepType::Action,
        "step should be reclassified as action type"
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// DEPENDS-ON TESTS
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn test_step_with_multiple_dependencies() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Step 1</title>
<content><paragraph>First</paragraph></content>
</step>
<step number="2" type="action">
<title>Step 2</title>
<content><paragraph>Second</paragraph></content>
</step>
<step number="3" type="action">
<title>Step 3</title>
<content><paragraph>Third - depends on 1 and 2</paragraph></content>
<depends-on step="1"/>
<depends-on step="2"/>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="test.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>R</risk><mitigation>M</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>M</method><expected-outcome>O</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(result.is_ok(), "Error: {:?}", result.err());
    let plan = result.unwrap();
    assert_eq!(plan.steps[2].depends_on, vec![1, 2]);
}

#[test]
fn test_mixed_explicit_and_implicit_step_numbers_keep_dependency_targets_stable() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step type="action">
<title>First implicit step</title>
<content><paragraph>First</paragraph></content>
</step>
<step number="1" type="action">
<title>Explicit first step</title>
<content><paragraph>Second</paragraph></content>
<depends-on step="1"/>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="test.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>R</risk><mitigation>M</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>M</method><expected-outcome>O</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(result.is_ok(), "Error: {:?}", result.err());
    let plan = result.unwrap();

    assert_eq!(
        plan.steps[0].number, 2,
        "implicit step should be renumbered after explicit 1"
    );
    assert_eq!(plan.steps[1].number, 1);
    assert_eq!(
        plan.steps[1].depends_on,
        vec![2],
        "depends-on should continue to reference the original implicit predecessor"
    );
}

#[test]
fn test_step_optional_fields() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Complete step</title>
<target-files>
<file path="src/main.rs" action="modify"/>
</target-files>
<location>After the imports section</location>
<rationale>This change is needed because...</rationale>
<content><paragraph>Detailed description</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="test.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>R</risk><mitigation>M</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>M</method><expected-outcome>O</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(result.is_ok(), "Error: {:?}", result.err());
    let plan = result.unwrap();
    assert_eq!(
        plan.steps[0].location,
        Some("After the imports section".to_string())
    );
    assert_eq!(
        plan.steps[0].rationale,
        Some("This change is needed because...".to_string())
    );
}
