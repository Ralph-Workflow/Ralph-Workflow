// ============================================================================
// Tests for commit message validator tolerant parsing
// ============================================================================

#[test]
fn test_commit_unknown_element_inside_ralph_commit_is_skipped() {
    // Unknown elements should be tolerated, not rejected
    let xml = r"<ralph-commit>
<ralph-subject>feat: add new feature</ralph-subject>
<unknown-extra-element>This should be skipped</unknown-extra-element>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_ok(),
        "Unknown element should be skipped, not rejected: {:?}",
        result.err()
    );
    let elements = result.unwrap();
    assert_eq!(elements.subject, "feat: add new feature");
}

#[test]
fn test_commit_multiple_unknown_elements_are_skipped() {
    let xml = r"<ralph-commit>
<ralph-subject>fix(api): resolve null pointer</ralph-subject>
<llm-thinking>Some reasoning by the LLM</llm-thinking>
<extra-metadata>Something else</extra-metadata>
<ralph-body>Fix description</ralph-body>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_ok(),
        "Multiple unknown elements should all be skipped: {:?}",
        result.err()
    );
    let elements = result.unwrap();
    assert_eq!(elements.subject, "fix(api): resolve null pointer");
    assert!(elements.body.is_some());
}

#[test]
fn test_commit_stray_text_between_elements_is_ignored() {
    // The commit validator does NOT use trim_text, so it uses whitespace-only text.
    // However non-whitespace stray text should now be tolerated too.
    let xml = "<ralph-commit>\n<ralph-subject>feat: add feature</ralph-subject>\n</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_ok(),
        "Whitespace text between elements should be ignored: {:?}",
        result.err()
    );
}

#[test]
fn test_commit_required_subject_still_enforced_after_unknown_elements() {
    // Required elements (ralph-subject or ralph-skip) must still be enforced
    let xml = r"<ralph-commit>
<unknown-element>Some content without subject</unknown-element>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Missing required subject/skip should still be rejected"
    );
    let error = result.unwrap_err();
    assert!(matches!(
        error.error_type,
        XsdErrorType::MissingRequiredElement
    ));
}

#[test]
fn test_commit_mutual_exclusivity_still_enforced_with_unknown_elements() {
    // ralph-skip and ralph-subject together should still be rejected
    let xml = r"<ralph-commit>
<unknown-element>Extra</unknown-element>
<ralph-skip>No changes</ralph-skip>
<ralph-subject>feat: something</ralph-subject>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Mixed skip and commit elements should still be rejected"
    );
}

#[test]
fn test_commit_unknown_self_closing_element_is_skipped() {
    // Self-closing unknown elements should also be tolerated
    let xml = r"<ralph-commit>
<ralph-subject>refactor: clean up code</ralph-subject>
<metadata key1=&quot;value1&quot;/>
</ralph-commit>";

    // Note: &quot; in attribute value is valid XML, so this should parse
    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_ok(),
        "Self-closing unknown element should be skipped: {:?}",
        result.err()
    );
}

#[test]
fn test_commit_truly_malformed_xml_still_rejected() {
    // Malformed XML (unclosed tags, wrong nesting) must still be rejected
    let xml = "<ralph-commit><ralph-subject>feat: test</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Truly malformed XML should still be rejected"
    );
}

#[test]
fn test_commit_valid_skip_with_unknown_elements_is_accepted() {
    // Unknown elements alongside ralph-skip should be tolerated
    let xml = r"<ralph-commit>
<ralph-skip>No changes needed</ralph-skip>
<llm-reasoning>The analysis showed no changes were necessary</llm-reasoning>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_ok(),
        "Unknown element alongside ralph-skip should be tolerated: {:?}",
        result.err()
    );
    let elements = result.unwrap();
    assert!(elements.skip_reason.is_some());
    assert_eq!(elements.skip_reason.unwrap(), "No changes needed");
}
