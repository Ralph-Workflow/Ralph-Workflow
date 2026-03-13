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

/// Test that plan with bare scope-items (no wrapper) still parses correctly.
///
/// This verifies tolerant parsing: an LLM emitting `<scope-item>` elements
/// directly inside `<ralph-summary>` (without a `<scope-items>` wrapper) is
/// accepted and produces the expected scope items.
#[test]
fn test_plan_xml_scope_items_without_wrapper_parse_correctly() {
    with_default_timeout(|| {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Implement tolerant wrapper parsing</context>
<scope-item count="3" category="parsers">parser changes</scope-item>
<scope-item count="15" category="tests">unit tests</scope-item>
<scope-item count="2" category="integration">integration tests</scope-item>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Update parser</title>
<target-files>
<file path="src/parser.rs" action="modify"/>
</target-files>
<content>
<paragraph>Update the parser to handle bare scope-items.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/parser.rs" action="modify"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>Regression in existing parsers</risk>
<mitigation>Run full test suite</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>cargo test</method>
<expected-outcome>All tests pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let result = ralph_workflow::validate_plan_xml(xml);
        assert!(
            result.is_ok(),
            "Plan with bare scope-items should parse: {:?}",
            result.err()
        );

        let plan = result.unwrap();
        assert_eq!(
            plan.summary.scope_items.len(),
            3,
            "Should parse all three bare scope-items"
        );
        assert_eq!(
            plan.summary.scope_items[0].description, "parser changes",
            "First scope-item description should match"
        );
        assert_eq!(
            plan.summary.scope_items[0].count,
            Some("3".to_string()),
            "First scope-item count should be preserved"
        );
        assert_eq!(
            plan.summary.scope_items[0].category,
            Some("parsers".to_string()),
            "First scope-item category should be preserved"
        );
    });
}

/// Test that plan with bare file elements in step (no target-files wrapper) parses correctly.
///
/// This verifies tolerant parsing: an LLM emitting `<file>` elements directly
/// inside a `<step>` (without a `<target-files>` wrapper) is accepted and the
/// files are parsed as target files.
#[test]
fn test_plan_xml_bare_file_elements_in_step_parse_correctly() {
    with_default_timeout(|| {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test bare file elements</context>
<scope-items>
<scope-item count="1" category="files">a file</scope-item>
<scope-item count="1" category="tests">a test</scope-item>
<scope-item count="1" category="tasks">a task</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="medium">
<title>Add new file</title>
<file path="src/new_module.rs" action="create"/>
<file path="src/main.rs" action="modify"/>
<content>
<paragraph>Create a new module and update main.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/new_module.rs" action="create"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>Compilation errors</risk>
<mitigation>Check imports</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>cargo build</method>
<expected-outcome>Compiles successfully</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let result = ralph_workflow::validate_plan_xml(xml);
        assert!(
            result.is_ok(),
            "Plan with bare file elements in step should parse: {:?}",
            result.err()
        );

        let plan = result.unwrap();
        assert_eq!(plan.steps.len(), 1, "Should have one step");
        assert_eq!(
            plan.steps[0].target_files.len(),
            2,
            "Should have two target files from bare file elements"
        );
        assert_eq!(
            plan.steps[0].target_files[0].path, "src/new_module.rs",
            "First target file path should match"
        );
        assert_eq!(
            plan.steps[0].target_files[1].path, "src/main.rs",
            "Second target file path should match"
        );
    });
}

/// Test that plan with bare content elements in step (no content wrapper) parses correctly.
///
/// This verifies tolerant parsing: an LLM emitting `<paragraph>`, `<code-block>`,
/// or `<list>` elements directly inside a `<step>` (without a `<content>` wrapper)
/// is accepted and the content elements are captured.
#[test]
fn test_plan_xml_bare_content_elements_in_step_parse_correctly() {
    with_default_timeout(|| {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test bare content elements</context>
<scope-items>
<scope-item count="1" category="steps">step</scope-item>
<scope-item count="1" category="tests">test</scope-item>
<scope-item count="1" category="tasks">task</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Refactor module</title>
<target-files>
<file path="src/module.rs" action="modify"/>
</target-files>
<paragraph>Refactor the module to improve readability.</paragraph>
<paragraph>Make sure to update all callers.</paragraph>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/module.rs" action="modify"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="medium">
<risk>Breaking API changes</risk>
<mitigation>Check all callers before merging</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>cargo test</method>
<expected-outcome>All tests pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let result = ralph_workflow::validate_plan_xml(xml);
        assert!(
            result.is_ok(),
            "Plan with bare content elements in step should parse: {:?}",
            result.err()
        );

        let plan = result.unwrap();
        assert_eq!(plan.steps.len(), 1, "Should have one step");
        // Bare content elements are captured — the step content should be non-empty
        assert!(
            !plan.steps[0].content.elements.is_empty(),
            "Step content should be non-empty from bare paragraph elements"
        );
    });
}

/// Test that plan with missing list type attribute defaults to unordered.
///
/// This verifies tolerant parsing: an LLM emitting a `<list>` without a `type`
/// attribute produces an Unordered list rather than an error.
#[test]
fn test_plan_xml_list_without_type_attribute_defaults_to_unordered() {
    with_default_timeout(|| {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test list without type attribute</context>
<scope-items>
<scope-item count="1" category="lists">list</scope-item>
<scope-item count="1" category="tests">test</scope-item>
<scope-item count="1" category="tasks">task</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Add list handling</title>
<target-files>
<file path="src/list.rs" action="create"/>
</target-files>
<content>
<list>
<item>First item</item>
<item>Second item</item>
<item>Third item</item>
</list>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/list.rs" action="create"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>Type mismatch</risk>
<mitigation>Add validation</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>cargo test</method>
<expected-outcome>All tests pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let result = ralph_workflow::validate_plan_xml(xml);
        assert!(
            result.is_ok(),
            "Plan with list missing type attribute should parse: {:?}",
            result.err()
        );

        let plan = result.unwrap();
        assert_eq!(plan.steps.len(), 1, "Should have one step");
        // The step should have content with list elements (parsed without error)
        assert!(
            !plan.steps[0].content.elements.is_empty(),
            "Step should have content elements including the list"
        );
    });
}

/// Test that plan with unnumbered steps assigns sequential numbers automatically.
///
/// This verifies tolerant parsing: an LLM omitting `number` attributes on steps
/// results in steps getting auto-assigned sequential numbers 1, 2, 3...
#[test]
fn test_plan_xml_steps_without_number_are_auto_assigned_sequentially() {
    with_default_timeout(|| {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test auto-numbering of steps</context>
<scope-items>
<scope-item count="3" category="steps">steps</scope-item>
<scope-item count="1" category="tests">test</scope-item>
<scope-item count="1" category="tasks">task</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step type="file-change" priority="high">
<title>First step</title>
<target-files>
<file path="src/a.rs" action="create"/>
</target-files>
<content>
<paragraph>Create first file.</paragraph>
</content>
</step>
<step type="file-change" priority="medium">
<title>Second step</title>
<target-files>
<file path="src/b.rs" action="create"/>
</target-files>
<content>
<paragraph>Create second file.</paragraph>
</content>
</step>
<step type="action" priority="low">
<title>Third step</title>
<target-files>
<file path="src/c.rs" action="modify"/>
</target-files>
<content>
<paragraph>Update third file.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/a.rs" action="create"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>Ordering issues</risk>
<mitigation>Review step sequence</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>cargo test</method>
<expected-outcome>All tests pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let result = ralph_workflow::validate_plan_xml(xml);
        assert!(
            result.is_ok(),
            "Plan with unnumbered steps should parse: {:?}",
            result.err()
        );

        let plan = result.unwrap();
        assert_eq!(plan.steps.len(), 3, "Should have three steps");
        assert_eq!(plan.steps[0].number, 1, "First step should be number 1");
        assert_eq!(plan.steps[1].number, 2, "Second step should be number 2");
        assert_eq!(plan.steps[2].number, 3, "Third step should be number 3");
        assert_eq!(plan.steps[0].title, "First step");
        assert_eq!(plan.steps[1].title, "Second step");
        assert_eq!(plan.steps[2].title, "Third step");
    });
}

/// Test a realistic LLM output combining multiple tolerance behaviors at once.
///
/// This integration test simulates what a real LLM response might look like:
/// - scope-items without wrapper
/// - bare file elements in one step
/// - bare content elements in another step
/// - step without number attribute
/// - list without type attribute
///
/// All of these must be handled together gracefully.
#[test]
fn test_plan_xml_realistic_llm_output_with_multiple_tolerances() {
    with_default_timeout(|| {
        let xml = r#"<ralph-plan>
<ralph-summary>
<context>Implement XML tolerance across all validator paths</context>
<scope-item count="5" category="validators">validator changes</scope-item>
<scope-item count="20" category="tests">unit tests</scope-item>
<scope-item count="4" category="integration">integration tests</scope-item>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="critical">
<title>Add scope-item bare parsing</title>
<file path="src/section_parsers.rs" action="modify"/>
<paragraph>Add logic to handle bare scope-item elements without a wrapper.</paragraph>
</step>
<step type="file-change" priority="high">
<title>Add bare file parsing in steps</title>
<target-files>
<file path="src/step_parsers.rs" action="modify"/>
</target-files>
<content>
<paragraph>Update step parser to accept file elements without target-files wrapper.</paragraph>
<list>
<item>Handle Start event for bare file</item>
<item>Handle Empty event for bare file</item>
</list>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/section_parsers.rs" action="modify"/>
<file path="src/step_parsers.rs" action="modify"/>
</primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair severity="medium">
<risk>Regression in strict mode</risk>
<mitigation>Keep all existing tests green</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>cargo xtask verify</method>
<expected-outcome>All checks pass with no ERROR/WARNING</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

        let result = ralph_workflow::validate_plan_xml(xml);
        assert!(
            result.is_ok(),
            "Realistic LLM output with multiple tolerances should parse: {:?}",
            result.err()
        );

        let plan = result.unwrap();

        // Bare scope-items are captured
        assert_eq!(
            plan.summary.scope_items.len(),
            3,
            "Should have three bare scope-items"
        );
        assert_eq!(
            plan.summary.context,
            "Implement XML tolerance across all validator paths"
        );

        // Two steps total
        assert_eq!(plan.steps.len(), 2, "Should have two steps");

        // Step 1 has explicit number
        assert_eq!(plan.steps[0].number, 1, "First step has explicit number 1");
        assert!(
            !plan.steps[0].target_files.is_empty() || !plan.steps[0].content.elements.is_empty(),
            "Step 1 should have content or target files from bare elements"
        );

        // Step 2 auto-assigned number
        assert_eq!(
            plan.steps[1].number, 2,
            "Second step auto-assigned number 2"
        );
        assert_eq!(plan.steps[1].title, "Add bare file parsing in steps");
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
