//! XML validation logic for development result format.

use super::types::DevelopmentResultElements;
use crate::files::llm_output_extraction::xml_helpers::{
    create_reader, duplicate_element_error, format_content_preview, malformed_xml_error,
    missing_required_error, read_text_until_end, skip_to_end, text_outside_tags_error,
    unexpected_element_error,
};
use crate::files::llm_output_extraction::xsd_validation::{XsdErrorType, XsdValidationError};
use quick_xml::events::Event;
use std::borrow::Cow;

/// Example of a valid development result XML for error messages.
const EXAMPLE_DEVELOPMENT_RESULT_XML: &str = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Implemented the feature with tests</ralph-summary>
</ralph-development-result>";

/// Valid status values for development results.
const VALID_STATUSES: [&str; 3] = ["completed", "partial", "failed"];

/// Validate development result XML content against the XSD schema.
///
/// This function validates that the XML content conforms to the expected
/// development result format defined in `development_result.xsd`:
///
/// ```xml
/// <ralph-development-result>
///   <ralph-status>completed|partial|failed</ralph-status>
///   <ralph-summary>Brief summary of what was done</ralph-summary>
///   <ralph-files-changed>Optional list of files modified</ralph-files-changed>
///   <ralph-next-steps>Optional next steps</ralph-next-steps>
/// </ralph-development-result>
/// ```
///
/// # Arguments
///
/// * `xml_content` - The XML content to validate
///
/// # Returns
///
/// * `Ok(DevelopmentResultElements)` if the XML is valid and contains all required elements
/// * `Err(XsdValidationError)` if the XML is invalid or doesn't conform to the schema
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn validate_development_result_xml(
    xml_content: &str,
) -> Result<DevelopmentResultElements, XsdValidationError> {
    use crate::files::llm_output_extraction::xml_helpers::check_for_illegal_xml_characters;

    const VALID_TAGS: [&str; 4] = [
        "ralph-status",
        "ralph-summary",
        "ralph-files-changed",
        "ralph-next-steps",
    ];

    let trimmed = xml_content.trim();
    let content = unwrap_cdata_wrapper(trimmed);

    // Check for illegal XML characters BEFORE parsing
    check_for_illegal_xml_characters(content.as_ref())?;

    let mut reader = create_reader(content.as_ref());
    let mut buf = Vec::new();

    // Find the root element
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == b"ralph-development-result" => break,
            Ok(Event::Start(e)) => {
                let name_bytes = e.name();
                let tag_name = String::from_utf8_lossy(name_bytes.as_ref());
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "ralph-development-result".to_string(),
                    expected: "<ralph-development-result> as root element".to_string(),
                    found: format!("<{tag_name}> (wrong root element)"),
                    suggestion: "Use <ralph-development-result> as the root element.".to_string(),
                    example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
                });
            }
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "ralph-development-result".to_string(),
                    expected: "<ralph-development-result> as root element".to_string(),
                    found: format_content_preview(content.as_ref()),
                    suggestion:
                        "Wrap your result in <ralph-development-result>...</ralph-development-result> tags."
                            .to_string(),
                    example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
                });
            }
            Ok(Event::Text(_) | _) => {
                // Text before root element or other events - continue to find root or reach EOF
                // EOF will give a more informative "missing root element" error
            }
            Err(e) => return Err(malformed_xml_error(&e)),
        }
        buf.clear();
    }

    // Parse child elements
    let mut status: Option<String> = None;
    let mut summary: Option<String> = None;
    let mut files_changed: Option<String> = None;
    let mut next_steps: Option<String> = None;

    loop {
        buf.clear();
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => match e.name().as_ref() {
                b"ralph-status" => {
                    if status.is_some() {
                        return Err(duplicate_element_error(
                            "ralph-status",
                            "ralph-development-result",
                        ));
                    }
                    status = Some(read_text_until_end(&mut reader, b"ralph-status")?);
                }
                b"ralph-summary" => {
                    if summary.is_some() {
                        return Err(duplicate_element_error(
                            "ralph-summary",
                            "ralph-development-result",
                        ));
                    }
                    summary = Some(read_text_until_end(&mut reader, b"ralph-summary")?);
                }
                b"ralph-files-changed" => {
                    if files_changed.is_some() {
                        return Err(duplicate_element_error(
                            "ralph-files-changed",
                            "ralph-development-result",
                        ));
                    }
                    files_changed = Some(read_text_until_end(&mut reader, b"ralph-files-changed")?);
                }
                b"ralph-next-steps" => {
                    if next_steps.is_some() {
                        return Err(duplicate_element_error(
                            "ralph-next-steps",
                            "ralph-development-result",
                        ));
                    }
                    next_steps = Some(read_text_until_end(&mut reader, b"ralph-next-steps")?);
                }
                other => {
                    let _ = skip_to_end(&mut reader, other);
                    return Err(unexpected_element_error(
                        other,
                        &VALID_TAGS,
                        "ralph-development-result",
                    ));
                }
            },
            Ok(Event::Text(e)) => {
                let text = e.unescape().unwrap_or_default();
                let trimmed = text.trim();
                if !trimmed.is_empty() {
                    return Err(text_outside_tags_error(trimmed, "ralph-development-result"));
                }
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"ralph-development-result" => break,
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-development-result".to_string(),
                    expected: "closing </ralph-development-result> tag".to_string(),
                    found: "end of content without closing tag".to_string(),
                    suggestion: "Add </ralph-development-result> at the end.".to_string(),
                    example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
                });
            }
            Ok(_) => {} // Skip comments, etc.
            Err(e) => return Err(malformed_xml_error(&e)),
        }
    }

    // Validate required element: status
    let status = status.ok_or_else(|| {
        missing_required_error(
            "ralph-status",
            "ralph-development-result",
            Some(EXAMPLE_DEVELOPMENT_RESULT_XML),
        )
    })?;

    // Validate required element: summary
    let summary = summary.ok_or_else(|| {
        missing_required_error(
            "ralph-summary",
            "ralph-development-result",
            Some(EXAMPLE_DEVELOPMENT_RESULT_XML),
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
            example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
        });
    }

    // Validate status is one of the allowed values
    if !VALID_STATUSES.contains(&status.as_str()) {
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
            example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
        });
    }

    // Validate summary content
    if summary.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-summary".to_string(),
            expected: "non-empty summary description".to_string(),
            found: "empty summary".to_string(),
            suggestion: "Add a description of what was done inside <ralph-summary>.".to_string(),
            example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
        });
    }

    Ok(DevelopmentResultElements {
        status,
        summary,
        files_changed: files_changed.filter(|s| !s.is_empty()),
        next_steps: next_steps.filter(|s| !s.is_empty()),
    })
}

fn unwrap_cdata_wrapper(content: &str) -> Cow<'_, str> {
    let trimmed = content.trim();
    let Some(stripped) = trimmed.strip_prefix("<![CDATA[") else {
        return Cow::Borrowed(trimmed);
    };
    let Some(inner) = stripped.strip_suffix("]]>") else {
        return Cow::Borrowed(trimmed);
    };
    Cow::Borrowed(inner.trim())
}
