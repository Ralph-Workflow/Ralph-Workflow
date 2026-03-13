//! Structure-focused plan validation tests.
//!
//! These tests focus on required/optional sections and high-level plan structure
//! rather than detailed attribute parsing.

use super::*;

#[test]
fn test_validate_minimal_valid_plan() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Add a new feature to the application</context>
<scope-items>
<scope-item count="3" category="files">files to modify</scope-item>
<scope-item count="1" category="feature">new feature</scope-item>
<scope-item count="5" category="tests">test cases</scope-item>
</scope-items>
</ralph-summary>

<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Add configuration</title>
<target-files>
<file path="src/config.rs" action="modify"/>
</target-files>
<location>After the imports</location>
<content>
<paragraph>Add new configuration option.</paragraph>
</content>
</step>
</ralph-implementation-steps>

<ralph-critical-files>
<primary-files>
<file path="src/config.rs" action="modify" estimated-changes="~20 lines"/>
</primary-files>
</ralph-critical-files>

<ralph-risks-mitigations>
<risk-pair severity="low">
<risk>Breaking existing configuration</risk>
<mitigation>Add backward compatibility</mitigation>
</risk-pair>
</ralph-risks-mitigations>

<ralph-verification-strategy>
<verification>
<method>Run unit tests</method>
<expected-outcome>All tests pass</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(result.is_ok(), "Error: {:?}", result.err());

    let plan = result.unwrap();
    assert_eq!(plan.summary.scope_items.len(), 3);
    assert_eq!(plan.steps.len(), 1);
    assert_eq!(plan.steps[0].number, 1);
    assert_eq!(plan.steps[0].kind, StepType::FileChange);
    assert_eq!(plan.steps[0].priority, Some(Priority::High));
    assert_eq!(plan.critical_files.primary_files.len(), 1);
    assert_eq!(plan.risks_mitigations.len(), 1);
    assert_eq!(plan.verification_strategy.len(), 1);
}

#[test]
fn test_missing_root_element() {
    let xml = "Some random text";
    let result = validate_plan_xml(xml);
    assert!(result.is_err());
    assert_eq!(result.unwrap_err().element_path, "ralph-plan");
}

#[test]
fn test_missing_summary() {
    let xml = r#"<ralph-plan>
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
    assert_eq!(result.unwrap_err().element_path, "ralph-summary");
}

#[test]
fn test_insufficient_scope_items() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test context</context>
<scope-items>
<scope-item>Only one item</scope-item>
<scope-item>Two items</scope-item>
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
    assert!(err.element_path.contains("scope-items"));
    assert!(err.found.contains('2'));
}

#[test]
fn test_action_step_without_target_files() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test action step</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Configure environment</title>
<content>
<paragraph>Set up the test environment.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="test.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>Test risk</risk><mitigation>Test mitigation</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>Test</method><expected-outcome>Pass</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(result.is_ok(), "Error: {:?}", result.err());

    let plan = result.unwrap();
    assert_eq!(plan.steps[0].kind, StepType::Action);
    assert!(plan.steps[0].target_files.is_empty());
}

#[test]
fn test_file_change_step_without_target_files_is_reclassified_as_action() {
    // Previously rejected; now reclassified as action type.
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test file-change step</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="file-change">
<title>Modify config</title>
<content>
<paragraph>Change the configuration.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files><file path="test.rs" action="create"/></primary-files>
</ralph-critical-files>
<ralph-risks-mitigations>
<risk-pair><risk>Test risk</risk><mitigation>Test mitigation</mitigation></risk-pair>
</ralph-risks-mitigations>
<ralph-verification-strategy>
<verification><method>Test</method><expected-outcome>Pass</expected-outcome></verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(
        result.is_ok(),
        "file-change step without target-files should now be reclassified as action: {:?}",
        result.err()
    );
    let plan = result.unwrap();
    assert_eq!(
        plan.steps[0].kind,
        StepType::Action,
        "step should be reclassified as action"
    );
}
