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
fn test_plan_list_type_invalid_still_rejected() {
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
    assert!(result.is_err(), "list type=\"random\" should be rejected");
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
