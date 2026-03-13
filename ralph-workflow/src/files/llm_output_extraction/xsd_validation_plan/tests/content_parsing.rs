//! Content element parsing tests.
//!
//! These tests validate that individual rich content elements (code blocks,
//! tables, lists) parse correctly into typed structures.

use super::*;

#[test]
fn test_parse_code_block() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test code block</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Test step</title>
<content>
<code-block language="rust" filename="test.rs">
fn main() {
println!("Hello");
}
</code-block>
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
    if let ContentElement::CodeBlock(cb) = &plan.steps[0].content.elements[0] {
        assert_eq!(cb.language, Some("rust".to_string()));
        assert_eq!(cb.filename, Some("test.rs".to_string()));
        assert!(cb.content.contains("println"));
    } else {
        panic!("Expected code block");
    }
}

#[test]
fn test_parse_table() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test table</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Test step</title>
<content>
<table>
<caption>Test Table</caption>
<columns>
<column>Name</column>
<column>Value</column>
</columns>
<row>
<cell>foo</cell>
<cell>bar</cell>
</row>
<row>
<cell>baz</cell>
<cell>qux</cell>
</row>
</table>
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
    if let ContentElement::Table(t) = &plan.steps[0].content.elements[0] {
        assert_eq!(t.caption, Some("Test Table".to_string()));
        assert_eq!(t.columns.len(), 2);
        assert_eq!(t.rows.len(), 2);
        assert_eq!(t.rows[0].cells.len(), 2);
    } else {
        panic!("Expected table");
    }
}

#[test]
fn test_parse_list() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test list</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Test step</title>
<content>
<list type="ordered">
<item>First item</item>
<item>Second item</item>
<item>Third item</item>
</list>
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
    if let ContentElement::List(l) = &plan.steps[0].content.elements[0] {
        assert_eq!(l.list_type, ListType::Ordered);
        assert_eq!(l.items.len(), 3);
    } else {
        panic!("Expected list");
    }
}

#[test]
fn test_complex_plan_with_dependencies() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Implement OAuth2 authentication for the application</context>
<scope-items>
<scope-item count="3" category="auth">OAuth2 provider integrations</scope-item>
<scope-item count="5" category="api">new API endpoints</scope-item>
<scope-item count="2" category="ui">login components</scope-item>
<scope-item count="8" category="tests">test cases</scope-item>
</scope-items>
</ralph-summary>

<ralph-implementation-steps>
<step number="1" type="file-change" priority="high">
<title>Add OAuth2 configuration</title>
<target-files>
<file path="src/config/oauth.rs" action="create"/>
<file path="src/config/mod.rs" action="modify"/>
</target-files>
<location>Create new file; add mod to mod.rs</location>
<rationale>Configuration must exist before providers</rationale>
<content>
<paragraph>Create OAuth2 configuration:</paragraph>
<code-block language="rust">
pub struct OAuth2Config {
pub client_id: String,
}
</code-block>
</content>
</step>

<step number="2" type="research" priority="medium">
<title>Research OAuth2 libraries</title>
<content>
<paragraph>Evaluate libraries:</paragraph>
<table>
<caption>Library Comparison</caption>
<columns>
<column>Library</column>
<column>Pros</column>
</columns>
<row>
<cell>oauth2</cell>
<cell>Official</cell>
</row>
</table>
</content>
<depends-on step="1"/>
</step>

<step number="3" type="action" priority="high">
<title>Configure test environment</title>
<content>
<list type="ordered">
<item>Create Google Cloud project</item>
<item>Create GitHub OAuth App</item>
</list>
</content>
<depends-on step="1"/>
</step>
</ralph-implementation-steps>

<ralph-critical-files>
<primary-files>
<file path="src/config/oauth.rs" action="create" estimated-changes="~50 lines"/>
<file path="src/auth/oauth2.rs" action="create" estimated-changes="~200 lines"/>
</primary-files>
<reference-files>
<file path="src/auth/mod.rs" purpose="Existing auth patterns"/>
</reference-files>
</ralph-critical-files>

<ralph-risks-mitigations>
<risk-pair severity="high">
<risk>Token interception</risk>
<mitigation>Use HTTPS, implement PKCE</mitigation>
</risk-pair>
<risk-pair severity="medium">
<risk>Provider API changes</risk>
<mitigation>Abstract behind interfaces</mitigation>
</risk-pair>
</ralph-risks-mitigations>

<ralph-verification-strategy>
<verification>
<method>Run integration tests</method>
<expected-outcome>OAuth flows complete successfully</expected-outcome>
</verification>
<verification>
<method>Manual testing</method>
<expected-outcome>Users can sign in</expected-outcome>
</verification>
</ralph-verification-strategy>
</ralph-plan>"#;

    let result = validate_plan_xml(xml);
    assert!(result.is_ok(), "Error: {:?}", result.err());

    let plan = result.unwrap();

    // Summary checks
    assert_eq!(plan.summary.scope_items.len(), 4);
    assert_eq!(plan.summary.scope_items[0].count, Some("3".to_string()));
    assert_eq!(
        plan.summary.scope_items[0].category,
        Some("auth".to_string())
    );

    // Steps checks
    assert_eq!(plan.steps.len(), 3);
    assert_eq!(plan.steps[0].number, 1);
    assert_eq!(plan.steps[0].kind, StepType::FileChange);
    assert_eq!(plan.steps[0].target_files.len(), 2);

    assert_eq!(plan.steps[1].number, 2);
    assert_eq!(plan.steps[1].kind, StepType::Research);
    assert_eq!(plan.steps[1].depends_on, vec![1]);

    assert_eq!(plan.steps[2].number, 3);
    assert_eq!(plan.steps[2].kind, StepType::Action);
    assert_eq!(plan.steps[2].depends_on, vec![1]);

    // Critical files checks
    assert_eq!(plan.critical_files.primary_files.len(), 2);
    assert_eq!(plan.critical_files.reference_files.len(), 1);

    // Risks checks
    assert_eq!(plan.risks_mitigations.len(), 2);
    assert_eq!(plan.risks_mitigations[0].severity, Some(Severity::High));

    // Verification checks
    assert_eq!(plan.verification_strategy.len(), 2);
}

#[test]
fn test_merges_bare_content_with_content_wrapper() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test mixed content</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Mixed content step</title>
<content><paragraph>Wrapped paragraph</paragraph></content>
<paragraph>Bare paragraph</paragraph>
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
    assert_eq!(plan.steps[0].content.elements.len(), 2);
}
