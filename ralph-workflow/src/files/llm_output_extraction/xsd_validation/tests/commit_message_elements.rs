// ============================================================================
// Tests for CommitMessageElements::format_body()
// ============================================================================

#[test]
fn test_format_body_with_simple_body() {
    let elements = CommitMessageElements {
        subject: "feat: test".to_string(),
        body: Some("Simple body text".to_string()),
        body_summary: None,
        body_details: None,
        body_footer: None,
        skip_reason: None,
        files: vec![],
        excluded_files: vec![],
    };

    assert_eq!(elements.format_body(), "Simple body text");
}

#[test]
fn test_format_body_with_detailed_elements() {
    let elements = CommitMessageElements {
        subject: "feat: test".to_string(),
        body: None,
        body_summary: Some("Summary line".to_string()),
        body_details: Some("Detailed explanation".to_string()),
        body_footer: Some("Footer text".to_string()),
        skip_reason: None,
        files: vec![],
        excluded_files: vec![],
    };

    let formatted = elements.format_body();
    assert!(formatted.contains("Summary line"));
    assert!(formatted.contains("Detailed explanation"));
    assert!(formatted.contains("Footer text"));
}

#[test]
fn test_format_body_empty_when_no_body() {
    let elements = CommitMessageElements {
        subject: "feat: test".to_string(),
        body: None,
        body_summary: None,
        body_details: None,
        body_footer: None,
        skip_reason: None,
        files: vec![],
        excluded_files: vec![],
    };

    assert_eq!(elements.format_body(), "");
}

#[test]
fn test_format_body_skips_whitespace_only_detailed_sections() {
    let elements = CommitMessageElements {
        subject: "feat: test".to_string(),
        body: None,
        body_summary: Some("   \n\t".to_string()),
        body_details: Some("\t  ".to_string()),
        body_footer: Some("Footer text".to_string()),
        skip_reason: None,
        files: vec![],
        excluded_files: vec![],
    };

    assert_eq!(elements.format_body(), "Footer text");
}

// ============================================================================
// Tests for ralph-files element support
// ============================================================================

#[test]
fn test_validate_xml_with_files_list() {
    let xml = r"<ralph-commit>
<ralph-subject>feat(auth): add OAuth2 login flow</ralph-subject>
<ralph-body>Add Google and GitHub providers.</ralph-body>
<ralph-files>
<ralph-file>src/auth/oauth.rs</ralph-file>
<ralph-file>tests/auth_test.rs</ralph-file>
</ralph-files>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok(), "Expected OK, got: {result:?}");
    let elements = result.unwrap();
    assert_eq!(elements.files.len(), 2);
    assert_eq!(elements.files[0], "src/auth/oauth.rs");
    assert_eq!(elements.files[1], "tests/auth_test.rs");
}

#[test]
fn test_validate_xml_no_files_gives_empty_vec() {
    let xml = r"<ralph-commit>
<ralph-subject>refactor: extract validation into module</ralph-subject>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok(), "Expected OK, got: {result:?}");
    let elements = result.unwrap();
    assert_eq!(elements.files, Vec::<String>::new());
}

#[test]
fn test_validate_xml_files_with_skip_is_rejected() {
    let xml = r"<ralph-commit>
<ralph-skip>No changes found</ralph-skip>
<ralph-files>
<ralph-file>src/foo.rs</ralph-file>
</ralph-files>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when ralph-files used with ralph-skip"
    );
}

#[test]
fn test_validate_xml_files_empty_list_is_rejected() {
    let xml = r"<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
<ralph-files>
</ralph-files>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when ralph-files has no ralph-file children"
    );
}

#[test]
fn test_validate_xml_files_self_closing_is_rejected() {
    // Regression: quick_xml emits Event::Empty for <ralph-files/>, which must be treated as
    // a present-but-empty container and therefore invalid.
    let xml = r"<ralph-commit>
 <ralph-subject>feat: add feature</ralph-subject>
 <ralph-files/>
 </ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when ralph-files is self-closing (empty)"
    );
}

#[test]
fn test_validate_xml_multiple_files_blocks_is_rejected() {
    let xml = r"<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
<ralph-files>
<ralph-file>src/foo.rs</ralph-file>
</ralph-files>
<ralph-files>
<ralph-file>src/bar.rs</ralph-file>
</ralph-files>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when ralph-files appears more than once"
    );
}

#[test]
fn test_validate_xml_files_rejects_unexpected_child_element() {
    let xml = r"<ralph-commit>
 <ralph-subject>feat: add feature</ralph-subject>
 <ralph-files>
 <ralph-file>src/foo.rs</ralph-file>
 <unexpected>src/bar.rs</unexpected>
 </ralph-files>
 </ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when ralph-files contains an unexpected child element"
    );
}

#[test]
fn test_validate_xml_files_rejects_non_whitespace_text() {
    let xml = r"<ralph-commit>
 <ralph-subject>feat: add feature</ralph-subject>
 <ralph-files>
 text that should not be here
 <ralph-file>src/foo.rs</ralph-file>
 </ralph-files>
 </ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when ralph-files contains non-whitespace text"
    );
}

// ============================================================================
// Tests for ralph-excluded-files element support
// ============================================================================

#[test]
fn test_validate_xml_with_excluded_files_internal_ignore() {
    let xml = r#"<ralph-commit>
<ralph-subject>feat(auth): add OAuth2 login flow</ralph-subject>
<ralph-files>
<ralph-file>src/auth/oauth.rs</ralph-file>
</ralph-files>
<ralph-excluded-files>
<ralph-excluded-file reason="internal-ignore">.agent/tmp/trace.log</ralph-excluded-file>
</ralph-excluded-files>
</ralph-commit>"#;

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_ok(),
        "Expected OK for valid excluded-files, got: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(elements.excluded_files.len(), 1);
    assert_eq!(elements.excluded_files[0].path, ".agent/tmp/trace.log");
    assert!(matches!(
        elements.excluded_files[0].reason,
        crate::reducer::state::pipeline::ExcludedFileReason::InternalIgnore
    ));
}

#[test]
fn test_validate_xml_with_excluded_files_all_reason_values() {
    for reason_attr in [
        "internal-ignore",
        "not-task-related",
        "sensitive",
        "deferred",
    ] {
        let xml = format!(
            r#"<ralph-commit>
<ralph-subject>feat: test</ralph-subject>
<ralph-excluded-files>
<ralph-excluded-file reason="{reason_attr}">some/file.rs</ralph-excluded-file>
</ralph-excluded-files>
</ralph-commit>"#
        );
        let result = validate_xml_against_xsd(&xml);
        assert!(
            result.is_ok(),
            "Expected OK for reason={reason_attr}, got: {result:?}"
        );
    }
}

#[test]
fn test_validate_xml_excluded_files_missing_reason_is_rejected() {
    let xml = r"<ralph-commit>
<ralph-subject>feat: test</ralph-subject>
<ralph-excluded-files>
<ralph-excluded-file>.agent/tmp/trace.log</ralph-excluded-file>
</ralph-excluded-files>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when reason attribute is missing"
    );
}

#[test]
fn test_validate_xml_excluded_files_unknown_reason_is_rejected() {
    let xml = r#"<ralph-commit>
<ralph-subject>feat: test</ralph-subject>
<ralph-excluded-files>
<ralph-excluded-file reason="unknown-reason">some/file.rs</ralph-excluded-file>
</ralph-excluded-files>
</ralph-commit>"#;

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_err(), "Expected Err when reason value is unknown");
}

#[test]
fn test_validate_xml_excluded_files_empty_list_is_rejected() {
    let xml = r"<ralph-commit>
<ralph-subject>feat: test</ralph-subject>
<ralph-excluded-files>
</ralph-excluded-files>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when ralph-excluded-files has no children"
    );
}

#[test]
fn test_validate_xml_excluded_files_self_closing_is_rejected() {
    // Regression: quick_xml emits Event::Empty for <ralph-excluded-files/>, which must not be
    // silently ignored (it would incorrectly behave as if the element was absent).
    let xml = r"<ralph-commit>
 <ralph-subject>feat: test</ralph-subject>
 <ralph-excluded-files/>
 </ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when ralph-excluded-files is self-closing (empty)"
    );
}

#[test]
fn test_validate_xml_excluded_file_self_closing_is_rejected() {
    let xml = r#"<ralph-commit>
  <ralph-subject>feat: test</ralph-subject>
  <ralph-excluded-files>
  <ralph-excluded-file reason="deferred"/>
  </ralph-excluded-files>
  </ralph-commit>"#;

    let result = validate_xml_against_xsd(xml);
    let err = result.expect_err("Expected Err when excluded-file is self-closing");
    assert_eq!(err.error_type, XsdErrorType::InvalidContent);
    assert_eq!(
        err.element_path,
        "ralph-commit/ralph-excluded-files/ralph-excluded-file"
    );
    assert!(
        err.found.contains("<ralph-excluded-file"),
        "Unexpected found value: {}",
        err.found
    );
}

#[test]
fn test_validate_xml_without_excluded_files_is_valid() {
    let xml = r"<ralph-commit>
<ralph-subject>feat: test</ralph-subject>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok(), "Expected OK without ralph-excluded-files");
    assert!(result.unwrap().excluded_files.is_empty());
}

#[test]
fn test_validate_xml_excluded_files_coexist_with_files() {
    let xml = r#"<ralph-commit>
<ralph-subject>feat: test</ralph-subject>
<ralph-files>
<ralph-file>src/main.rs</ralph-file>
</ralph-files>
<ralph-excluded-files>
<ralph-excluded-file reason="deferred">src/other.rs</ralph-excluded-file>
</ralph-excluded-files>
</ralph-commit>"#;

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_ok(),
        "Expected OK when ralph-files and ralph-excluded-files coexist, got: {result:?}"
    );
    let elements = result.unwrap();
    assert_eq!(elements.files.len(), 1);
    assert_eq!(elements.excluded_files.len(), 1);
}

#[test]
fn test_validate_xml_excluded_files_rejects_non_whitespace_cdata() {
    let xml = r#"<ralph-commit>
<ralph-subject>feat: test</ralph-subject>
<ralph-excluded-files>
<![CDATA[not allowed]]>
<ralph-excluded-file reason="deferred">src/other.rs</ralph-excluded-file>
</ralph-excluded-files>
</ralph-commit>"#;

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when ralph-excluded-files contains non-whitespace CDATA"
    );
}

#[test]
fn test_validate_xml_excluded_files_with_skip_is_rejected() {
    let xml = r#"<ralph-commit>
<ralph-skip>No changes found</ralph-skip>
<ralph-excluded-files>
<ralph-excluded-file reason="internal-ignore">.agent/tmp/trace.log</ralph-excluded-file>
</ralph-excluded-files>
</ralph-commit>"#;

    let result = validate_xml_against_xsd(xml);
    assert!(
        result.is_err(),
        "Expected Err when ralph-excluded-files used with ralph-skip"
    );
}

// ============================================================================
// Tests for XsdErrorType::description()
// ============================================================================

#[test]
fn test_error_type_descriptions() {
    assert_eq!(
        XsdErrorType::MissingRequiredElement.description(),
        "Missing required element"
    );
    assert_eq!(
        XsdErrorType::UnexpectedElement.description(),
        "Unexpected element"
    );
    assert_eq!(
        XsdErrorType::InvalidContent.description(),
        "Invalid content"
    );
    assert_eq!(XsdErrorType::MalformedXml.description(), "Malformed XML");
}

// ============================================================================
// Tests for <code> element support
// ============================================================================

#[test]
fn test_validate_subject_with_code_element() {
    // XSD allows <code> elements for escaping special characters
    let xml = r"<ralph-commit>
<ralph-subject>fix: handle <code>a &lt; b</code> comparison</ralph-subject>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    // Text from both outside and inside <code> should be collected
    assert!(elements.subject.contains("fix: handle"));
    assert!(elements.subject.contains("a < b"));
    assert!(elements.subject.contains("comparison"));
}

#[test]
fn test_validate_body_with_code_element() {
    let xml = r"<ralph-commit>
<ralph-subject>feat: add generic support</ralph-subject>
<ralph-body>Added <code>HashMap&lt;K, V&gt;</code> support to the parser.</ralph-body>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    let body = elements.body.unwrap();
    assert!(body.contains("HashMap<K, V>"));
}

#[test]
fn test_validate_detailed_body_with_code_elements() {
    let xml = r"<ralph-commit>
<ralph-subject>refactor: improve type handling</ralph-subject>
<ralph-body-summary>Refactored <code>Option&lt;T&gt;</code> handling</ralph-body-summary>
<ralph-body-details>Changed <code>if a &lt; b</code> to <code>if a &gt; b</code></ralph-body-details>
</ralph-commit>";

    let result = validate_xml_against_xsd(xml);
    assert!(result.is_ok());
    let elements = result.unwrap();
    assert!(elements.body_summary.unwrap().contains("Option<T>"));
    let details = elements.body_details.unwrap();
    assert!(details.contains("if a < b"));
    assert!(details.contains("if a > b"));
}
