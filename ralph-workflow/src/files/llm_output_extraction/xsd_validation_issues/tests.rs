use super::*;

#[test]
fn test_validate_valid_single_issue() {
    let xml = r"<ralph-issues>
<ralph-issue>First issue description</ralph-issue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    assert_eq!(elements.issues.len(), 1);
    assert_eq!(elements.issues[0].text, "First issue description");
    assert!(elements.no_issues_found.is_none());
}

#[test]
fn test_validate_valid_multiple_issues() {
    let xml = r"<ralph-issues>
<ralph-issue>First issue</ralph-issue>
<ralph-issue>Second issue</ralph-issue>
<ralph-issue>Third issue</ralph-issue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    assert_eq!(elements.issues.len(), 3);
    assert_eq!(elements.issue_count(), 3);
}

#[test]
fn test_validate_valid_no_issues_found() {
    let xml = r"<ralph-issues><ralph-no-issues-found>No issues were found during review</ralph-no-issues-found></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    assert!(elements.issues.is_empty());
    assert!(elements.no_issues_found.is_some());
    assert!(elements.is_empty());
}

#[test]
fn test_validate_missing_root_element() {
    let xml = r"Some random text without proper XML tags";

    let result = validate_issues_xml(xml);
    assert!(result.is_err());
    let error = result.unwrap_err();
    assert_eq!(error.element_path, "ralph-issues");
}

#[test]
fn test_validate_empty_issues() {
    let xml = r"<ralph-issues></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_err());
    let error = result.unwrap_err();
    assert!(error.expected.contains("at least one"));
}

#[test]
fn test_validate_mixed_issues_and_no_issues_found() {
    let xml = r"<ralph-issues><ralph-issue>First issue</ralph-issue><ralph-no-issues-found>No issues</ralph-no-issues-found></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_err());
    let error = result.unwrap_err();
    assert!(error.suggestion.contains("not both") || error.expected.contains("not both"));
}

#[test]
fn test_validate_duplicate_no_issues_found() {
    let xml = r"<ralph-issues><ralph-no-issues-found>No issues</ralph-no-issues-found><ralph-no-issues-found>Also no issues</ralph-no-issues-found></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_err());
}

#[test]
fn test_validate_whitespace_handling() {
    // This is the key test - quick_xml should handle whitespace between elements
    let xml = "  <ralph-issues>  \n  <ralph-issue>Issue text</ralph-issue>  \n  </ralph-issues>  ";

    let result = validate_issues_xml(xml);
    assert!(result.is_ok());
}

#[test]
fn test_validate_with_xml_declaration() {
    let xml = r#"<?xml version="1.0"?><ralph-issues><ralph-issue>Issue text</ralph-issue></ralph-issues>"#;

    let result = validate_issues_xml(xml);
    assert!(result.is_ok());
}

#[test]
fn test_validate_issue_with_code_element() {
    // XSD now allows <code> elements for escaping special characters
    let xml = r"<ralph-issues><ralph-issue>Check if <code>a &lt; b</code> is valid</ralph-issue></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    assert_eq!(elements.issues.len(), 1);
    // The text from both outside and inside <code> should be collected
    assert!(elements.issues[0].text.contains("Check if"));
    assert!(elements.issues[0].text.contains("a < b"));
    assert!(elements.issues[0].text.contains("is valid"));
}

#[test]
fn test_validate_no_issues_with_code_element() {
    let xml = r"<ralph-issues><ralph-no-issues-found>All <code>Record&lt;string, T&gt;</code> types are correct</ralph-no-issues-found></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    assert!(elements.no_issues_found.is_some());
    let msg = elements.no_issues_found.unwrap();
    assert!(msg.contains("Record<string, T>"));
}

// =========================================================================
// REALISTIC LLM OUTPUT TESTS
// These test actual patterns that LLMs produce when following the prompts
// =========================================================================

#[test]
fn test_llm_realistic_issue_with_generic_type_escaped() {
    // LLM correctly escapes generic types per prompt instructions
    let xml = r"<ralph-issues>
<ralph-issue>[High] src/parser.rs:42 - The function <code>parse&lt;T&gt;</code> does not handle empty input.
Suggested fix: Add a check for empty input before parsing.</ralph-issue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_ok(), "Should parse escaped generic: {result:?}");
    let elements = result.unwrap();
    assert!(elements.issues[0].text.contains("parse<T>"));
}

#[test]
fn test_llm_realistic_issue_with_comparison_escaped() {
    // LLM correctly escapes comparison operators
    let xml = r"<ralph-issues>
<ralph-issue>[Medium] src/validate.rs:15 - The condition <code>count &lt; 0</code> should be <code>count &lt;= 0</code>.
Suggested fix: Change the comparison operator.</ralph-issue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Should parse escaped comparisons: {result:?}"
    );
    let elements = result.unwrap();
    assert!(elements.issues[0].text.contains("count < 0"));
    assert!(elements.issues[0].text.contains("count <= 0"));
}

#[test]
fn test_llm_realistic_issue_with_logical_operators_escaped() {
    // LLM escapes && and || operators
    let xml = r"<ralph-issues><ralph-issue>[Low] src/filter.rs:88 - The expression <code>a &amp;&amp; b || c</code> has ambiguous precedence.
Suggested fix: Add explicit parentheses.</ralph-issue></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Should parse escaped logical operators: {result:?}"
    );
    let elements = result.unwrap();
    assert!(elements.issues[0].text.contains("a && b || c"));
}

#[test]
fn test_llm_realistic_issue_with_rust_lifetime() {
    // LLM references Rust lifetime syntax
    let xml = r"<ralph-issues><ralph-issue>[High] src/buffer.rs:23 - The lifetime <code>&amp;'a str</code> should match the struct lifetime.
Suggested fix: Ensure lifetime annotations are consistent.</ralph-issue></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_ok(), "Should parse lifetime syntax: {result:?}");
    let elements = result.unwrap();
    assert!(elements.issues[0].text.contains("&'a str"));
}

#[test]
fn test_llm_realistic_issue_with_html_in_description() {
    // LLM describes HTML-related code
    let xml = r#"<ralph-issues><ralph-issue>[Medium] src/template.rs:56 - The HTML template uses <code>&lt;div class="container"&gt;</code> but should use semantic tags.
Suggested fix: Replace with appropriate semantic HTML elements.</ralph-issue></ralph-issues>"#;

    let result = validate_issues_xml(xml);
    assert!(result.is_ok(), "Should parse HTML in code: {result:?}");
    let elements = result.unwrap();
    assert!(elements.issues[0]
        .text
        .contains("<div class=\"container\">"));
}

#[test]
fn test_llm_realistic_no_issues_with_detailed_explanation() {
    // LLM provides detailed explanation when no issues found
    let xml = "<ralph-issues><ralph-no-issues-found>The implementation correctly handles all edge cases:\n- Input validation properly rejects values where <code>x &lt; 0</code>\n- The generic <code>Result&lt;T, E&gt;</code> type is used consistently\n- Error handling follows the project's established patterns\nNo issues require attention.</ralph-no-issues-found></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Should parse detailed no-issues: {result:?}"
    );
    let elements = result.unwrap();
    let msg = elements.no_issues_found.unwrap();
    assert!(msg.contains("x < 0"));
    assert!(msg.contains("Result<T, E>"));
}

#[test]
fn test_llm_realistic_multiple_issues_with_mixed_content() {
    // LLM reports multiple issues with various escaped content
    let xml = r"<ralph-issues><ralph-issue>[Critical] src/auth.rs:12 - SQL injection vulnerability: user input in <code>query &amp;&amp; filter</code> is not sanitized.</ralph-issue><ralph-issue>[High] src/api.rs:45 - Missing null check: <code>response.data</code> may be undefined when <code>status &lt; 200</code>.</ralph-issue><ralph-issue>[Medium] src/utils.rs:78 - The type <code>Option&lt;Vec&lt;T&gt;&gt;</code> could be simplified to <code>Vec&lt;T&gt;</code> with empty default.</ralph-issue></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Should parse multiple issues with mixed content: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(elements.issues.len(), 3);
    assert!(elements.issues[0].text.contains("query && filter"));
    assert!(elements.issues[1].text.contains("status < 200"));
    assert!(elements.issues[2].text.contains("Option<Vec<T>>"));
}

#[test]
fn test_llm_mistake_unescaped_less_than_fails() {
    // LLM forgets to escape < - this SHOULD fail
    let xml = r"<ralph-issues><ralph-issue>[High] src/compare.rs:10 - The condition a < b is wrong.</ralph-issue></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_err(),
        "Unescaped < should fail XML parsing: {result:?}"
    );
}

#[test]
fn test_llm_mistake_unescaped_generic_fails() {
    // LLM forgets to escape generic type - this SHOULD fail
    let xml = r"<ralph-issues><ralph-issue>[High] src/types.rs:5 - The type Vec<String> is incorrect.</ralph-issue></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_err(),
        "Unescaped generic should fail XML parsing: {result:?}"
    );
}

#[test]
fn test_llm_mistake_unescaped_ampersand_fails() {
    // LLM forgets to escape & - this SHOULD fail
    let xml = r"<ralph-issues><ralph-issue>[High] src/logic.rs:20 - The expression a && b is wrong.</ralph-issue></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_err(),
        "Unescaped && should fail XML parsing: {result:?}"
    );
}

#[test]
fn test_llm_uses_cdata_for_code_content() {
    // LLM uses CDATA instead of escaping (valid alternative)
    let xml = r"<ralph-issues><ralph-issue>[High] src/cmp.rs:10 - The condition <code><![CDATA[a < b && c > d]]></code> has issues.</ralph-issue></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_ok(), "CDATA should be valid: {result:?}");
    let elements = result.unwrap();
    assert!(elements.issues[0].text.contains("a < b && c > d"));
}

// =========================================================================
// TOLERANT PARSING TESTS
// These test the tolerant behavior: unknown elements are skipped,
// stray text is ignored. Issues has no enum status field, so no
// normalization tests are needed here.
// =========================================================================

#[test]
fn test_tolerant_issues_skips_unknown_elements() {
    // Extra elements like <ralph-analysis>...</ralph-analysis> should be skipped
    let xml = r"<ralph-issues>
<ralph-issue>First issue</ralph-issue>
<ralph-analysis>Some extra analysis that should be skipped</ralph-analysis>
<ralph-issue>Second issue</ralph-issue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Unknown elements should be skipped, not rejected: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(
        elements.issues.len(),
        2,
        "Issues should be parsed correctly despite unknown elements"
    );
}

#[test]
fn test_tolerant_issues_ignores_stray_text() {
    // Text between issue elements should be tolerated
    let xml = "<ralph-issues>\nsome stray text here\n<ralph-issue>First issue</ralph-issue>\nmore stray text\n</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Stray text between issue elements should be tolerated: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(
        elements.issues.len(),
        1,
        "Issue should be parsed correctly despite stray text"
    );
}

// =========================================================================
// ADDITIONAL TOLERANT PARSING TESTS
// These cover the self-closing empty element case (Event::Empty handler)
// and confirm issues has no enum fields requiring normalization.
// =========================================================================

#[test]
fn test_tolerant_issues_skips_self_closing_unknown_element() {
    // Self-closing unknown elements (Event::Empty) should be skipped
    let xml = r"<ralph-issues>
<ralph-meta/>
<ralph-issue>First issue</ralph-issue>
<ralph-tag/>
<ralph-issue>Second issue</ralph-issue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Self-closing unknown elements should be skipped, not rejected: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(
        elements.issues.len(),
        2,
        "Issues should be parsed correctly despite self-closing unknown elements"
    );
    assert_eq!(elements.issues[0].text, "First issue");
    assert_eq!(elements.issues[1].text, "Second issue");
}

#[test]
fn test_tolerant_issues_multiple_unknown_elements_interspersed() {
    // Multiple unknown elements interspersed between issue elements should be skipped
    let xml = r"<ralph-issues>
<ralph-preamble>some metadata</ralph-preamble>
<ralph-issue>Issue one</ralph-issue>
<ralph-analysis>extra analysis</ralph-analysis>
<ralph-issue>Issue two</ralph-issue>
<ralph-footer>end of response</ralph-footer>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Multiple unknown elements interspersed should be skipped: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(
        elements.issues.len(),
        2,
        "Only real issues should be collected, not unknown elements"
    );
    assert_eq!(elements.issues[0].text, "Issue one");
    assert_eq!(elements.issues[1].text, "Issue two");
}

#[test]
fn test_tolerant_no_issues_found_with_self_closing_unknown() {
    // Self-closing unknown elements alongside no-issues-found should be tolerated
    let xml = r"<ralph-issues>
<ralph-meta/>
<ralph-no-issues-found>No issues found during review</ralph-no-issues-found>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Self-closing unknown element alongside no-issues-found should be tolerated: {result:?}"
    );
    let elements = result.unwrap();
    assert!(elements.issues.is_empty());
    assert_eq!(
        elements.no_issues_found,
        Some("No issues found during review".to_string())
    );
}

// =========================================================================
// SKILLS-MCP FIELD TESTS
// =========================================================================

#[test]
fn test_issue_with_skills_mcp_parses_entries() {
    let xml = r#"<ralph-issues>
<ralph-issue>Retry metrics are not updated on continuation attempts.
<skills-mcp>
<skill reason="Capture the regression with a failing test">test-driven-development</skill>
<skill reason="Investigate first if the cause is not yet clear">systematic-debugging</skill>
</skills-mcp>
</ralph-issue>
</ralph-issues>"#;

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Issue with skills-mcp should parse: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(elements.issues.len(), 1);

    let issue = &elements.issues[0];
    assert!(
        issue.text.contains("Retry metrics"),
        "Issue text should contain issue description"
    );

    let sm = issue
        .skills_mcp
        .as_ref()
        .expect("skills_mcp should be present");
    assert_eq!(sm.skills.len(), 2);
    assert_eq!(sm.skills[0].name, "test-driven-development");
    assert_eq!(
        sm.skills[0].reason.as_deref(),
        Some("Capture the regression with a failing test")
    );
    assert_eq!(sm.skills[1].name, "systematic-debugging");
}

#[test]
fn test_issues_without_skills_mcp_still_work() {
    let xml = r"<ralph-issues>
<ralph-issue>First issue description</ralph-issue>
<ralph-issue>Second issue description</ralph-issue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Issues without skills-mcp should parse: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(elements.issues.len(), 2);
    assert!(
        elements.issues[0].skills_mcp.is_none(),
        "skills_mcp should be None when absent"
    );
    assert_eq!(elements.issues[0].text, "First issue description");
}

#[test]
fn test_issue_with_mcp_entry_in_skills_mcp() {
    let xml = r#"<ralph-issues>
<ralph-issue>Need to fix dependency research.
<skills-mcp>
<mcp reason="Use for dependency and library research">context7</mcp>
</skills-mcp>
</ralph-issue>
</ralph-issues>"#;

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Issue with mcp entry should parse: {result:?}"
    );
    let elements = result.unwrap();
    let sm = elements.issues[0]
        .skills_mcp
        .as_ref()
        .expect("skills_mcp should be present");
    assert_eq!(sm.mcps.len(), 1);
    assert_eq!(sm.mcps[0].name, "context7");
    assert_eq!(
        sm.mcps[0].reason.as_deref(),
        Some("Use for dependency and library research")
    );
}

#[test]
fn test_issue_with_malformed_skills_mcp_preserved() {
    let xml = r"<ralph-issues>
<ralph-issue>Some issue with malformed guidance.
<skills-mcp>Use systematic-debugging and test-driven-development</skills-mcp>
</ralph-issue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Issue with malformed skills-mcp should not cause overall failure: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(elements.issues.len(), 1);
    let sm = elements.issues[0]
        .skills_mcp
        .as_ref()
        .expect("skills_mcp should be present even if malformed");
    // Either raw_content or empty structured content - but must not fail
    assert!(
        sm.raw_content.is_some() || sm.skills.is_empty(),
        "Malformed content should be preserved"
    );
}

#[test]
fn test_multiple_issues_each_with_own_skills_mcp() {
    let xml = r#"<ralph-issues>
<ralph-issue>First issue about testing.
<skills-mcp>
<skill reason="Write test first">test-driven-development</skill>
</skills-mcp>
</ralph-issue>
<ralph-issue>Second issue about debugging.
<skills-mcp>
<skill reason="Investigate before coding">systematic-debugging</skill>
<mcp reason="Research the API">context7</mcp>
</skills-mcp>
</ralph-issue>
</ralph-issues>"#;

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Multiple issues each with skills-mcp should parse: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(elements.issues.len(), 2);

    let sm0 = elements.issues[0]
        .skills_mcp
        .as_ref()
        .expect("First issue should have skills_mcp");
    assert_eq!(sm0.skills.len(), 1);
    assert_eq!(sm0.skills[0].name, "test-driven-development");

    let sm1 = elements.issues[1]
        .skills_mcp
        .as_ref()
        .expect("Second issue should have skills_mcp");
    assert_eq!(sm1.skills.len(), 1);
    assert_eq!(sm1.mcps.len(), 1);
    assert_eq!(sm1.skills[0].name, "systematic-debugging");
    assert_eq!(sm1.mcps[0].name, "context7");
}

// =========================================================================
// REGRESSION TEST FOR BUG: NUL byte from NBSP typo
// =========================================================================

#[test]
fn test_validate_nul_byte_from_nbsp_typo() {
    // Regression test for bug where agent writes \u0000 instead of \u00A0
    // This simulates: .replace("git diff", "git\0A0diff")
    // The bug report shows this exact pattern in `.agent/tmp/issues.xml.processed`
    let xml =
        "<ralph-issues><ralph-issue>Check git\u{0000}A0diff usage</ralph-issue></ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(result.is_err(), "NUL byte should be rejected");

    let error = result.unwrap_err();
    assert!(
        error.found.contains("NUL") || error.found.contains("0x00"),
        "Error should identify NUL byte, got: {}",
        error.found
    );
    assert!(
        error.suggestion.contains("\\u00A0") || error.suggestion.contains("non-breaking space"),
        "Error should suggest NBSP as common fix, got: {}",
        error.suggestion
    );
}

// =========================================================================
// Fuzzy tag matching tests (Step 6 of implementation plan)
// =========================================================================

/// Test: misspelled ralph-isue tag resolves to ralph-issue.
#[test]
fn test_tolerant_issues_misspelled_issue_tag() {
    let xml = r"<ralph-issues>
<ralph-isue>First issue description</ralph-isue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Misspelled <ralph-isue> should be accepted: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(elements.issues.len(), 1, "One issue should be parsed");
    assert_eq!(
        elements.issues[0].text, "First issue description",
        "Issue content should be correctly extracted from misspelled tag"
    );
}

/// Test: misspelled ralph-no-isssues-found tag resolves to ralph-no-issues-found.
#[test]
fn test_tolerant_issues_misspelled_no_issues_found_tag() {
    let xml = r"<ralph-issues>
<ralph-no-isssues-found>No issues were found</ralph-no-isssues-found>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Misspelled <ralph-no-isssues-found> should be accepted: {result:?}"
    );
    let elements = result.unwrap();
    assert!(
        elements.no_issues_found.is_some(),
        "no_issues_found should be set"
    );
    assert_eq!(
        elements.no_issues_found.unwrap(),
        "No issues were found",
        "No-issues-found content should be correctly extracted"
    );
}

/// Test: fuzzy-resolved ralph-no-issues-found still enforces mutual exclusion.
#[test]
fn test_tolerant_issues_fuzzy_resolved_no_issues_still_enforces_mutual_exclusion() {
    // If a typo resolves to ralph-no-issues-found, it should still trigger the mixing check
    let xml = r"<ralph-issues>
<ralph-issue>Some issue</ralph-issue>
<ralph-no-isssues-found>No issues</ralph-no-isssues-found>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_err(),
        "Mixing ralph-issue with fuzzy-resolved ralph-no-issues-found should be rejected"
    );
    let error = result.unwrap_err();
    assert!(
        error.expected.contains("not both") || error.suggestion.contains("not both"),
        "Error should mention mutual exclusion: {:?}",
        error
    );
}

/// Test: completely unknown tag (large edit distance) is skipped.
#[test]
fn test_tolerant_issues_completely_unknown_tag_skipped() {
    let xml = r"<ralph-issues>
<ralph-issue>First issue</ralph-issue>
<ralph-banana>this should be ignored</ralph-banana>
<ralph-issue>Second issue</ralph-issue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Unknown tag with large edit distance should be skipped: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(elements.issues.len(), 2, "Both issues should be parsed");
}

/// Test: self-closing misspelled tag is also handled.
#[test]
fn test_tolerant_issues_self_closing_misspelled_tag() {
    let xml = r"<ralph-issues>
<ralph-isue/>
<ralph-issue>Actual issue</ralph-issue>
</ralph-issues>";

    let result = validate_issues_xml(xml);
    assert!(
        result.is_ok(),
        "Self-closing misspelled tag should be handled: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(
        elements.issues.len(),
        1,
        "Only the actual issue should be parsed"
    );
}
