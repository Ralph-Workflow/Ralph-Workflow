//! XML validation logic for development result format.

use super::types::DevelopmentResultElements;
use crate::files::llm_output_extraction::xml_helpers::{
    create_reader, duplicate_element_error, format_content_preview, malformed_xml_error,
    missing_required_error, read_text_until_end, skip_to_end,
    tolerant_parsing::{normalize_enum_value, DEVELOPMENT_STATUS_SYNONYMS},
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
    let mut skills_mcp_value: Option<
        crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp,
    > = None;
    let mut files_changed: Option<String> = None;
    let mut files_changed_present = false;
    let mut next_steps: Option<String> = None;
    let mut next_steps_present = false;

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
                b"skills-mcp" => {
                    use crate::files::llm_output_extraction::xml_helpers::parse_skills_mcp;
                    skills_mcp_value = Some(parse_skills_mcp(&mut reader));
                }
                b"ralph-files-changed" => {
                    if files_changed_present {
                        return Err(duplicate_element_error(
                            "ralph-files-changed",
                            "ralph-development-result",
                        ));
                    }
                    files_changed_present = true;
                    files_changed = Some(read_text_until_end(&mut reader, b"ralph-files-changed")?);
                }
                b"ralph-next-steps" => {
                    if next_steps_present {
                        return Err(duplicate_element_error(
                            "ralph-next-steps",
                            "ralph-development-result",
                        ));
                    }
                    next_steps_present = true;
                    next_steps = Some(read_text_until_end(&mut reader, b"ralph-next-steps")?);
                }
                other => {
                    // Tolerant: skip unknown elements instead of rejecting.
                    // Required elements (status, summary) are still enforced after the loop.
                    let _ = skip_to_end(&mut reader, other);
                    // Continue parsing — do not return an error for unknown elements.
                }
            },
            Ok(Event::Empty(e)) => match e.name().as_ref() {
                b"ralph-status" => {
                    if status.is_some() {
                        return Err(duplicate_element_error(
                            "ralph-status",
                            "ralph-development-result",
                        ));
                    }
                    status = Some(String::new());
                }
                b"ralph-summary" => {
                    if summary.is_some() {
                        return Err(duplicate_element_error(
                            "ralph-summary",
                            "ralph-development-result",
                        ));
                    }
                    summary = Some(String::new());
                }
                b"skills-mcp" => {
                    use crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp;
                    skills_mcp_value = Some(SkillsMcp {
                        skills: Vec::new(),
                        mcps: Vec::new(),
                        raw_content: None,
                    });
                }
                b"ralph-files-changed" => {
                    if files_changed_present {
                        return Err(duplicate_element_error(
                            "ralph-files-changed",
                            "ralph-development-result",
                        ));
                    }
                    files_changed_present = true;
                    files_changed = Some(String::new());
                }
                b"ralph-next-steps" => {
                    if next_steps_present {
                        return Err(duplicate_element_error(
                            "ralph-next-steps",
                            "ralph-development-result",
                        ));
                    }
                    next_steps_present = true;
                    next_steps = Some(String::new());
                }
                other => {
                    // Tolerant: skip unknown empty elements instead of rejecting.
                    let _ = other;
                    // Continue parsing — do not return an error for unknown elements.
                }
            },
            Ok(Event::Text(e)) => {
                // Tolerant: ignore stray text between elements.
                // By the time content reaches the validator it has been extracted with matching
                // open/close tags. Stray text between child elements within a valid root is
                // genuinely harmless — it does not change the meaning of the result.
                let _ = e;
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

    // Tolerant: normalize status via synonym table (case-insensitive, synonym mapping).
    // Returns the canonical form if the value is recognized, or None if truly ambiguous.
    let Some(status) = normalize_enum_value(&status, &VALID_STATUSES, DEVELOPMENT_STATUS_SYNONYMS)
    else {
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
    };

    // Tolerant: empty summary is informational — the status outcome is what matters.
    // When summary is empty, use a fallback placeholder rather than rejecting.
    // This preserves the status outcome even when the LLM omits the summary text.
    let summary = if summary.is_empty() {
        "(no summary provided)".to_string()
    } else {
        summary
    };

    Ok(DevelopmentResultElements {
        status,
        summary,
        skills_mcp: skills_mcp_value,
        files_changed: files_changed.filter(|s| !s.is_empty()),
        files_changed_present,
        next_steps: next_steps.filter(|s| !s.is_empty()),
        next_steps_present,
    })
}

/// Validate continuation-mode development result XML.
///
/// Continuation outputs must have status "partial" or "failed" and must include
/// non-empty `ralph-next-steps`. The `ralph-files-changed` element, if present,
/// is silently discarded rather than rejected — it is a harmless structural
/// deviation. No content-quality checks are enforced on the wording of the
/// summary or next-steps.
///
/// # Errors
///
/// Returns error if the XML is invalid or violates the continuation contract.
pub fn validate_continuation_development_result_xml(
    xml_content: &str,
) -> Result<DevelopmentResultElements, XsdValidationError> {
    let elements = validate_development_result_xml(xml_content)?;

    if elements.status != "partial" && elements.status != "failed" {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-status".to_string(),
            expected: "one of: partial, failed for continuation output".to_string(),
            found: elements.status,
            suggestion:
                "Continuation output exists only when the full plan was not completed, so use <ralph-status>partial</ralph-status> or <ralph-status>failed</ralph-status>."
                    .to_string(),
            example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
        });
    }

    // Tolerate ralph-files-changed if present — clear it so downstream ignores it.
    let mut elements = elements;
    elements.files_changed = None;
    elements.files_changed_present = false;

    if elements.next_steps.is_none() {
        return Err(missing_required_error(
            "ralph-next-steps",
            "ralph-development-result",
            Some(
                r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>The full plan was not completed because verification still fails.</ralph-summary>
<ralph-next-steps>1. Fix the failing verification.
2. Re-run the focused continuation tests.
3. Finish the remaining plan and verify the repository.</ralph-next-steps>
</ralph-development-result>",
            ),
        ));
    }

    Ok(elements)
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
