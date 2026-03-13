//! Tests for file element validation.
//!
//! These tests verify that the parser correctly validates file elements
//! in target-files and critical-files sections.

use super::*;
use crate::files::llm_output_extraction::xsd_validation::XsdErrorType;

#[test]
fn test_target_file_missing_path() {
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
<target-files>
<file action="modify"/>
</target-files>
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
    assert!(err.element_path.contains("file"));
    assert!(err.expected.contains("path"));
}

#[test]
fn test_target_file_missing_action() {
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
<target-files>
<file path="src/test.rs"/>
</target-files>
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
    assert!(err.element_path.contains("file"));
    assert!(err.expected.contains("action"));
}

#[test]
fn test_target_file_invalid_action() {
    // Note: "update" is now a valid synonym for "modify" via tolerant parsing.
    // This test uses a truly invalid action value that has no synonym mapping.
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
<target-files>
<file path="src/test.rs" action="banana"/>
</target-files>
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
    assert_eq!(err.error_type, XsdErrorType::InvalidContent);
    assert!(err.found.contains("banana"));
    assert!(
        err.suggestion.contains("create")
            || err.suggestion.contains("modify")
            || err.suggestion.contains("delete")
    );
}

#[test]
fn test_target_file_all_actions_valid() {
    for action in ["create", "modify", "delete"] {
        let xml = format!(
            r#"<ralph-plan>
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
<target-files>
<file path="src/test.rs" action="{action}"/>
</target-files>
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
</ralph-plan>"#
        );

        let result = validate_plan_xml(&xml);
        assert!(
            result.is_ok(),
            "Failed for action '{}': {:?}",
            action,
            result.err()
        );
    }
}

#[test]
fn test_multiple_target_files() {
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
<target-files>
<file path="src/a.rs" action="create"/>
<file path="src/b.rs" action="modify"/>
<file path="src/c.rs" action="delete"/>
</target-files>
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
    assert!(result.is_ok(), "Error: {:?}", result.err());
    let plan = result.unwrap();
    assert_eq!(plan.steps[0].target_files.len(), 3);
    assert_eq!(plan.steps[0].target_files[0].action, FileAction::Create);
    assert_eq!(plan.steps[0].target_files[1].action, FileAction::Modify);
    assert_eq!(plan.steps[0].target_files[2].action, FileAction::Delete);
}
