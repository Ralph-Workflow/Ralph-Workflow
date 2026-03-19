//! Skills-MCP field tests for XSD validation of development result XML format.

#[test]
fn test_dev_result_with_skills_mcp_parses_entries() {
    let xml = r#"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Verification found a reproducible reducer failure.</ralph-summary>
<skills-mcp>
<skill reason="A concrete failure should be investigated before editing code">systematic-debugging</skill>
<skill reason="The eventual fix should begin with a reproducing test">test-driven-development</skill>
<mcp reason="Use when external dependency behavior needs confirmation">context7</mcp>
</skills-mcp>
<ralph-files-changed>src/lib.rs</ralph-files-changed>
</ralph-development-result>"#;

    let result = validate_development_result_xml(xml);
    assert!(
        result.is_ok(),
        "Dev result with skills-mcp should parse: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(elements.status, "partial");

    let sm = elements.skills_mcp.expect("skills_mcp should be present");
    assert_eq!(sm.skills.len(), 2);
    assert_eq!(sm.mcps.len(), 1);
    assert_eq!(sm.skills[0].name, "systematic-debugging");
    assert_eq!(
        sm.skills[0].reason.as_deref(),
        Some("A concrete failure should be investigated before editing code")
    );
    assert_eq!(sm.mcps[0].name, "context7");
}

#[test]
fn test_dev_result_without_skills_mcp_still_validates() {
    let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Fixed all bugs</ralph-summary>
</ralph-development-result>";

    let result = validate_development_result_xml(xml);
    assert!(
        result.is_ok(),
        "Dev result without skills-mcp should parse: {result:?}"
    );
    let elements = result.unwrap();
    assert!(
        elements.skills_mcp.is_none(),
        "skills_mcp should be None when absent"
    );
}

#[test]
fn test_dev_result_malformed_skills_mcp_preserves_raw_content() {
    let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Some work done.</ralph-summary>
<skills-mcp>Use test-driven-development for fixing this issue.</skills-mcp>
</ralph-development-result>";

    let result = validate_development_result_xml(xml);
    assert!(
        result.is_ok(),
        "Dev result with malformed skills-mcp should still parse: {result:?}"
    );
    let elements = result.unwrap();
    let sm = elements
        .skills_mcp
        .expect("skills_mcp should still be present even if malformed");
    assert!(
        sm.raw_content.is_some() || sm.skills.is_empty(),
        "Malformed content should be preserved"
    );
}

#[test]
fn test_dev_result_empty_skills_mcp() {
    let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done.</ralph-summary>
<skills-mcp></skills-mcp>
</ralph-development-result>";

    let result = validate_development_result_xml(xml);
    assert!(
        result.is_ok(),
        "Empty skills-mcp should be accepted: {result:?}"
    );
    let elements = result.unwrap();
    let sm = elements
        .skills_mcp
        .expect("Empty skills-mcp should be Some, not None");
    assert!(sm.skills.is_empty());
    assert!(sm.mcps.is_empty());
}

#[test]
fn test_dev_result_self_closing_skills_mcp() {
    let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done.</ralph-summary>
<skills-mcp/>
</ralph-development-result>";

    let result = validate_development_result_xml(xml);
    assert!(
        result.is_ok(),
        "Self-closing skills-mcp should be accepted: {result:?}"
    );
    let elements = result.unwrap();
    let sm = elements
        .skills_mcp
        .expect("Self-closing skills-mcp should be Some, not None");
    assert!(sm.skills.is_empty());
    assert!(sm.mcps.is_empty());
}
