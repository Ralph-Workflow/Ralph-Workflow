//! Tolerant parsing tests for plan XSD validation.
//!
//! These tests verify that the plan validator applies consistent tolerance:
//! - Stray text between elements is ignored
//! - Unknown elements are skipped
//! - Enum synonyms are accepted for `FileAction`, `StepType`, `Priority`, `Severity`, `ListType`
//! - Truly invalid enum values still produce errors

use super::*;

// ============================================================================
// Helper to build minimal plan XML for testing
// ============================================================================

/// Minimal valid plan XML template with customizable step type and file action.
fn minimal_plan_with_step(step_type: &str, action: &str, priority: &str) -> String {
    format!(
        r#"<ralph-plan>
<ralph-summary>
<context>Test plan for tolerant parsing</context>
<scope-items>
<scope-item count="1" category="files">file one</scope-item>
<scope-item count="1" category="tests">test one</scope-item>
<scope-item count="1" category="features">feature one</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="{step_type}" priority="{priority}">
<title>Implement the feature</title>
<target-files>
<file path="src/main.rs" action="{action}"/>
</target-files>
<content>
<paragraph>Implementation details.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/main.rs" action="{action}"/>
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
</ralph-plan>"#
    )
}

// ============================================================================
// FileAction synonym acceptance tests
// ============================================================================

#[test]
fn test_plan_file_action_synonym_add_parses_as_create() {
    let xml = minimal_plan_with_step("file-change", "add", "high");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "action=\"add\" should be accepted as create: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].target_files[0].action, FileAction::Create);
}

#[test]
fn test_plan_file_action_synonym_new_parses_as_create() {
    let xml = minimal_plan_with_step("file-change", "new", "high");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "action=\"new\" should be accepted as create: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].target_files[0].action, FileAction::Create);
}

#[test]
fn test_plan_file_action_synonym_edit_parses_as_modify() {
    let xml = minimal_plan_with_step("file-change", "edit", "high");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "action=\"edit\" should be accepted as modify: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].target_files[0].action, FileAction::Modify);
}

#[test]
fn test_plan_file_action_synonym_update_parses_as_modify() {
    let xml = minimal_plan_with_step("file-change", "update", "high");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "action=\"update\" should be accepted as modify: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].target_files[0].action, FileAction::Modify);
}

#[test]
fn test_plan_file_action_synonym_remove_parses_as_delete() {
    let xml = minimal_plan_with_step("file-change", "remove", "high");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "action=\"remove\" should be accepted as delete: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].target_files[0].action, FileAction::Delete);
}

#[test]
fn test_plan_file_action_invalid_still_rejected() {
    let xml = minimal_plan_with_step("file-change", "banana", "high");
    let result = validate_plan_xml(&xml);
    assert!(result.is_err(), "action=\"banana\" should be rejected");
}

// ============================================================================
// StepType synonym acceptance tests
// ============================================================================

#[test]
fn test_plan_step_type_synonym_code_parses_as_file_change() {
    let xml = minimal_plan_with_step("code", "modify", "high");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "type=\"code\" should be accepted as file-change: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].kind, StepType::FileChange);
}

#[test]
fn test_plan_step_type_synonym_implementation_parses_as_file_change() {
    let xml = minimal_plan_with_step("implementation", "modify", "high");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "type=\"implementation\" should be accepted as file-change: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].kind, StepType::FileChange);
}

#[test]
fn test_plan_step_type_synonym_investigate_parses_as_research() {
    // investigate is a research step, which does not require target-files
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="files">file one</scope-item>
<scope-item count="1" category="tests">test one</scope-item>
<scope-item count="1" category="features">feature one</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="investigate" priority="high">
<title>Research the approach</title>
<content>
<paragraph>Research details.</paragraph>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "type=\"investigate\" should be accepted as research: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].kind, StepType::Research);
}

#[test]
fn test_plan_step_type_synonym_analysis_parses_as_research() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="files">file one</scope-item>
<scope-item count="1" category="tests">test one</scope-item>
<scope-item count="1" category="features">feature one</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="analysis" priority="high">
<title>Analyze the codebase</title>
<content>
<paragraph>Analysis details.</paragraph>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "type=\"analysis\" should be accepted as research: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].kind, StepType::Research);
}

// ============================================================================
// Priority synonym acceptance tests
// ============================================================================

#[test]
fn test_plan_priority_synonym_p0_parses_as_critical() {
    let xml = minimal_plan_with_step("file-change", "modify", "p0");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "priority=\"p0\" should be accepted as critical: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].priority, Some(Priority::Critical));
}

#[test]
fn test_plan_priority_synonym_urgent_parses_as_critical() {
    let xml = minimal_plan_with_step("file-change", "modify", "urgent");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "priority=\"urgent\" should be accepted as critical: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].priority, Some(Priority::Critical));
}

#[test]
fn test_plan_priority_synonym_p1_parses_as_high() {
    let xml = minimal_plan_with_step("file-change", "modify", "p1");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "priority=\"p1\" should be accepted as high: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].priority, Some(Priority::High));
}

#[test]
fn test_plan_priority_synonym_p2_parses_as_medium() {
    let xml = minimal_plan_with_step("file-change", "modify", "p2");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "priority=\"p2\" should be accepted as medium: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].priority, Some(Priority::Medium));
}

#[test]
fn test_plan_priority_synonym_p3_parses_as_low() {
    let xml = minimal_plan_with_step("file-change", "modify", "p3");
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "priority=\"p3\" should be accepted as low: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].priority, Some(Priority::Low));
}

// ============================================================================
// Severity synonym acceptance in risk-pair tests
// ============================================================================

#[test]
fn test_plan_risk_severity_synonym_urgent_parses_as_critical() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
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
<risk-pair severity="urgent">
<risk>Urgent risk</risk>
<mitigation>Fix it fast</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>Run tests</method>
<expected-outcome>All pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "severity=\"urgent\" should be accepted as critical: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.risks_mitigations[0].severity, Some(Severity::Critical));
}

#[test]
fn test_plan_risk_severity_unknown_becomes_none() {
    // Unknown severity is silently treated as None (optional field)
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
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
<risk-pair severity="definitely-unknown-severity">
<risk>Some risk</risk>
<mitigation>Some mitigation</mitigation>
</risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification>
<method>Run tests</method>
<expected-outcome>All pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    // Unknown severity is an optional field - it should parse successfully with None severity
    assert!(
        result.is_ok(),
        "Unknown severity should be parsed as None (optional field): {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.risks_mitigations[0].severity, None);
}

// ============================================================================
// ListType synonym acceptance tests
// ============================================================================

#[test]
fn test_plan_list_type_synonym_bulleted_parses_as_unordered() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<content>
<list type="bulleted">
<item>First item</item>
<item>Second item</item>
</list>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "list type=\"bulleted\" should be accepted as unordered: {:?}",
        result.err()
    );
}

#[test]
fn test_plan_list_type_synonym_numbered_parses_as_ordered() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<content>
<list type="numbered">
<item>First item</item>
<item>Second item</item>
</list>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "list type=\"numbered\" should be accepted as ordered: {:?}",
        result.err()
    );
}

#[test]
fn test_plan_list_type_unrecognized_defaults_to_unordered() {
    // Tolerance change: unrecognized list type now defaults to Unordered instead of rejecting.
    // The list type attribute is non-essential structure; the list contents are what matter.
    // This preserves semantically-complete responses that just use non-standard type names.
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<content>
<list type="random">
<item>Item</item>
</list>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "list type=\"random\" should now default to Unordered instead of being rejected: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    let list = match &plan.steps[0].content.elements[0] {
        ContentElement::List(l) => l,
        other => panic!("Expected List, got {other:?}"),
    };
    assert_eq!(
        list.list_type,
        ListType::Unordered,
        "Unrecognized list type should default to Unordered"
    );
}

// ============================================================================
// Stray text tolerance tests
// ============================================================================

#[test]
fn test_plan_stray_text_between_top_level_sections_is_ignored() {
    let xml = r#"<ralph-plan>
Some explanatory text here that LLM may emit.
<ralph-summary>
<context>Test context</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
Here is another comment between sections.
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Stray text between top-level sections should be ignored: {:?}",
        result.err()
    );
}

#[test]
fn test_plan_stray_text_between_summary_children_is_ignored() {
    let xml = r#"<ralph-plan>
<ralph-summary>
Some commentary here.
<context>Test context</context>
More commentary after context.
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Stray text between summary children should be ignored: {:?}",
        result.err()
    );
}

// ============================================================================
// Unknown element skipping tests
// ============================================================================

#[test]
fn test_plan_unknown_element_inside_steps_is_skipped() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test context</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<some-unknown-element>This should be skipped</some-unknown-element>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Unknown element inside step should be skipped: {:?}",
        result.err()
    );
}

#[test]
fn test_plan_unknown_top_level_section_is_skipped() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test context</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-extra-section>This should be skipped entirely</ralph-extra-section>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Unknown top-level section should be skipped: {:?}",
        result.err()
    );
}

// ============================================================================
// Nested list type synonym acceptance tests
// These test the bug fix in section_parsers.rs parse_list function which
// previously used a hardcoded match instead of normalize_enum_value for
// nested list type attributes.
// ============================================================================

#[test]
fn test_plan_nested_list_type_bulleted_parses_as_unordered() {
    // Nested list with type="bulleted" should normalize to unordered
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<content>
<list type="unordered">
<item>Parent item
<list type="bulleted">
<item>Nested bulleted item</item>
</list>
</item>
</list>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Nested list type=\"bulleted\" should be accepted as unordered: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    // Verify the nested list was parsed with the correct type
    let list = match &plan.steps[0].content.elements[0] {
        ContentElement::List(l) => l,
        other => panic!("Expected List, got {other:?}"),
    };
    let nested = list.items[0]
        .nested_list
        .as_ref()
        .expect("should have nested list");
    assert_eq!(
        nested.list_type,
        ListType::Unordered,
        "Nested list type=\"bulleted\" should normalize to Unordered"
    );
}

#[test]
fn test_plan_nested_list_type_numbered_parses_as_ordered() {
    // Nested list with type="numbered" should normalize to ordered
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<content>
<list type="unordered">
<item>Parent item
<list type="numbered">
<item>Nested numbered item one</item>
<item>Nested numbered item two</item>
</list>
</item>
</list>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Nested list type=\"numbered\" should be accepted as ordered: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    let list = match &plan.steps[0].content.elements[0] {
        ContentElement::List(l) => l,
        other => panic!("Expected List, got {other:?}"),
    };
    let nested = list.items[0]
        .nested_list
        .as_ref()
        .expect("should have nested list");
    assert_eq!(
        nested.list_type,
        ListType::Ordered,
        "Nested list type=\"numbered\" should normalize to Ordered"
    );
}

#[test]
fn test_plan_nested_list_type_ol_parses_as_ordered() {
    // Nested list with type="ol" should normalize to ordered
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<content>
<list type="unordered">
<item>Parent item
<list type="ol">
<item>Nested ol item one</item>
<item>Nested ol item two</item>
</list>
</item>
</list>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Nested list type=\"ol\" should be accepted as ordered: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    let list = match &plan.steps[0].content.elements[0] {
        ContentElement::List(l) => l,
        other => panic!("Expected List, got {other:?}"),
    };
    let nested = list.items[0]
        .nested_list
        .as_ref()
        .expect("should have nested list");
    assert_eq!(
        nested.list_type,
        ListType::Ordered,
        "Nested list type=\"ol\" should normalize to Ordered"
    );
}

#[test]
fn test_plan_nested_list_type_ordered_uppercase_parses_as_ordered() {
    // Nested list with type="ORDERED" (case-insensitive) should normalize to ordered
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<content>
<list type="unordered">
<item>Parent item
<list type="ORDERED">
<item>Nested ORDERED item</item>
</list>
</item>
</list>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Nested list type=\"ORDERED\" (case-insensitive) should be accepted as ordered: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    let list = match &plan.steps[0].content.elements[0] {
        ContentElement::List(l) => l,
        other => panic!("Expected List, got {other:?}"),
    };
    let nested = list.items[0]
        .nested_list
        .as_ref()
        .expect("should have nested list");
    assert_eq!(
        nested.list_type,
        ListType::Ordered,
        "Nested list type=\"ORDERED\" should normalize to Ordered"
    );
}

#[test]
fn test_plan_nested_list_type_ul_parses_as_unordered() {
    // Nested list with type="ul" should normalize to unordered
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test plan</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<target-files>
<file path="src/foo.rs" action="modify"/>
</target-files>
<content>
<list type="ordered">
<item>Parent item
<list type="ul">
<item>Nested ul item</item>
</list>
</item>
</list>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Nested list type=\"ul\" should be accepted as unordered: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    let list = match &plan.steps[0].content.elements[0] {
        ContentElement::List(l) => l,
        other => panic!("Expected List, got {other:?}"),
    };
    let nested = list.items[0]
        .nested_list
        .as_ref()
        .expect("should have nested list");
    assert_eq!(
        nested.list_type,
        ListType::Unordered,
        "Nested list type=\"ul\" should normalize to Unordered"
    );
}

// ============================================================================
// Missing required content is still rejected
// ============================================================================

#[test]
fn test_plan_missing_summary_still_fails() {
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

    let result = validate_plan_xml(xml);
    assert!(result.is_err(), "Missing summary should still fail");
}

// ============================================================================
// Full plan with minor deviations parses successfully
// ============================================================================

#[test]
fn test_plan_full_plan_with_synonym_enums_parses_successfully() {
    // This test exercises the full tolerance: synonym enums + unknown elements skipped
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
<unknown-notes>This would be skipped by tolerant parsing</unknown-notes>
<content>
<paragraph>Add five new synonym tables.</paragraph>
<list type="bulleted">
<item>FILE_ACTION_SYNONYMS</item>
<item>STEP_TYPE_SYNONYMS</item>
</list>
</content>
</step>
<step number="2" type="investigate" priority="p1">
<title>Research existing parsers</title>
<content>
<paragraph>Review the reference implementations.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="tolerant_parsing.rs" action="update"/>
<file path="schema.rs" action="change"/>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Full plan with minor deviations should parse successfully: {:?}",
        result.err()
    );

    let plan = result.unwrap();
    assert_eq!(plan.steps.len(), 2);
    assert_eq!(plan.steps[0].kind, StepType::FileChange); // "code" → file-change
    assert_eq!(plan.steps[0].priority, Some(Priority::Critical)); // "p0" → critical
    assert_eq!(plan.steps[0].target_files[0].action, FileAction::Create); // "add" → create
    assert_eq!(plan.steps[1].kind, StepType::Research); // "investigate" → research
    assert_eq!(plan.steps[1].priority, Some(Priority::High)); // "p1" → high
    assert_eq!(plan.risks_mitigations[0].severity, Some(Severity::Critical)); // "urgent" → critical
                                                                              // primary-files also have synonym actions
    assert_eq!(
        plan.critical_files.primary_files[0].action,
        FileAction::Modify
    ); // "update" → modify
    assert_eq!(
        plan.critical_files.primary_files[1].action,
        FileAction::Modify
    ); // "change" → modify
}

// ============================================================================
// Wrapper element bypass tolerance tests (Steps 2 in the implementation plan)
// ============================================================================

/// Test: scope-item elements directly under ralph-summary (no scope-items wrapper)
/// should be accepted and parsed identically to properly-wrapped scope-items.
#[test]
fn test_plan_scope_items_without_wrapper_is_accepted() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test scope-items without wrapper</context>
<scope-item count="1" category="files">file one</scope-item>
<scope-item count="2" category="tests">test two</scope-item>
<scope-item count="3" category="features">feature three</scope-item>
</ralph-summary>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "scope-item elements without scope-items wrapper should be accepted: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(
        plan.summary.scope_items.len(),
        3,
        "Should parse 3 scope-items from bare elements"
    );
    assert_eq!(plan.summary.scope_items[0].description, "file one");
    assert_eq!(plan.summary.scope_items[1].description, "test two");
    assert_eq!(plan.summary.scope_items[2].description, "feature three");
}

/// Test: bare scope-item elements directly produce the same result as wrapped ones.
/// Verify attributes (count, category) are preserved when wrapper is omitted.
#[test]
fn test_plan_bare_scope_items_preserve_attributes() {
    let wrapped_xml = r#"<ralph-plan>
<ralph-summary>
<context>Same plan</context>
<scope-items>
<scope-item count="5" category="refactors">refactoring items</scope-item>
<scope-item count="10" category="tests">unit tests</scope-item>
<scope-item count="3" category="docs">doc updates</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Step</title>
<content><paragraph>Do things.</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/lib.rs" action="modify"/>
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
<method>cargo test</method>
<expected-outcome>All pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let bare_xml = r#"<ralph-plan>
<ralph-summary>
<context>Same plan</context>
<scope-item count="5" category="refactors">refactoring items</scope-item>
<scope-item count="10" category="tests">unit tests</scope-item>
<scope-item count="3" category="docs">doc updates</scope-item>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Step</title>
<content><paragraph>Do things.</paragraph></content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/lib.rs" action="modify"/>
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
<method>cargo test</method>
<expected-outcome>All pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let wrapped = validate_plan_xml(wrapped_xml);
    let bare = validate_plan_xml(bare_xml);

    assert!(
        wrapped.is_ok(),
        "Wrapped version should parse: {:?}",
        wrapped.err()
    );
    assert!(bare.is_ok(), "Bare version should parse: {:?}", bare.err());

    let w = wrapped.unwrap();
    let b = bare.unwrap();

    assert_eq!(w.summary.scope_items.len(), b.summary.scope_items.len());
    for (wi, bi) in w.summary.scope_items.iter().zip(&b.summary.scope_items) {
        assert_eq!(wi.description, bi.description);
        assert_eq!(wi.count, bi.count);
        assert_eq!(wi.category, bi.category);
    }
}

/// Test: file elements directly under step (no target-files wrapper) are accepted.
#[test]
fn test_plan_file_elements_without_target_files_wrapper_is_accepted() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test target-files without wrapper</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<file path="src/main.rs" action="modify"/>
<file path="src/lib.rs" action="create"/>
<content>
<paragraph>Details.</paragraph>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "file elements without target-files wrapper should be accepted for file-change steps: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(
        plan.steps[0].target_files.len(),
        2,
        "Should parse 2 target files from bare file elements"
    );
    assert_eq!(plan.steps[0].target_files[0].path, "src/main.rs");
    assert_eq!(plan.steps[0].target_files[0].action, FileAction::Modify);
    assert_eq!(plan.steps[0].target_files[1].path, "src/lib.rs");
    assert_eq!(plan.steps[0].target_files[1].action, FileAction::Create);
}

/// Test: self-closing file element directly under step is accepted.
#[test]
fn test_plan_self_closing_file_without_target_files_wrapper_is_accepted() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Step one</title>
<file path="src/main.rs" action="modify"/>
<content>
<paragraph>Details.</paragraph>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Self-closing file element without wrapper should be accepted: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].target_files.len(), 1);
}

/// Test: content elements directly under step (no content wrapper) are accepted.
#[test]
fn test_plan_content_elements_without_content_wrapper_is_accepted() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test content without wrapper</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Step one</title>
<paragraph>First paragraph of details.</paragraph>
<paragraph>Second paragraph of details.</paragraph>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "paragraph elements without content wrapper should be accepted: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    // Should have content with at least one element
    assert!(
        !plan.steps[0].content.elements.is_empty(),
        "Should have content elements"
    );
}

/// Test: file elements directly under ralph-critical-files (no primary-files wrapper) are accepted.
#[test]
fn test_plan_files_without_primary_files_wrapper_are_accepted() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test primary-files without wrapper</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Step one</title>
<content>
<paragraph>Details.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<file path="src/main.rs" action="modify"/>
<file path="src/lib.rs" action="create"/>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "file elements without primary-files wrapper should be accepted as primary files: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(
        plan.critical_files.primary_files.len(),
        2,
        "Should parse 2 primary files from bare file elements under ralph-critical-files"
    );
    assert_eq!(plan.critical_files.primary_files[0].path, "src/main.rs");
    assert_eq!(
        plan.critical_files.primary_files[0].action,
        FileAction::Modify
    );
    assert_eq!(plan.critical_files.primary_files[1].path, "src/lib.rs");
    assert_eq!(
        plan.critical_files.primary_files[1].action,
        FileAction::Create
    );
}

/// Test: file with purpose attribute directly under ralph-critical-files becomes a reference file.
#[test]
fn test_plan_bare_reference_file_without_reference_files_wrapper_is_accepted() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test reference-files without wrapper</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Step one</title>
<content>
<paragraph>Details.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/main.rs" action="modify"/>
</primary-files>
<file path="docs/architecture.md" purpose="reference for patterns"/>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "file element with purpose attr without reference-files wrapper should become reference file: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(
        plan.critical_files.reference_files.len(),
        1,
        "Should parse 1 reference file from bare file element with purpose attribute"
    );
    assert_eq!(
        plan.critical_files.reference_files[0].path,
        "docs/architecture.md"
    );
}

/// Test: ambiguous bare file (has both action and purpose attributes) should be handled gracefully.
/// When a bare file has action, it's treated as primary. The purpose attr is ignored.
#[test]
fn test_plan_bare_file_with_action_attr_becomes_primary_not_reference() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test ambiguous bare file</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Step one</title>
<content>
<paragraph>Details.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<file path="src/main.rs" action="modify" purpose="also has purpose"/>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Bare file with action attr should be accepted as primary file: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    // action attribute wins → primary file
    assert_eq!(plan.critical_files.primary_files.len(), 1);
    assert_eq!(plan.critical_files.primary_files[0].path, "src/main.rs");
}

// ============================================================================
// Missing/invalid attribute default tests (Step 3 in the implementation plan)
// ============================================================================

/// Test: list without type attribute defaults to Unordered instead of failing.
#[test]
fn test_plan_list_without_type_attribute_defaults_to_unordered() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test list without type attr</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Step one</title>
<content>
<list>
<item>First item</item>
<item>Second item</item>
</list>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "list without type attribute should default to Unordered instead of failing: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    let list = match &plan.steps[0].content.elements[0] {
        ContentElement::List(l) => l,
        other => panic!("Expected List, got {other:?}"),
    };
    assert_eq!(
        list.list_type,
        ListType::Unordered,
        "Missing list type should default to Unordered"
    );
}

/// Test: heading with level="1" (below minimum) is clamped to 2.
#[test]
fn test_plan_heading_level_1_clamped_to_2() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test heading level clamping</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Step one</title>
<content>
<heading level="1">H1 Should Be Clamped</heading>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "heading level=\"1\" should be clamped to 2 instead of failing: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    let heading = match &plan.steps[0].content.elements[0] {
        ContentElement::Heading(h) => h,
        other => panic!("Expected Heading, got {other:?}"),
    };
    assert_eq!(
        heading.level, 2,
        "heading level=\"1\" should be clamped to 2"
    );
}

/// Test: heading with level="5" (above maximum) is clamped to 4.
#[test]
fn test_plan_heading_level_5_clamped_to_4() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test heading level clamping</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Step one</title>
<content>
<heading level="5">H5 Should Be Clamped</heading>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "heading level=\"5\" should be clamped to 4 instead of failing: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    let heading = match &plan.steps[0].content.elements[0] {
        ContentElement::Heading(h) => h,
        other => panic!("Expected Heading, got {other:?}"),
    };
    assert_eq!(
        heading.level, 4,
        "heading level=\"5\" should be clamped to 4"
    );
}

/// Test: heading without level attribute defaults to 3.
#[test]
fn test_plan_heading_without_level_defaults_to_3() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test heading without level attr</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Step one</title>
<content>
<heading>No Level Specified</heading>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "heading without level attribute should default to 3: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    let heading = match &plan.steps[0].content.elements[0] {
        ContentElement::Heading(h) => h,
        other => panic!("Expected Heading, got {other:?}"),
    };
    assert_eq!(
        heading.level, 3,
        "heading without level attr should default to 3"
    );
}

/// Test: step without number attribute is auto-assigned sequential number starting from 1.
#[test]
fn test_plan_step_without_number_is_auto_assigned() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test step auto-numbering</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step type="action" priority="high">
<title>First step without number</title>
<content>
<paragraph>Details.</paragraph>
</content>
</step>
<step type="action" priority="medium">
<title>Second step without number</title>
<content>
<paragraph>More details.</paragraph>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "steps without number attribute should be auto-assigned sequential numbers: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps.len(), 2, "Should have 2 steps");
    assert_eq!(
        plan.steps[0].number, 1,
        "First auto-assigned step should be number 1"
    );
    assert_eq!(
        plan.steps[1].number, 2,
        "Second auto-assigned step should be number 2"
    );
}

/// Test: mixed - some steps have explicit numbers, unnumbered ones get next available.
#[test]
fn test_plan_mixed_numbered_and_unnumbered_steps() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test mixed numbering</context>
<scope-items>
<scope-item count="1" category="a">a</scope-item>
<scope-item count="1" category="b">b</scope-item>
<scope-item count="1" category="c">c</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action" priority="high">
<title>Explicit step 1</title>
<content><paragraph>Details.</paragraph></content>
</step>
<step type="action" priority="medium">
<title>No-number step after step 1</title>
<content><paragraph>Details.</paragraph></content>
</step>
<step number="5" type="action" priority="low">
<title>Explicit step 5</title>
<content><paragraph>Details.</paragraph></content>
</step>
<step type="action" priority="low">
<title>No-number step after step 5</title>
<content><paragraph>Details.</paragraph></content>
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

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "Mixed numbered/unnumbered steps should be accepted: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(plan.steps.len(), 4, "Should have 4 steps");
    assert_eq!(plan.steps[0].number, 1, "Explicit step 1");
    assert_eq!(plan.steps[2].number, 5, "Explicit step 5");
    // The unnumbered steps should get sequential numbers after each explicit step
    // step[1] (after step 1): auto-assigns to 2
    // step[3] (after step 5): auto-assigns to 6
    assert!(
        plan.steps[1].number > 0,
        "Second step should have non-zero number"
    );
    assert!(
        plan.steps[3].number > 0,
        "Fourth step should have non-zero number"
    );
}
