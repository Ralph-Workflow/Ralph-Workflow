//! Tests for list item flexibility with block elements inside list items.
//!
//! This module tests that list items can contain various content types including
//! code blocks, paragraphs, inline code, and nested lists.

use super::*;

// ═══════════════════════════════════════════════════════════════════════════
// LIST ITEM FLEXIBILITY TESTS - Block elements inside list items
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn test_list_item_with_code_block() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test code blocks in list items</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Steps with code examples</title>
<content>
<list type="ordered">
<item>First, create the config:
<code-block language="rust">
struct Config {
name: String,
}
</code-block>
</item>
<item>Then use it in main</item>
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
    assert!(
        result.is_ok(),
        "List items with code-block should be valid: {:?}",
        result.err()
    );

    let plan = result.unwrap();
    if let ContentElement::List(l) = &plan.steps[0].content.elements[0] {
        assert_eq!(l.items.len(), 2);
        // First item should have text content (code-block is stripped for inline parsing)
        assert!(!l.items[0].content.is_empty());
    } else {
        panic!("Expected list");
    }
}

#[test]
fn test_list_item_with_paragraph() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test paragraphs in list items</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Steps with paragraphs</title>
<content>
<list type="unordered">
<item>
<paragraph>This is a detailed explanation of the first step.</paragraph>
</item>
<item>Simple second item</item>
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
    assert!(
        result.is_ok(),
        "List items with paragraph should be valid: {:?}",
        result.err()
    );
}

#[test]
fn test_list_item_with_inline_code() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test inline code in list items</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Steps with inline code</title>
<content>
<list type="ordered">
<item>Run <code>cargo build</code> to compile</item>
<item>Execute <code>./target/debug/app</code></item>
<item>Check the <emphasis>output</emphasis> carefully</item>
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
    assert!(
        result.is_ok(),
        "List items with inline code should be valid: {:?}",
        result.err()
    );

    let plan = result.unwrap();
    if let ContentElement::List(l) = &plan.steps[0].content.elements[0] {
        assert_eq!(l.items.len(), 3);
        // Check that inline code was parsed
        let first_item = &l.items[0].content;
        assert!(
            first_item
                .iter()
                .any(|e| matches!(e, InlineElement::Code(_))),
            "Should contain inline code element"
        );
    } else {
        panic!("Expected list");
    }
}

#[test]
fn test_list_item_with_mixed_block_and_inline() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test mixed content in list items</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Complex list items</title>
<content>
<list type="ordered">
<item>First, understand the <code>Config</code> struct:
<code-block language="rust">
struct Config {
    name: String,
}
</code-block>
Then implement the <emphasis>required</emphasis> trait.
</item>
<item>Next step with <code>simple</code> code</item>
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
    assert!(
        result.is_ok(),
        "List items with mixed block and inline should be valid: {:?}",
        result.err()
    );

    let plan = result.unwrap();
    if let ContentElement::List(l) = &plan.steps[0].content.elements[0] {
        assert_eq!(l.items.len(), 2);
        // First item should have inline elements
        assert!(!l.items[0].content.is_empty());
    } else {
        panic!("Expected list");
    }
}

#[test]
fn test_list_item_with_nested_list_and_code_block() {
    let xml = r#"<ralph-plan>
<ralph-summary>
<context>Test nested structures in list items</context>
<scope-items>
<scope-item>item 1</scope-item>
<scope-item>item 2</scope-item>
<scope-item>item 3</scope-item>
</scope-items>
</ralph-summary>
<ralph-implementation-steps>
<step number="1" type="action">
<title>Nested list test</title>
<content>
<list type="ordered">
<item>Top-level item with nested list:
<list type="unordered">
<item>Nested item 1</item>
<item>Nested item 2</item>
</list>
</item>
<item>Another top-level item with code:
<code-block language="rust">
let x = 42;
</code-block>
</item>
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
    assert!(
        result.is_ok(),
        "List items with nested list and code-block should be valid: {:?}",
        result.err()
    );

    let plan = result.unwrap();
    if let ContentElement::List(l) = &plan.steps[0].content.elements[0] {
        assert_eq!(l.items.len(), 2);
    } else {
        panic!("Expected list");
    }
}

#[test]
fn test_strip_block_elements_helper() {
    // Test the helper function directly
    let content = r#"First text <code-block language="rust">fn main() {}</code-block> middle <paragraph>para</paragraph> end"#;
    let stripped = crate::files::llm_output_extraction::xsd_validation_plan::strip_block_elements_for_inline_parsing(content);
    assert!(
        !stripped.contains("<code-block"),
        "Should strip code-block tags"
    );
    assert!(
        !stripped.contains("<paragraph"),
        "Should strip paragraph tags"
    );
    assert!(stripped.contains("First text"), "Should preserve text");
    assert!(stripped.contains("middle"), "Should preserve middle text");
    assert!(stripped.contains("end"), "Should preserve end text");
}

#[test]
fn test_strip_block_elements_with_nested_list() {
    let content = r#"Text before <list type="ordered"><item>nested</item></list> text after"#;
    let stripped = crate::files::llm_output_extraction::xsd_validation_plan::strip_block_elements_for_inline_parsing(content);
    assert!(!stripped.contains("<list"), "Should strip list tags");
    assert!(stripped.contains("Text before"), "Should preserve text");
    assert!(stripped.contains("text after"), "Should preserve text");
}

#[test]
fn test_strip_block_elements_with_deeply_nested_list() {
    let content = r#"Lead <list type="unordered"><item>outer <list type="ordered"><item>inner</item></list> tail</item></list> Trail"#;
    let stripped = crate::files::llm_output_extraction::xsd_validation_plan::strip_block_elements_for_inline_parsing(content);
    assert!(
        !stripped.contains("<list"),
        "Should remove nested opening list tags"
    );
    assert!(
        !stripped.contains("</list>"),
        "Should remove nested closing list tags"
    );
    assert!(stripped.contains("Lead"), "Should preserve prefix text");
    assert!(stripped.contains("Trail"), "Should preserve suffix text");
}
