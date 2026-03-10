// ============================================================================
// Tests for validate_xml_against_xsd()
// ============================================================================

#[test]
fn test_validate_valid_minimal_xml() {
    let xml = r"<ralph-commit>
<ralph-subject>feat: add new feature</ralph-subject>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    assert_eq!(elements.subject, "feat: add new feature");
    assert!(elements.body.is_none());
    assert!(elements.body_summary.is_none());
}

#[test]
fn test_validate_valid_xml_with_body() {
    let xml = r"<ralph-commit>
<ralph-subject>fix(api): resolve null pointer</ralph-subject>
<ralph-body>This fixes the null pointer issue in the API handler.</ralph-body>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    assert_eq!(elements.subject, "fix(api): resolve null pointer");
    assert_eq!(
        elements.body,
        Some("This fixes the null pointer issue in the API handler.".to_string())
    );
}

#[test]
fn test_validate_valid_xml_with_detailed_body() {
    let xml = r"<ralph-commit>
<ralph-subject>docs: update API documentation</ralph-subject>
<ralph-body-summary>Updated the API documentation to reflect recent changes.</ralph-body-summary>
<ralph-body-details>- Added new endpoints
- Updated request/response examples
- Fixed typos in authentication section</ralph-body-details>
<ralph-body-footer>Closes #123</ralph-body-footer>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    assert_eq!(elements.subject, "docs: update API documentation");
    assert!(elements.body.is_none());
    assert_eq!(
        elements.body_summary,
        Some("Updated the API documentation to reflect recent changes.".to_string())
    );
    assert!(elements.body_details.is_some());
    assert_eq!(elements.body_footer, Some("Closes #123".to_string()));
}

#[test]
fn test_validate_with_xml_declaration() {
    let xml = r#"<?xml version="1.0" encoding="UTF-8"?>
<ralph-commit>
<ralph-subject>test: add unit tests</ralph-subject>
</ralph-commit>"#;

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok());
}

#[test]
fn test_validate_missing_root_element() {
    let xml = r"Some random text without proper XML tags";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_err());
    let error = result.unwrap_err();
    assert!(matches!(
        error.error_type,
        XsdErrorType::MissingRequiredElement
    ));
    assert_eq!(error.element_path, "ralph-commit");
}

#[test]
fn test_validate_missing_closing_tag() {
    let xml = r"<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_err());
    let error = result.unwrap_err();
    assert!(matches!(error.error_type, XsdErrorType::MalformedXml));
}

#[test]
fn test_validate_rejects_invalid_escape_sequences() {
    // The validator must not downgrade unescape errors into empty strings.
    let xml = r"<ralph-commit>
<ralph-subject>feat: test &bad;</ralph-subject>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err for invalid XML escape/entity sequences"
    );
}

#[test]
fn test_validate_missing_subject() {
    let xml = r"<ralph-commit>
<ralph-body>Some body text</ralph-body>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_err());
    let error = result.unwrap_err();
    assert!(matches!(
        error.error_type,
        XsdErrorType::MissingRequiredElement
    ));
    // Error should indicate that either ralph-subject or ralph-skip is required
    assert!(error.element_path.contains("ralph-commit"));
}

#[test]
fn test_validate_empty_subject() {
    let xml = r"<ralph-commit>
<ralph-subject>   </ralph-subject>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_err());
    let error = result.unwrap_err();
    assert!(matches!(error.error_type, XsdErrorType::InvalidContent));
    assert_eq!(error.element_path, "ralph-subject");
}

#[test]
fn test_validate_invalid_conventional_commit_format() {
    let xml = r"<ralph-commit>
<ralph-subject>This is not a conventional commit</ralph-subject>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_err());
    let error = result.unwrap_err();
    assert!(matches!(error.error_type, XsdErrorType::InvalidContent));
    assert!(error.suggestion.contains("conventional commit format"));
}

#[test]
fn test_validate_duplicate_subject() {
    let xml = r"<ralph-commit>
<ralph-subject>feat: first subject</ralph-subject>
<ralph-subject>feat: second subject</ralph-subject>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_err());
    let error = result.unwrap_err();
    assert!(matches!(error.error_type, XsdErrorType::UnexpectedElement));
    assert!(error.found.contains("duplicate"));
}

#[test]
fn test_validate_mixed_simple_and_detailed_body() {
    let xml = r"<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
<ralph-body>Simple body</ralph-body>
<ralph-body-summary>Detailed summary</ralph-body-summary>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_err());
    let error = result.unwrap_err();
    assert!(matches!(error.error_type, XsdErrorType::UnexpectedElement));
    assert!(error.suggestion.contains("<ralph-body>"));
}

#[test]
fn test_validate_whitespace_handling() {
    let xml = r"
  <ralph-commit>

    <ralph-subject>feat: add feature</ralph-subject>

    <ralph-body>
      Body with whitespace
    </ralph-body>

  </ralph-commit>
";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    assert_eq!(elements.subject, "feat: add feature");
    assert!(elements
        .body
        .as_ref()
        .unwrap()
        .contains("Body with whitespace"));
}

#[test]
fn test_validate_with_escaped_newlines_in_content() {
    // This tests that quick_xml properly handles whitespace between elements
    // which was the original issue with literal \n characters
    let xml = "<ralph-commit>\n<ralph-subject>feat: test</ralph-subject>\n</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok());
}

#[test]
fn test_validate_rejects_commit_elements_after_skip() {
    // Regression: if <ralph-skip> appears before commit message elements, the validator
    // must reject the mixed content rather than silently accepting skip and ignoring
    // the later commit message.
    let xml = r"<ralph-commit>
<ralph-skip>No changes found</ralph-skip>
<ralph-subject>feat: should not be accepted</ralph-subject>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected mixed skip+commit elements to be rejected"
    );
}
