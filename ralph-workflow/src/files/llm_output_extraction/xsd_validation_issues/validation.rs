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

use super::types::{IssueEntry, IssuesElements};
use crate::files::llm_output_extraction::xml_helpers::{
    create_reader, duplicate_element_error, format_content_preview, malformed_xml_error,
    parse_skills_mcp, read_text_until_end, read_text_until_end_fuzzy, skip_to_end,
    tolerant_parsing::normalize_tag_name,
};
use crate::files::llm_output_extraction::xsd_validation::{XsdErrorType, XsdValidationError};
use quick_xml::events::Event;

/// Example of valid issues XML with issues.
pub(crate) const EXAMPLE_ISSUES_XML: &str = r"<ralph-issues>
<ralph-issue>Missing error handling in API endpoint</ralph-issue>
<ralph-issue>Variable shadowing in loop construct</ralph-issue>
</ralph-issues>";

/// Example of valid issues XML with no issues.
pub(crate) const EXAMPLE_NO_ISSUES_XML: &str = r"<ralph-issues>
<ralph-no-issues-found>No issues were found during review</ralph-no-issues-found>
</ralph-issues>";

/// Known child element tags for issues validation.
/// Used for fuzzy tag name matching (typo tolerance).
const KNOWN_ISSUES_TAGS: &[&str] = &["ralph-issue", "ralph-no-issues-found"];

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
/// assert_eq!(parsed.issues[0].text, "Missing error handling");
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

    let parsed = parse_issues_with_reader(&mut create_reader(content), content)?;

    // Filter out empty issues
    let filtered_issues: Vec<IssueEntry> = parsed
        .issues
        .into_iter()
        .filter(|entry| !entry.text.is_empty())
        .collect();
    let filtered_no_issues = parsed.no_issues_found.filter(|value| !value.is_empty());

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

#[derive(Default)]
struct ParsedIssues {
    issues: Vec<IssueEntry>,
    no_issues_found: Option<String>,
}

fn parse_issues_with_reader(
    reader: &mut quick_xml::Reader<&[u8]>,
    content: &str,
) -> Result<ParsedIssues, XsdValidationError> {
    find_issues_root(reader, content)?;
    parse_issues_children(reader, ParsedIssues::default())
}

fn find_issues_root(
    reader: &mut quick_xml::Reader<&[u8]>,
    content: &str,
) -> Result<(), XsdValidationError> {
    match read_owned_event(reader) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"ralph-issues" => Ok(()),
        Ok(Event::Start(e)) => {
            let name_bytes = e.name();
            let tag_name = String::from_utf8_lossy(name_bytes.as_ref());
            Err(XsdValidationError {
                error_type: XsdErrorType::MissingRequiredElement,
                element_path: "ralph-issues".to_string(),
                expected: "<ralph-issues> as root element".to_string(),
                found: format!("<{tag_name}> (wrong root element)"),
                suggestion: "Use <ralph-issues> as the root element.".to_string(),
                example: Some(EXAMPLE_ISSUES_XML.into()),
            })
        }
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-issues".to_string(),
            expected: "<ralph-issues> as root element".to_string(),
            found: format_content_preview(content),
            suggestion: "Wrap your issues in <ralph-issues>...</ralph-issues> tags.".to_string(),
            example: Some(EXAMPLE_ISSUES_XML.into()),
        }),
        Ok(Event::Text(_) | _) => find_issues_root(reader, content),
        Err(err) => Err(malformed_xml_error(&err)),
    }
}

fn parse_issues_children(
    reader: &mut quick_xml::Reader<&[u8]>,
    parsed: ParsedIssues,
) -> Result<ParsedIssues, XsdValidationError> {
    match read_owned_event(reader) {
        Ok(Event::Start(e)) => {
            let next = process_issue_child_start(reader, parsed, e.name().as_ref())?;
            parse_issues_children(reader, next)
        }
        Ok(Event::Text(_)) => parse_issues_children(reader, parsed),
        Ok(Event::End(e)) if e.name().as_ref() == b"ralph-issues" => Ok(parsed),
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "ralph-issues".to_string(),
            expected: "closing </ralph-issues> tag".to_string(),
            found: "end of content without closing tag".to_string(),
            suggestion: "Add </ralph-issues> at the end.".to_string(),
            example: Some(EXAMPLE_ISSUES_XML.into()),
        }),
        Ok(_) => parse_issues_children(reader, parsed),
        Err(err) => Err(malformed_xml_error(&err)),
    }
}

fn process_issue_child_start(
    reader: &mut quick_xml::Reader<&[u8]>,
    parsed: ParsedIssues,
    tag_name: &[u8],
) -> Result<ParsedIssues, XsdValidationError> {
    match tag_name {
        b"ralph-issue" => parse_canonical_issue(reader, parsed),
        b"ralph-no-issues-found" => parse_canonical_no_issues(reader, parsed),
        other => process_fuzzy_or_unknown_child(reader, parsed, other),
    }
}

fn process_fuzzy_or_unknown_child(
    reader: &mut quick_xml::Reader<&[u8]>,
    parsed: ParsedIssues,
    other: &[u8],
) -> Result<ParsedIssues, XsdValidationError> {
    let tag_name = String::from_utf8_lossy(other);
    match normalize_tag_name(&tag_name, KNOWN_ISSUES_TAGS) {
        Some("ralph-issue") => parse_fuzzy_issue(reader, parsed, other),
        Some("ralph-no-issues-found") => parse_fuzzy_no_issues(reader, parsed, other),
        Some(_) => {
            let _ = skip_to_end(reader, other);
            Ok(parsed)
        }
        None => {
            let _ = skip_to_end(reader, other);
            Ok(parsed)
        }
    }
}

fn parse_canonical_issue(
    reader: &mut quick_xml::Reader<&[u8]>,
    parsed: ParsedIssues,
) -> Result<ParsedIssues, XsdValidationError> {
    if parsed.no_issues_found.is_some() {
        return Err(mixed_issue_no_issue_error(
            "ralph-issues/ralph-issue",
            EXAMPLE_ISSUES_XML,
        ));
    }

    let entry = parse_issue_entry(reader, b"ralph-issue")?;
    Ok(ParsedIssues {
        issues: append_item(parsed.issues, entry),
        no_issues_found: parsed.no_issues_found,
    })
}

fn parse_fuzzy_issue(
    reader: &mut quick_xml::Reader<&[u8]>,
    parsed: ParsedIssues,
    original_tag: &[u8],
) -> Result<ParsedIssues, XsdValidationError> {
    if parsed.no_issues_found.is_some() {
        return Err(mixed_issue_no_issue_error(
            "ralph-issues/ralph-issue",
            EXAMPLE_ISSUES_XML,
        ));
    }

    let entry = parse_issue_entry(reader, original_tag)?;
    Ok(ParsedIssues {
        issues: append_item(parsed.issues, entry),
        no_issues_found: parsed.no_issues_found,
    })
}

fn parse_canonical_no_issues(
    reader: &mut quick_xml::Reader<&[u8]>,
    parsed: ParsedIssues,
) -> Result<ParsedIssues, XsdValidationError> {
    if !parsed.issues.is_empty() {
        return Err(mixed_issue_no_issue_error(
            "ralph-issues/ralph-no-issues-found",
            EXAMPLE_NO_ISSUES_XML,
        ));
    }
    if parsed.no_issues_found.is_some() {
        return Err(duplicate_element_error(
            "ralph-no-issues-found",
            "ralph-issues",
        ));
    }

    Ok(ParsedIssues {
        issues: parsed.issues,
        no_issues_found: Some(read_text_until_end(reader, b"ralph-no-issues-found")?),
    })
}

fn parse_fuzzy_no_issues(
    reader: &mut quick_xml::Reader<&[u8]>,
    parsed: ParsedIssues,
    original_tag: &[u8],
) -> Result<ParsedIssues, XsdValidationError> {
    if !parsed.issues.is_empty() {
        return Err(mixed_issue_no_issue_error(
            "ralph-issues/ralph-no-issues-found",
            EXAMPLE_NO_ISSUES_XML,
        ));
    }
    if parsed.no_issues_found.is_some() {
        return Err(duplicate_element_error(
            "ralph-no-issues-found",
            "ralph-issues",
        ));
    }

    Ok(ParsedIssues {
        issues: parsed.issues,
        no_issues_found: Some(read_text_until_end_fuzzy(
            reader,
            b"ralph-no-issues-found",
            original_tag,
        )?),
    })
}

fn mixed_issue_no_issue_error(element_path: &str, example: &str) -> XsdValidationError {
    XsdValidationError {
        error_type: XsdErrorType::UnexpectedElement,
        element_path: element_path.to_string(),
        expected: "either <ralph-issue> elements OR <ralph-no-issues-found>, not both".to_string(),
        found: "mixed issues and no-issues-found".to_string(),
        suggestion:
            "Use <ralph-issue> when issues exist, or <ralph-no-issues-found> when no issues exist."
                .to_string(),
        example: Some(example.into()),
    }
}

fn append_item<T>(items: Vec<T>, item: T) -> Vec<T> {
    items.into_iter().chain(std::iter::once(item)).collect()
}

fn read_owned_event(
    reader: &mut quick_xml::Reader<&[u8]>,
) -> Result<Event<'static>, quick_xml::Error> {
    reader
        .read_event_into(&mut Vec::new())
        .map(quick_xml::events::Event::into_owned)
}

/// Parse the content of a `<ralph-issue>` element into an `IssueEntry`.
///
/// This handles mixed content: text content and optional `<code>` and `<skills-mcp>` child elements.
/// Text content (including text from `<code>` children) is collected into `text`.
/// A `<skills-mcp>` child is parsed into `skills_mcp`.
/// Other unknown child elements are skipped tolerantly.
///
/// The `original_tag` parameter is used for fuzzy matching - when the opening tag was misspelled,
/// this allows the parser to accept either the canonical closing tag OR the original misspelled one.
fn parse_issue_entry(
    reader: &mut quick_xml::Reader<&[u8]>,
    original_tag: &[u8],
) -> Result<IssueEntry, XsdValidationError> {
    let canonical_tag = b"ralph-issue";
    parse_issue_entry_parts(reader, canonical_tag, original_tag, Vec::new(), None)
}

fn parse_issue_entry_parts(
    reader: &mut quick_xml::Reader<&[u8]>,
    canonical_tag: &[u8],
    original_tag: &[u8],
    text_parts: Vec<String>,
    skills_mcp: Option<crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp>,
) -> Result<IssueEntry, XsdValidationError> {
    match read_owned_event(reader) {
        Ok(Event::Text(e)) => {
            let text = e.unescape().unwrap_or_default().to_string();
            let next_parts = append_non_empty_text(text_parts, text);
            parse_issue_entry_parts(reader, canonical_tag, original_tag, next_parts, skills_mcp)
        }
        Ok(Event::CData(e)) => {
            let text = String::from_utf8_lossy(&e).to_string();
            let next_parts = append_non_empty_text(text_parts, text);
            parse_issue_entry_parts(reader, canonical_tag, original_tag, next_parts, skills_mcp)
        }
        Ok(Event::Start(e)) => parse_issue_entry_started_child(
            reader,
            canonical_tag,
            original_tag,
            text_parts,
            skills_mcp,
            e,
        ),
        Ok(Event::Empty(e)) => parse_issue_entry_empty_child(
            reader,
            canonical_tag,
            original_tag,
            text_parts,
            skills_mcp,
            e,
        ),
        Ok(Event::End(e)) => {
            if e.name().as_ref() == canonical_tag || e.name().as_ref() == original_tag {
                let text = text_parts.join("").trim().to_string();
                Ok(IssueEntry { text, skills_mcp })
            } else {
                parse_issue_entry_parts(reader, canonical_tag, original_tag, text_parts, skills_mcp)
            }
        }
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type:
                crate::files::llm_output_extraction::xsd_validation::XsdErrorType::MalformedXml,
            element_path: "ralph-issue".to_string(),
            expected: format!(
                "closing </{}> or </{}>",
                String::from_utf8_lossy(canonical_tag),
                String::from_utf8_lossy(original_tag)
            ),
            found: "unexpected end of file".to_string(),
            suggestion: format!(
                "Ensure the <ralph-issue> element has a matching closing tag (</{}> or </{}>).",
                String::from_utf8_lossy(canonical_tag),
                String::from_utf8_lossy(original_tag)
            ),
            example: None,
        }),
        Ok(_) => {
            parse_issue_entry_parts(reader, canonical_tag, original_tag, text_parts, skills_mcp)
        }
        Err(err) => Err(malformed_xml_error(&err)),
    }
}

fn parse_issue_entry_started_child(
    reader: &mut quick_xml::Reader<&[u8]>,
    canonical_tag: &[u8],
    original_tag: &[u8],
    text_parts: Vec<String>,
    skills_mcp: Option<crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp>,
    event: quick_xml::events::BytesStart<'_>,
) -> Result<IssueEntry, XsdValidationError> {
    match event.name().as_ref() {
        b"code" => {
            let code_text = read_text_until_end(reader, b"code")?;
            let next_parts = append_non_empty_text(text_parts, code_text);
            parse_issue_entry_parts(reader, canonical_tag, original_tag, next_parts, skills_mcp)
        }
        b"skills-mcp" => {
            let parsed_skills_mcp = parse_skills_mcp(reader);
            parse_issue_entry_parts(
                reader,
                canonical_tag,
                original_tag,
                text_parts,
                Some(parsed_skills_mcp),
            )
        }
        other => {
            let _ = skip_to_end(reader, other);
            parse_issue_entry_parts(reader, canonical_tag, original_tag, text_parts, skills_mcp)
        }
    }
}

fn parse_issue_entry_empty_child(
    reader: &mut quick_xml::Reader<&[u8]>,
    canonical_tag: &[u8],
    original_tag: &[u8],
    text_parts: Vec<String>,
    skills_mcp: Option<crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp>,
    event: quick_xml::events::BytesStart<'_>,
) -> Result<IssueEntry, XsdValidationError> {
    if event.name().as_ref() == b"skills-mcp" {
        use crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp;
        return parse_issue_entry_parts(
            reader,
            canonical_tag,
            original_tag,
            text_parts,
            Some(SkillsMcp {
                skills: Vec::new(),
                mcps: Vec::new(),
                raw_content: None,
            }),
        );
    }

    parse_issue_entry_parts(reader, canonical_tag, original_tag, text_parts, skills_mcp)
}

fn append_non_empty_text(text_parts: Vec<String>, text: String) -> Vec<String> {
    if text.trim().is_empty() {
        text_parts
    } else {
        append_item(text_parts, text)
    }
}
