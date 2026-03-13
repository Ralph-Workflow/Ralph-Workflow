//! Integration tests for plan XML validation tolerant parsing.
//!
//! These tests verify tolerant parsing behavior through the public API boundary,
//! complementing the unit tests in the plan validation module.
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

/// Test that valid plan with synonym enum values parses successfully.
///
/// This verifies that the tolerant parsing allows synonym values
/// like action="add", type="code", priority="p0" in the full public API flow.
#[test]
fn test_plan_xml_valid_plan_with_synonym_enum_values() {
    with_default_timeout(|| {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Implement XML tolerance for all validators</context>
<scope-items>
<scope-item count="5" category="synonym-tables">new synonym tables</scope-item>
<scope-item count="4" category="validator-updates">validator updates</scope-item>
<scope-item count="30" category="tests">unit and integration tests</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="code" priority="p0">
<title>Add synonym tables</title>
<target-files>
<file path="tolerant_parsing.rs" action="add"/>
</target-files>
<content>
<paragraph>Add five new synonym tables.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="tolerant_parsing.rs" action="update"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="urgent">
<risk>Ambiguous synonym mappings</risk>
<mitigation>Only add unambiguous mappings</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>cargo xtask verify</method>
<expected-outcome>All checks pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let result = ralph_workflow::validate_plan_xml(xml);
        assert!(
            result.is_ok(),
            "Plan with synonym enum values should parse successfully: {:?}",
            result.err()
        );

        let plan = result.unwrap();
        assert_eq!(plan.steps.len(), 1, "Should have one step");
        assert_eq!(plan.steps[0].number, 1, "Step should be numbered 1");
        assert!(
            !plan.steps[0].title.is_empty(),
            "Step title should not be empty"
        );
    });
}

/// Test that plan with stray text between sections still parses.
///
/// This verifies that non-essential text between XML elements
/// is ignored rather than causing a parse failure.
#[test]
fn test_plan_xml_stray_text_between_sections_is_ignored() {
    with_default_timeout(|| {
        let xml = r#"<ralph-plan>
Here is some stray text that LLM may emit.
<ralph-summary>
<context>Test plan context</context>
<scope-items>
<scope-item count="1" category="files">file one</scope-item>
<scope-item count="1" category="tests">test one</scope-item>
<scope-item count="1" category="features">feature one</scope-item>
</scope-items>
</ralph-summary>
And more stray text here between sections.
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Implement feature</title>
<target-files>
<file path="src/main.rs" action="modify"/>
</target-files>
<content>
<paragraph>Implementation details.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/main.rs" action="modify"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>Breaking changes</risk>
<mitigation>Add tests</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>Run tests</method>
<expected-outcome>All pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let result = ralph_workflow::validate_plan_xml(xml);
        assert!(
            result.is_ok(),
            "Plan with stray text between sections should still parse: {:?}",
            result.err()
        );
    });
}

/// Test that plan with unknown extra elements still parses.
///
/// This verifies that unknown XML elements are skipped rather than
/// causing a parse failure.
#[test]
fn test_plan_xml_unknown_extra_elements_are_skipped() {
    with_default_timeout(|| {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-extra-unknown-section>This should be skipped entirely</ralph-extra-unknown-section>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<some-unknown-child>Should be skipped</some-unknown-child>
<content>
<paragraph>Details.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/foo.rs" action="modify"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>Risk</risk>
<mitigation>Mitigation</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>Run tests</method>
<expected-outcome>All pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let result = ralph_workflow::validate_plan_xml(xml);
        assert!(
            result.is_ok(),
            "Plan with unknown extra elements should still parse: {:?}",
            result.err()
        );

        let plan = result.unwrap();
        assert_eq!(plan.steps.len(), 1, "Should have exactly one step");
    });
}

/// Test that plan missing required sections still fails.
///
/// This verifies that tolerance only applies to non-essential structure,
/// and required elements are still enforced.
#[test]
fn test_plan_xml_missing_required_sections_still_fails() {
    with_default_timeout(|| {
        // Plan missing ralph-summary
        let xml = r#"<ralph-plan>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<content>
<paragraph>Details.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/foo.rs" action="modify"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>Risk</risk>
<mitigation>Mitigation</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>Run tests</method>
<expected-outcome>All pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let result = ralph_workflow::validate_plan_xml(xml);
        assert!(
            result.is_err(),
            "Plan missing required summary section should still fail"
        );
    });
}
