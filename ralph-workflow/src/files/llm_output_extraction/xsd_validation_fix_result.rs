//! XSD validation for fix result XML format.
//!
//! This module provides validation of XML output against the XSD schema
//! to ensure AI agent output conforms to the expected format for fix results.
//!
//! Uses `quick_xml` for robust XML parsing with proper whitespace handling.

use crate::files::llm_output_extraction::xml_helpers::{
    create_reader, duplicate_element_error, format_content_preview, malformed_xml_error,
    missing_required_error, read_text_until_end, read_text_until_end_fuzzy, skip_to_end,
    tolerant_parsing::{normalize_enum_value, normalize_tag_name, FIX_STATUS_SYNONYMS},
};
use crate::files::llm_output_extraction::xsd_validation::{XsdErrorType, XsdValidationError};
use quick_xml::events::Event;

/// Example of a valid fix result XML for error messages.
const EXAMPLE_FIX_RESULT_XML: &str = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summary>Fixed all 3 issues found during review</ralph-summary>
</ralph-fix-result>";

/// Valid status values for fix results.
const VALID_STATUSES: [&str; 3] = ["all_issues_addressed", "issues_remain", "no_issues_found"];

/// Known child element tags for fix result validation.
/// Used for fuzzy tag name matching (typo tolerance).
const KNOWN_FIX_RESULT_TAGS: &[&str] = &["ralph-status", "ralph-summary"];

/// Validate fix result XML content against the XSD schema.
///
/// This function validates that the XML content conforms to the expected
/// fix result format defined in `fix_result.xsd`:
///
/// ```xml
/// <ralph-fix-result>
///   <ralph-status>all_issues_addressed|issues_remain|no_issues_found</ralph-status>
///   <ralph-summary>Optional summary of fixes applied</ralph-summary>
/// </ralph-fix-result>
/// ```
///
/// # Arguments
///
/// * `xml_content` - The XML content to validate
///
/// # Returns
///
/// * `Ok(FixResultElements)` if the XML is valid and contains all required elements
/// * `Err(XsdValidationError)` if the XML is invalid or doesn't conform to the schema
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn validate_fix_result_xml(xml_content: &str) -> Result<FixResultElements, XsdValidationError> {
    use crate::files::llm_output_extraction::xml_helpers::check_for_illegal_xml_characters;

    let content = xml_content.trim();

    // Check for illegal XML characters BEFORE parsing
    check_for_illegal_xml_characters(content)?;

    let parsed_state =
        parse_fix_result_state(content, &mut create_reader(content), &mut Vec::new())?;
    let FixResultParseState { status, summary } = parsed_state;

    // Validate required element: status
    let status = status.ok_or_else(|| {
        missing_required_error(
            "ralph-status",
            "ralph-fix-result",
            Some(EXAMPLE_FIX_RESULT_XML),
        )
    })?;

    // Validate status content
    if status.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-status".to_string(),
            expected: "non-empty status value".to_string(),
            found: "empty status".to_string(),
            suggestion: format!(
                "The <ralph-status> must contain one of: {}",
                VALID_STATUSES.join(", ")
            ),
            example: Some(EXAMPLE_FIX_RESULT_XML.into()),
        });
    }

    // Tolerant: normalize status via synonym table (case-insensitive, synonym mapping).
    // Returns the canonical form if the value is recognized, or None if truly ambiguous.
    let Some(status) = normalize_enum_value(&status, &VALID_STATUSES, FIX_STATUS_SYNONYMS) else {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-status".to_string(),
            expected: format!("one of: {}", VALID_STATUSES.join(", ")),
            found: status.clone(),
            suggestion: format!(
                "Change <ralph-status>{}</ralph-status> to use a valid value: {}",
                status,
                VALID_STATUSES.join(", ")
            ),
            example: Some(EXAMPLE_FIX_RESULT_XML.into()),
        });
    };

    Ok(FixResultElements {
        status,
        summary: summary.filter(|s| !s.is_empty()),
    })
}

#[derive(Debug, Default)]
struct FixResultParseState {
    status: Option<String>,
    summary: Option<String>,
}

fn parse_fix_result_state(
    content: &str,
    reader: &mut quick_xml::Reader<&[u8]>,
    buf: &mut Vec<u8>,
) -> Result<FixResultParseState, XsdValidationError> {
    find_fix_root(content, reader, buf)
        .and_then(|_| parse_fix_children(reader, buf, FixResultParseState::default()))
}

fn find_fix_root(
    content: &str,
    reader: &mut quick_xml::Reader<&[u8]>,
    buf: &mut Vec<u8>,
) -> Result<(), XsdValidationError> {
    *buf = Vec::new();
    match reader.read_event_into(buf) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"ralph-fix-result" => Ok(()),
        Ok(Event::Start(e)) => {
            let name_bytes = e.name();
            let tag_name = String::from_utf8_lossy(name_bytes.as_ref());
            Err(XsdValidationError {
                error_type: XsdErrorType::MissingRequiredElement,
                element_path: "ralph-fix-result".to_string(),
                expected: "<ralph-fix-result> as root element".to_string(),
                found: format!("<{tag_name}> (wrong root element)"),
                suggestion: "Use <ralph-fix-result> as the root element.".to_string(),
                example: Some(EXAMPLE_FIX_RESULT_XML.into()),
            })
        }
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-fix-result".to_string(),
            expected: "<ralph-fix-result> as root element".to_string(),
            found: format_content_preview(content),
            suggestion: "Wrap your result in <ralph-fix-result>...</ralph-fix-result> tags."
                .to_string(),
            example: Some(EXAMPLE_FIX_RESULT_XML.into()),
        }),
        Ok(_) => find_fix_root(content, reader, buf),
        Err(e) => Err(malformed_xml_error(&e)),
    }
}

fn parse_fix_children(
    reader: &mut quick_xml::Reader<&[u8]>,
    buf: &mut Vec<u8>,
    state: FixResultParseState,
) -> Result<FixResultParseState, XsdValidationError> {
    *buf = Vec::new();
    match reader.read_event_into(buf) {
        Ok(Event::Start(e)) => {
            let next_state = parse_fix_start_child(reader, &state, e.name().as_ref())?;
            parse_fix_children(reader, buf, next_state)
        }
        Ok(Event::Text(_)) => parse_fix_children(reader, buf, state),
        Ok(Event::Empty(e)) => {
            let next_state = parse_fix_empty_child(&state, e.name().as_ref())?;
            parse_fix_children(reader, buf, next_state)
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"ralph-fix-result" => Ok(state),
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "ralph-fix-result".to_string(),
            expected: "closing </ralph-fix-result> tag".to_string(),
            found: "end of content without closing tag".to_string(),
            suggestion: "Add </ralph-fix-result> at the end.".to_string(),
            example: Some(EXAMPLE_FIX_RESULT_XML.into()),
        }),
        Ok(_) => parse_fix_children(reader, buf, state),
        Err(e) => Err(malformed_xml_error(&e)),
    }
}

fn parse_fix_start_child(
    reader: &mut quick_xml::Reader<&[u8]>,
    state: &FixResultParseState,
    tag: &[u8],
) -> Result<FixResultParseState, XsdValidationError> {
    match tag {
        b"ralph-status" => with_status(
            state,
            read_text_until_end(reader, b"ralph-status")?,
            "ralph-status",
        ),
        b"ralph-summary" => with_summary(
            state,
            read_text_until_end(reader, b"ralph-summary")?,
            "ralph-summary",
        ),
        other => parse_fix_start_child_fuzzy(reader, state, other),
    }
}

fn parse_fix_start_child_fuzzy(
    reader: &mut quick_xml::Reader<&[u8]>,
    state: &FixResultParseState,
    original_tag: &[u8],
) -> Result<FixResultParseState, XsdValidationError> {
    match normalize_fix_child_tag(original_tag) {
        Some("ralph-status") => with_status(
            state,
            read_text_until_end_fuzzy(reader, b"ralph-status", original_tag)?,
            "ralph-status",
        ),
        Some("ralph-summary") => with_summary(
            state,
            read_text_until_end_fuzzy(reader, b"ralph-summary", original_tag)?,
            "ralph-summary",
        ),
        Some(_) => {
            let _ = skip_to_end(reader, original_tag);
            Ok(FixResultParseState {
                status: state.status.clone(),
                summary: state.summary.clone(),
            })
        }
        None => {
            let _ = skip_to_end(reader, original_tag);
            Ok(FixResultParseState {
                status: state.status.clone(),
                summary: state.summary.clone(),
            })
        }
    }
}

fn parse_fix_empty_child(
    state: &FixResultParseState,
    tag: &[u8],
) -> Result<FixResultParseState, XsdValidationError> {
    match tag {
        b"ralph-status" => with_status(state, String::new(), "ralph-status"),
        b"ralph-summary" => with_summary(state, String::new(), "ralph-summary"),
        other => match normalize_fix_child_tag(other) {
            Some("ralph-status") => with_status(state, String::new(), "ralph-status"),
            Some("ralph-summary") => with_summary(state, String::new(), "ralph-summary"),
            Some(_) | None => Ok(FixResultParseState {
                status: state.status.clone(),
                summary: state.summary.clone(),
            }),
        },
    }
}

fn with_status(
    state: &FixResultParseState,
    status: String,
    element_name: &'static str,
) -> Result<FixResultParseState, XsdValidationError> {
    state
        .status
        .as_ref()
        .map(|_| duplicate_element_error(element_name, "ralph-fix-result"))
        .map_or_else(
            || {
                Ok(FixResultParseState {
                    status: Some(status),
                    summary: state.summary.clone(),
                })
            },
            Err,
        )
}

fn with_summary(
    state: &FixResultParseState,
    summary: String,
    element_name: &'static str,
) -> Result<FixResultParseState, XsdValidationError> {
    state
        .summary
        .as_ref()
        .map(|_| duplicate_element_error(element_name, "ralph-fix-result"))
        .map_or_else(
            || {
                Ok(FixResultParseState {
                    status: state.status.clone(),
                    summary: Some(summary),
                })
            },
            Err,
        )
}

fn normalize_fix_child_tag(tag: &[u8]) -> Option<&'static str> {
    let tag_name = String::from_utf8_lossy(tag);
    normalize_tag_name(&tag_name, KNOWN_FIX_RESULT_TAGS)
}

/// Parsed fix result elements from valid XML.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FixResultElements {
    /// The fix status (required).
    ///
    /// This field always contains a canonical, normalized status value. The validator
    /// applies tolerant parsing (see `xml_helpers::tolerant_parsing::normalize_enum_value`)
    /// before storing the status, so this field is guaranteed to be one of the canonical
    /// values: `"all_issues_addressed"`, `"issues_remain"`, or `"no_issues_found"`.
    ///
    /// Downstream consumers can safely use exact string comparison
    /// without needing to handle synonym values or case variations.
    pub status: String,
    /// Optional summary of fixes applied
    pub summary: Option<String>,
}

impl FixResultElements {
    /// Returns true if all issues have been addressed or no issues were found.
    #[must_use]
    pub fn is_complete(&self) -> bool {
        self.status == "all_issues_addressed" || self.status == "no_issues_found"
    }

    /// Returns true if issues remain.
    #[must_use]
    pub fn has_remaining_issues(&self) -> bool {
        self.status == "issues_remain"
    }

    /// Returns true if no issues were found.
    #[must_use]
    pub fn is_no_issues(&self) -> bool {
        self.status == "no_issues_found"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_valid_all_issues_addressed() {
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "all_issues_addressed");
        assert!(elements.is_complete());
        assert!(!elements.has_remaining_issues());
    }

    #[test]
    fn test_validate_valid_issues_remain() {
        let xml = r"<ralph-fix-result>
<ralph-status>issues_remain</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "issues_remain");
        assert!(elements.has_remaining_issues());
        assert!(!elements.is_complete());
    }

    #[test]
    fn test_validate_valid_no_issues_found() {
        let xml = r"<ralph-fix-result>
<ralph-status>no_issues_found</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "no_issues_found");
        assert!(elements.is_no_issues());
    }

    #[test]
    fn test_validate_valid_with_summary() {
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summary>All reported issues have been fixed</ralph-summary>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(result.is_ok());
        let elements = result.unwrap();
        assert_eq!(elements.status, "all_issues_addressed");
        assert_eq!(
            elements.summary,
            Some("All reported issues have been fixed".to_string())
        );
    }

    #[test]
    fn test_validate_missing_root_element() {
        let xml = r"Some random text without proper XML tags";

        let result = validate_fix_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert_eq!(error.element_path, "ralph-fix-result");
    }

    #[test]
    fn test_validate_missing_status() {
        let xml = r"<ralph-fix-result>
<ralph-summary>No status</ralph-summary>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert!(error.element_path.contains("ralph-status"));
    }

    #[test]
    fn test_validate_invalid_status() {
        let xml = r"<ralph-fix-result>
<ralph-status>invalid_status_value</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(result.is_err());
        let error = result.unwrap_err();
        assert!(error.expected.contains("all_issues_addressed"));
    }

    #[test]
    fn test_tolerant_fix_status_synonym_fixed() {
        // "fixed" should map to "all_issues_addressed"
        let xml = r"<ralph-fix-result>
<ralph-status>fixed</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Synonym 'fixed' should be accepted as 'all_issues_addressed': {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(
            elements.status, "all_issues_addressed",
            "Synonym 'fixed' should be normalized to 'all_issues_addressed'"
        );
    }

    #[test]
    fn test_tolerant_fix_status_case_insensitive() {
        // ALL_ISSUES_ADDRESSED should be accepted (case-insensitive)
        let xml = r"<ralph-fix-result>
<ralph-status>ALL_ISSUES_ADDRESSED</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Case-insensitive 'ALL_ISSUES_ADDRESSED' should be accepted: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(
            elements.status, "all_issues_addressed",
            "Case-insensitive 'ALL_ISSUES_ADDRESSED' should be normalized to lowercase"
        );
    }

    #[test]
    fn test_tolerant_fix_skips_unknown_elements() {
        // Extra elements alongside valid ones should be skipped
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summary>All fixed</ralph-summary>
<ralph-extra-info>some extra info</ralph-extra-info>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Unknown elements should be skipped, not rejected: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "all_issues_addressed");
    }

    #[test]
    fn test_truly_unknown_fix_status_rejected() {
        // Ambiguous/unknown status values should still be rejected
        let xml = r"<ralph-fix-result>
<ralph-status>banana</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_err(),
            "Truly unknown status 'banana' should still be rejected"
        );
        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error should reference ralph-status"
        );
    }

    #[test]
    fn test_validate_empty_status() {
        let xml = r"<ralph-fix-result>
<ralph-status>   </ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(result.is_err());
    }

    #[test]
    fn test_tolerant_fix_status_synonym_all_fixed_maps_to_all_issues_addressed() {
        let xml = r"<ralph-fix-result>
<ralph-status>all_fixed</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Synonym 'all_fixed' should be accepted as 'all_issues_addressed': {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "all_issues_addressed");
    }

    #[test]
    fn test_tolerant_fix_status_synonym_remaining_maps_to_issues_remain() {
        let xml = r"<ralph-fix-result>
<ralph-status>remaining</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Synonym 'remaining' should be accepted as 'issues_remain': {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "issues_remain");
    }

    #[test]
    fn test_tolerant_fix_status_synonym_no_issues_maps_to_no_issues_found() {
        let xml = r"<ralph-fix-result>
<ralph-status>no_issues</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Synonym 'no_issues' should be accepted as 'no_issues_found': {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "no_issues_found");
    }

    #[test]
    fn test_tolerant_fix_status_case_insensitive_issues_remain() {
        let xml = r"<ralph-fix-result>
<ralph-status>ISSUES_REMAIN</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Case-insensitive 'ISSUES_REMAIN' should be accepted: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "issues_remain");
    }

    #[test]
    fn test_tolerant_fix_skips_self_closing_unknown_element() {
        // Self-closing unknown elements should be skipped (tests Event::Empty handler)
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-meta/>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Self-closing unknown element should be skipped, not rejected: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "all_issues_addressed");
    }

    #[test]
    fn test_tolerant_fix_element_reordering_summary_before_status() {
        // summary before status should still parse correctly
        let xml = r"<ralph-fix-result>
<ralph-summary>All issues have been addressed</ralph-summary>
<ralph-status>all_issues_addressed</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Element reordering (summary before status) should be tolerated: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "all_issues_addressed");
        assert_eq!(
            elements.summary,
            Some("All issues have been addressed".to_string())
        );
    }

    #[test]
    fn test_tolerant_fix_stray_text_between_elements() {
        // Stray text between elements should be tolerated
        let xml = "<ralph-fix-result>\nSome stray text\n<ralph-status>all_issues_addressed</ralph-status>\nMore stray text\n</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Stray text between elements should be tolerated: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "all_issues_addressed");
    }

    #[test]
    fn test_tolerant_fix_truly_ambiguous_status_rejected() {
        // Ambiguous/unknown status values should still be rejected
        let xml = r"<ralph-fix-result>
<ralph-status>partially_fixed</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_err(),
            "Ambiguous status 'partially_fixed' should still be rejected"
        );
        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error should reference ralph-status"
        );
    }

    #[test]
    fn test_tolerant_fix_empty_self_closing_status_rejected() {
        // Empty self-closing status should be rejected (no value)
        let xml = r"<ralph-fix-result>
<ralph-status/>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_err(),
            "Empty self-closing status element should be rejected"
        );
        let error = result.unwrap_err();
        assert!(
            error.element_path.contains("ralph-status"),
            "Error should reference ralph-status"
        );
    }

    #[test]
    fn test_validate_duplicate_status() {
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-status>issues_remain</ralph-status>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(result.is_err());
    }

    #[test]
    fn test_validate_whitespace_handling() {
        // This is the key test - quick_xml should handle whitespace between elements
        let xml = "  <ralph-fix-result>  \n  <ralph-status>all_issues_addressed</ralph-status>  \n  </ralph-fix-result>  ";

        let result = validate_fix_result_xml(xml);
        assert!(result.is_ok());
    }

    #[test]
    fn test_validate_with_xml_declaration() {
        let xml = r#"<?xml version="1.0"?>
<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
</ralph-fix-result>"#;

        let result = validate_fix_result_xml(xml);
        assert!(result.is_ok());
    }

    // =========================================================================
    // Fuzzy tag matching tests (Step 5 of implementation plan)
    // =========================================================================

    /// Test: misspelled ralph-summry tag resolves to ralph-summary.
    #[test]
    fn test_tolerant_fix_misspelled_summary_tag_accepted() {
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summry>Fixed all issues</ralph-summry>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Misspelled <ralph-summry> should be accepted: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(
            elements.summary,
            Some("Fixed all issues".to_string()),
            "Summary content should be correctly extracted from misspelled tag"
        );
    }

    /// Test: misspelled ralph-statuss tag resolves to ralph-status.
    #[test]
    fn test_tolerant_fix_misspelled_status_tag_accepted() {
        let xml = r"<ralph-fix-result>
<ralph-statuss>all_issues_addressed</ralph-statuss>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Misspelled <ralph-statuss> should be accepted: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(
            elements.status, "all_issues_addressed",
            "Status should be correctly extracted from misspelled tag"
        );
    }

    /// Test: completely unknown tag (large edit distance) is skipped.
    #[test]
    fn test_tolerant_fix_completely_unknown_tag_skipped() {
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-banana>this should be ignored</ralph-banana>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Unknown tag with large edit distance should be skipped: {result:?}"
        );
        let elements = result.unwrap();
        assert_eq!(elements.status, "all_issues_addressed");
    }

    /// Test: self-closing misspelled tag is also handled.
    #[test]
    fn test_tolerant_fix_self_closing_misspelled_tag() {
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summry/>
</ralph-fix-result>";

        let result = validate_fix_result_xml(xml);
        assert!(
            result.is_ok(),
            "Self-closing misspelled tag should be handled: {result:?}"
        );
        let elements = result.unwrap();
        // Self-closing misspelled tag should be treated as empty and skipped
        assert!(elements.summary.is_none());
    }

    #[test]
    fn test_normalize_fix_child_tag_unknown_returns_none() {
        assert_eq!(normalize_fix_child_tag(b"ralph-banana"), None);
    }
}
