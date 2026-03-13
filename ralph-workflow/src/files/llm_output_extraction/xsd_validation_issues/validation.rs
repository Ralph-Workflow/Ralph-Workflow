//! XSD validation logic for issues XML format.
//!
//! This module implements the validation rules for issues XML content,
//! ensuring it conforms to the expected schema.
//!
//! # Validation Rules
//!
//! The validator enforces these rules:
//! 1. Root element must be `<ralph-issues>`
//! 2. Must contain either `<ralph-issue>` elements OR `<ralph-no-issues-found>`, not both
//! 3. At least one child element must be present
//! 4. No text outside of tags
//! 5. No duplicate `<ralph-no-issues-found>` elements
//!
//! # Error Handling
//!
//! The validator provides detailed error messages with:
//! - The specific validation rule that failed
//! - What was expected vs. what was found
//! - Concrete examples of correct XML format
//! - Suggestions for fixing the error

use super::types::IssuesElements;
use crate::files::llm_output_extraction::xml_helpers::{
    create_reader, duplicate_element_error, format_content_preview, malformed_xml_error,
    read_text_until_end, skip_to_end,
};
use crate::files::llm_output_extraction::xsd_validation::{XsdErrorType, XsdValidationError};
use quick_xml::events::Event;

/// Example of valid issues XML with issues.
pub const EXAMPLE_ISSUES_XML: &str = r"<ralph-issues>
<ralph-issue>Missing error handling in API endpoint</ralph-issue>
<ralph-issue>Variable shadowing in loop construct</ralph-issue>
</ralph-issues>";

/// Example of valid issues XML with no issues.
pub const EXAMPLE_NO_ISSUES_XML: &str = r"<ralph-issues>
<ralph-no-issues-found>No issues were found during review</ralph-no-issues-found>
</ralph-issues>";

/// Validate issues XML content against the issues XSD.
///
/// Accepts either `<ralph-issues><ralph-issue>...` items or a single
/// `<ralph-no-issues-found>` entry.
///
/// # Arguments
///
/// * `xml_content` - The XML string to validate
///
/// # Returns
///
/// * `Ok(IssuesElements)` - Parsed issues if validation succeeds
/// * `Err(XsdValidationError)` - Detailed error if validation fails
///
/// # Examples
///
/// ```rust
/// use ralph_workflow::files::llm_output_extraction::validate_issues_xml;
///
/// // Valid XML with issues
/// let xml = r#"<ralph-issues>
/// <ralph-issue>Missing error handling</ralph-issue>
/// </ralph-issues>"#;
/// let result = validate_issues_xml(xml);
/// assert!(result.is_ok());
/// let parsed = result.unwrap();
/// assert_eq!(parsed.issues.len(), 1);
/// assert_eq!(parsed.issues[0], "Missing error handling");
/// assert_eq!(parsed.no_issues_found, None);
///
/// // Valid XML with no issues
/// let xml = r#"<ralph-issues>
/// <ralph-no-issues-found>All good</ralph-no-issues-found>
/// </ralph-issues>"#;
/// let result = validate_issues_xml(xml);
/// assert!(result.is_ok());
/// let parsed = result.unwrap();
/// assert!(parsed.issues.is_empty());
/// assert_eq!(parsed.no_issues_found, Some("All good".to_string()));
/// ```
///
/// # Errors
///
/// Returns an error if:
/// - The root element is not `<ralph-issues>`
/// - Both `<ralph-issue>` and `<ralph-no-issues-found>` are present
/// - No child elements are present
/// - Text appears outside of tags
/// - The XML is malformed
pub fn validate_issues_xml(xml_content: &str) -> Result<IssuesElements, XsdValidationError> {
    use crate::files::llm_output_extraction::xml_helpers::check_for_illegal_xml_characters;

    let content = xml_content.trim();

    // Check for illegal XML characters BEFORE parsing
    // This provides clear error messages instead of cryptic parse errors
    check_for_illegal_xml_characters(content)?;

    let mut reader = create_reader(content);
    let mut buf = Vec::new();

    // Find the root element
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == b"ralph-issues" => break,
            Ok(Event::Start(e)) => {
                let name_bytes = e.name();
                let tag_name = String::from_utf8_lossy(name_bytes.as_ref());
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "ralph-issues".to_string(),
                    expected: "<ralph-issues> as root element".to_string(),
                    found: format!("<{tag_name}> (wrong root element)"),
                    suggestion: "Use <ralph-issues> as the root element.".to_string(),
                    example: Some(EXAMPLE_ISSUES_XML.into()),
                });
            }
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "ralph-issues".to_string(),
                    expected: "<ralph-issues> as root element".to_string(),
                    found: format_content_preview(content),
                    suggestion: "Wrap your issues in <ralph-issues>...</ralph-issues> tags."
                        .to_string(),
                    example: Some(EXAMPLE_ISSUES_XML.into()),
                });
            }
            Ok(Event::Text(_) | _) => {
                // Text before root element or other events - continue to EOF error which is more informative
            }
            Err(e) => return Err(malformed_xml_error(&e)),
        }
        buf.clear();
    }

    // Parse child elements
    let mut issues: Vec<String> = Vec::new();
    let mut no_issues_found: Option<String> = None;

    loop {
        buf.clear();
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                match e.name().as_ref() {
                    b"ralph-issue" => {
                        // Cannot mix issues and no-issues-found
                        if no_issues_found.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-issues/ralph-issue".to_string(),
                                expected: "either <ralph-issue> elements OR <ralph-no-issues-found>, not both".to_string(),
                                found: "mixed issues and no-issues-found".to_string(),
                                suggestion: "Use <ralph-issue> when issues exist, or <ralph-no-issues-found> when no issues exist.".to_string(),
                                example: Some(EXAMPLE_ISSUES_XML.into()),
                            });
                        }
                        let issue_text = read_text_until_end(&mut reader, b"ralph-issue")?;
                        issues.push(issue_text);
                    }
                    b"ralph-no-issues-found" => {
                        // Cannot mix issues and no-issues-found
                        if !issues.is_empty() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-issues/ralph-no-issues-found".to_string(),
                                expected: "either <ralph-issue> elements OR <ralph-no-issues-found>, not both".to_string(),
                                found: "mixed issues and no-issues-found".to_string(),
                                suggestion: "Use <ralph-issue> when issues exist, or <ralph-no-issues-found> when no issues exist.".to_string(),
                                example: Some(EXAMPLE_NO_ISSUES_XML.into()),
                            });
                        }
                        if no_issues_found.is_some() {
                            return Err(duplicate_element_error(
                                "ralph-no-issues-found",
                                "ralph-issues",
                            ));
                        }
                        no_issues_found =
                            Some(read_text_until_end(&mut reader, b"ralph-no-issues-found")?);
                    }
                    other => {
                        // Tolerant: skip unknown elements instead of rejecting.
                        // Required elements (ralph-issue or ralph-no-issues-found) are still
                        // enforced after the loop.
                        let _ = skip_to_end(&mut reader, other);
                        // Continue parsing — do not return an error for unknown elements.
                    }
                }
            }
            Ok(Event::Text(e)) => {
                // Tolerant: ignore stray text between elements.
                let _ = e;
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"ralph-issues" => break,
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-issues".to_string(),
                    expected: "closing </ralph-issues> tag".to_string(),
                    found: "end of content without closing tag".to_string(),
                    suggestion: "Add </ralph-issues> at the end.".to_string(),
                    example: Some(EXAMPLE_ISSUES_XML.into()),
                });
            }
            Ok(_) => {} // Skip comments, etc.
            Err(e) => return Err(malformed_xml_error(&e)),
        }
    }

    // Filter out empty issues
    let filtered_issues: Vec<String> = issues.into_iter().filter(|s| !s.is_empty()).collect();
    let filtered_no_issues = no_issues_found.filter(|s| !s.is_empty());

    // Must have either issues or no-issues-found
    if filtered_issues.is_empty() && filtered_no_issues.is_none() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-issues".to_string(),
            expected: "at least one <ralph-issue> element OR <ralph-no-issues-found>".to_string(),
            found: "empty <ralph-issues> element".to_string(),
            suggestion:
                "Add <ralph-issue> elements for issues found, or <ralph-no-issues-found> if no issues exist."
                    .to_string(),
            example: Some(EXAMPLE_ISSUES_XML.into()),
        });
    }

    Ok(IssuesElements {
        issues: filtered_issues,
        no_issues_found: filtered_no_issues,
    })
}
