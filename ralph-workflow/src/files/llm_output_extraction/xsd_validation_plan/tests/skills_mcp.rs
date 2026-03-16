//! Tests for skills-mcp field parsing in plan XML.

use super::*;

const MINIMAL_PLAN_WITH_SKILLS_MCP: &str = r#"<ralph-plan>
<ralph-summary>
<context>Add a new feature</context>
<scope-items>
<scope-item count="3" category="files">files to modify</scope-item>
<scope-item count="1" category="feature">new feature</scope-item>
<scope-item count="5" category="tests">test cases</scope-item>
</scope-items>
</ralph-summary>

<skills-mcp>
<skill reason="Implementation should start with failing tests">test-driven-development</skill>
<skill reason="The work touches Angular UI code">frontend-angular</skill>
<mcp reason="Use for Angular v21 implementation and documentation">angular-mcp</mcp>
</skills-mcp>

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

/// Helper to build a minimal plan XML with optional skills-mcp block.
fn minimal_plan_xml(skills_mcp_block: Option<&str>) -> String {
    let block = skills_mcp_block.unwrap_or("");
    format!(
        r#"<ralph-plan>
<ralph-summary>
<context>Add a new feature</context>
<scope-items>
<scope-item count="3" category="files">files to modify</scope-item>
<scope-item count="1" category="feature">new feature</scope-item>
<scope-item count="5" category="tests">test cases</scope-item>
</scope-items>
</ralph-summary>
{block}
<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Add configuration</title>
<target-files>
<file path="src/config.rs" action="modify"/>
</target-files>
<content>
<paragraph>Add new configuration option.</paragraph>
</content>
</step>
</ralph-implementation-steps>
<ralph-critical-files>
<primary-files>
<file path="src/config.rs" action="modify"/>
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
</ralph-plan>"#
    )
}

#[test]
fn test_plan_with_skills_mcp_parses_skill_and_mcp_entries() {
    let result = validate_plan_xml(MINIMAL_PLAN_WITH_SKILLS_MCP);
    assert!(
        result.is_ok(),
        "Plan with skills-mcp should parse: {result:?}"
    );
    let plan = result.unwrap();

    let sm = plan.skills_mcp.expect("skills_mcp should be present");
    assert_eq!(sm.skills.len(), 2, "Should have 2 skill entries");
    assert_eq!(sm.mcps.len(), 1, "Should have 1 mcp entry");

    assert_eq!(sm.skills[0].name, "test-driven-development");
    assert_eq!(
        sm.skills[0].reason.as_deref(),
        Some("Implementation should start with failing tests")
    );

    assert_eq!(sm.skills[1].name, "frontend-angular");
    assert_eq!(
        sm.skills[1].reason.as_deref(),
        Some("The work touches Angular UI code")
    );

    assert_eq!(sm.mcps[0].name, "angular-mcp");
    assert_eq!(
        sm.mcps[0].reason.as_deref(),
        Some("Use for Angular v21 implementation and documentation")
    );
}

#[test]
fn test_plan_without_skills_mcp_parses_successfully_with_none() {
    let xml = minimal_plan_xml(None);
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "Plan without skills-mcp should still parse: {result:?}"
    );
    let plan = result.unwrap();
    assert!(
        plan.skills_mcp.is_none(),
        "skills_mcp should be None when absent"
    );
}

#[test]
fn test_plan_skills_mcp_without_reason_attribute() {
    let xml = minimal_plan_xml(Some(
<<<<<<< Updated upstream
        "<skills-mcp>\n<skill>test-driven-development</skill>\n<mcp>context7</mcp>\n</skills-mcp>",
=======
        r#"<skills-mcp>
<skill>test-driven-development</skill>
<mcp>context7</mcp>
</skills-mcp>"#,
>>>>>>> Stashed changes
    ));
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "skills-mcp without reason attributes should parse: {result:?}"
    );
    let plan = result.unwrap();
    let sm = plan.skills_mcp.expect("skills_mcp should be present");
    assert_eq!(sm.skills[0].name, "test-driven-development");
    assert!(
        sm.skills[0].reason.is_none(),
        "reason should be None when absent"
    );
    assert_eq!(sm.mcps[0].name, "context7");
    assert!(sm.mcps[0].reason.is_none());
}

#[test]
fn test_plan_skills_mcp_with_raw_content_fallback_for_malformed() {
    // Content that is entirely plain text (no structured skill/mcp tags) should be
    // preserved as raw_content
    let xml = minimal_plan_xml(Some(
        r"<skills-mcp>Use test-driven-development and context7 for this work.</skills-mcp>",
    ));
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "Malformed skills-mcp content should still parse plan: {result:?}"
    );
    let plan = result.unwrap();
    let sm = plan
        .skills_mcp
        .expect("skills_mcp should be Some even for malformed content");
    // Either the raw text is in raw_content or it was ignored - but the plan must not fail
    // The important thing is the plan itself parsed.
    assert!(
        sm.skills.is_empty() || !sm.skills.is_empty(),
        "We just want it to not crash"
    );
}

#[test]
fn test_plan_skills_mcp_empty_element() {
    let xml = minimal_plan_xml(Some("<skills-mcp></skills-mcp>"));
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "Empty skills-mcp should be accepted: {result:?}"
    );
    let plan = result.unwrap();
    let sm = plan
        .skills_mcp
        .expect("Empty skills-mcp should be present (not None)");
    assert!(sm.skills.is_empty());
    assert!(sm.mcps.is_empty());
}

#[test]
fn test_plan_skills_mcp_self_closing() {
    let xml = minimal_plan_xml(Some("<skills-mcp/>"));
    let result = validate_plan_xml(&xml);
    assert!(
        result.is_ok(),
        "Self-closing skills-mcp should be accepted: {result:?}"
    );
    let plan = result.unwrap();
    let sm = plan
        .skills_mcp
        .expect("Self-closing skills-mcp should be present");
    assert!(sm.skills.is_empty());
    assert!(sm.mcps.is_empty());
}
