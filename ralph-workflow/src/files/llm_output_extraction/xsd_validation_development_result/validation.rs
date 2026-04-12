//! XML validation logic for development result format.

use super::types::DevelopmentResultElements;
use crate::files::llm_output_extraction::xml_helpers::{
    create_reader, duplicate_element_error, format_content_preview, malformed_xml_error,
    missing_required_error, parse_skills_mcp, read_text_until_end, read_text_until_end_fuzzy,
    skip_to_end,
    tolerant_parsing::{normalize_enum_value, normalize_tag_name, DEVELOPMENT_STATUS_SYNONYMS},
};
use crate::files::llm_output_extraction::xsd_validation::{XsdErrorType, XsdValidationError};
use crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp;
use quick_xml::events::Event;
use quick_xml::Reader;
use std::borrow::Cow;

/// Example of a valid development result XML for error messages.
const EXAMPLE_DEVELOPMENT_RESULT_XML: &str = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Implemented the feature with tests</ralph-summary>
</ralph-development-result>";

/// Valid status values for development results.
const VALID_STATUSES: [&str; 3] = ["completed", "partial", "failed"];

/// Known child element tags for development result validation.
/// Used for fuzzy tag name matching (typo tolerance).
const KNOWN_DEV_RESULT_TAGS: &[&str] = &[
    "ralph-status",
    "ralph-summary",
    "skills-mcp",
    "ralph-files-changed",
    "ralph-next-steps",
];

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

    let DevelopmentResultParseState {
        status,
        summary,
        skills_mcp_value,
        files_changed,
        next_steps,
    } = parse_development_result_with_reader(
        &mut create_reader(content.as_ref()),
        content.as_ref(),
    )?;

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

    let (files_changed, files_changed_present) =
        normalize_optional_text_and_presence(files_changed);
    let (next_steps, next_steps_present) = normalize_optional_text_and_presence(next_steps);

    Ok(DevelopmentResultElements {
        status,
        summary,
        skills_mcp: skills_mcp_value,
        files_changed,
        files_changed_present,
        next_steps,
        next_steps_present,
    })
}

#[derive(Default)]
struct DevelopmentResultParseState {
    status: Option<String>,
    summary: Option<String>,
    skills_mcp_value: Option<SkillsMcp>,
    files_changed: Option<String>,
    next_steps: Option<String>,
}

impl DevelopmentResultParseState {
    fn with_status(self, status: String) -> Result<Self, XsdValidationError> {
        if self.status.is_some() {
            Err(duplicate_element_error(
                "ralph-status",
                "ralph-development-result",
            ))
        } else {
            Ok(Self {
                status: Some(status),
                ..self
            })
        }
    }

    fn with_summary(self, summary: String) -> Result<Self, XsdValidationError> {
        if self.summary.is_some() {
            Err(duplicate_element_error(
                "ralph-summary",
                "ralph-development-result",
            ))
        } else {
            Ok(Self {
                summary: Some(summary),
                ..self
            })
        }
    }

    fn with_files_changed(self, files_changed: String) -> Result<Self, XsdValidationError> {
        if self.files_changed.is_some() {
            Err(duplicate_element_error(
                "ralph-files-changed",
                "ralph-development-result",
            ))
        } else {
            Ok(Self {
                files_changed: Some(files_changed),
                ..self
            })
        }
    }

    fn with_next_steps(self, next_steps: String) -> Result<Self, XsdValidationError> {
        if self.next_steps.is_some() {
            Err(duplicate_element_error(
                "ralph-next-steps",
                "ralph-development-result",
            ))
        } else {
            Ok(Self {
                next_steps: Some(next_steps),
                ..self
            })
        }
    }

    fn with_skills_mcp(self, skills_mcp_value: SkillsMcp) -> Self {
        Self {
            skills_mcp_value: Some(skills_mcp_value),
            ..self
        }
    }
}

fn parse_development_result_with_reader(
    reader: &mut Reader<&[u8]>,
    content: &str,
) -> Result<DevelopmentResultParseState, XsdValidationError> {
    find_development_root(reader, content)?;
    parse_development_result_children(reader, DevelopmentResultParseState::default())
}

fn find_development_root(
    reader: &mut Reader<&[u8]>,
    content: &str,
) -> Result<(), XsdValidationError> {
    find_development_root_next(reader, content, Vec::new())
}

fn find_development_root_next(
    reader: &mut Reader<&[u8]>,
    content: &str,
    mut buf: Vec<u8>,
) -> Result<(), XsdValidationError> {
    match reader.read_event_into(&mut buf) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"ralph-development-result" => Ok(()),
        Ok(Event::Start(e)) => {
            let name_bytes = e.name();
            let tag_name = String::from_utf8_lossy(name_bytes.as_ref());
            Err(XsdValidationError {
                error_type: XsdErrorType::MissingRequiredElement,
                element_path: "ralph-development-result".to_string(),
                expected: "<ralph-development-result> as root element".to_string(),
                found: format!("<{tag_name}> (wrong root element)"),
                suggestion: "Use <ralph-development-result> as the root element.".to_string(),
                example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
            })
        }
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-development-result".to_string(),
            expected: "<ralph-development-result> as root element".to_string(),
            found: format_content_preview(content),
            suggestion:
                "Wrap your result in <ralph-development-result>...</ralph-development-result> tags."
                    .to_string(),
            example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
        }),
        Ok(Event::Text(_) | _) => find_development_root(reader, content),
        Err(e) => Err(malformed_xml_error(&e)),
    }
}

fn parse_development_result_children(
    reader: &mut Reader<&[u8]>,
    state: DevelopmentResultParseState,
) -> Result<DevelopmentResultParseState, XsdValidationError> {
    parse_development_result_children_next(reader, state, Vec::new())
}

fn parse_development_result_children_next(
    reader: &mut Reader<&[u8]>,
    state: DevelopmentResultParseState,
    mut buf: Vec<u8>,
) -> Result<DevelopmentResultParseState, XsdValidationError> {
    match reader.read_event_into(&mut buf) {
        Ok(Event::Start(e)) => parse_development_result_start_tag(reader, state, e.name().as_ref())
            .and_then(|next_state| parse_development_result_children(reader, next_state)),
        Ok(Event::Empty(e)) => parse_development_result_empty_tag(state, e.name().as_ref())
            .and_then(|next_state| parse_development_result_children(reader, next_state)),
        Ok(Event::Text(_)) => parse_development_result_children(reader, state),
        Ok(Event::End(e)) if e.name().as_ref() == b"ralph-development-result" => Ok(state),
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "ralph-development-result".to_string(),
            expected: "closing </ralph-development-result> tag".to_string(),
            found: "end of content without closing tag".to_string(),
            suggestion: "Add </ralph-development-result> at the end.".to_string(),
            example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
        }),
        Ok(_) => parse_development_result_children(reader, state),
        Err(e) => Err(malformed_xml_error(&e)),
    }
}

fn parse_development_result_start_tag(
    reader: &mut Reader<&[u8]>,
    state: DevelopmentResultParseState,
    tag: &[u8],
) -> Result<DevelopmentResultParseState, XsdValidationError> {
    match tag {
        b"ralph-status" => state.with_status(read_text_until_end(reader, b"ralph-status")?),
        b"ralph-summary" => state.with_summary(read_text_until_end(reader, b"ralph-summary")?),
        b"skills-mcp" => Ok(state.with_skills_mcp(parse_skills_mcp(reader))),
        b"ralph-files-changed" => {
            state.with_files_changed(read_text_until_end(reader, b"ralph-files-changed")?)
        }
        b"ralph-next-steps" => {
            state.with_next_steps(read_text_until_end(reader, b"ralph-next-steps")?)
        }
        other => parse_development_result_fuzzy_start_tag(reader, state, other),
    }
}

fn parse_development_result_fuzzy_start_tag(
    reader: &mut Reader<&[u8]>,
    state: DevelopmentResultParseState,
    tag: &[u8],
) -> Result<DevelopmentResultParseState, XsdValidationError> {
    let tag_name = String::from_utf8_lossy(tag);

    match normalize_tag_name(&tag_name, KNOWN_DEV_RESULT_TAGS) {
        Some("ralph-status") => {
            state.with_status(read_text_until_end_fuzzy(reader, b"ralph-status", tag)?)
        }
        Some("ralph-summary") => {
            state.with_summary(read_text_until_end_fuzzy(reader, b"ralph-summary", tag)?)
        }
        Some("skills-mcp") => Ok(state.with_skills_mcp(parse_skills_mcp(reader))),
        Some("ralph-files-changed") => state.with_files_changed(read_text_until_end_fuzzy(
            reader,
            b"ralph-files-changed",
            tag,
        )?),
        Some("ralph-next-steps") => {
            state.with_next_steps(read_text_until_end_fuzzy(reader, b"ralph-next-steps", tag)?)
        }
        Some(_) | None => {
            let _ = skip_to_end(reader, tag);
            Ok(state)
        }
    }
}

fn parse_development_result_empty_tag(
    state: DevelopmentResultParseState,
    tag: &[u8],
) -> Result<DevelopmentResultParseState, XsdValidationError> {
    match tag {
        b"ralph-status" => state.with_status(String::new()),
        b"ralph-summary" => state.with_summary(String::new()),
        b"skills-mcp" => Ok(state.with_skills_mcp(SkillsMcp {
            skills: Vec::new(),
            mcps: Vec::new(),
            raw_content: None,
        })),
        b"ralph-files-changed" => state.with_files_changed(String::new()),
        b"ralph-next-steps" => state.with_next_steps(String::new()),
        other => parse_development_result_fuzzy_empty_tag(state, other),
    }
}

fn parse_development_result_fuzzy_empty_tag(
    state: DevelopmentResultParseState,
    tag: &[u8],
) -> Result<DevelopmentResultParseState, XsdValidationError> {
    let tag_name = String::from_utf8_lossy(tag);
    let _ = normalize_tag_name(&tag_name, KNOWN_DEV_RESULT_TAGS);
    Ok(state)
}

fn clear_files_changed(elements: DevelopmentResultElements) -> DevelopmentResultElements {
    DevelopmentResultElements {
        files_changed: None,
        files_changed_present: false,
        ..elements
    }
}

fn normalize_optional_text_and_presence(value: Option<String>) -> (Option<String>, bool) {
    let is_present = value.is_some();
    let normalized = value.filter(|s| !s.is_empty());
    (normalized, is_present)
}

/// Validate continuation-mode development result XML.
///
/// Continuation outputs may have status "completed", "partial", or "failed".
/// When status is "partial" or "failed", non-empty `ralph-next-steps` is required.
/// When status is "completed", `ralph-next-steps` is optional.
/// The `ralph-files-changed` element, if present, is silently discarded rather
/// than rejected — it is a harmless structural deviation. No content-quality
/// checks are enforced on the wording of the summary or next-steps.
///
/// # Errors
///
/// Returns error if the XML is invalid or violates the continuation contract.
pub fn validate_continuation_development_result_xml(
    xml_content: &str,
) -> Result<DevelopmentResultElements, XsdValidationError> {
    apply_continuation_development_result_contract(validate_development_result_xml(xml_content)?)
}

pub fn apply_continuation_development_result_contract(
    elements: DevelopmentResultElements,
) -> Result<DevelopmentResultElements, XsdValidationError> {
    let normalized = clear_files_changed(elements);

    if (normalized.status == "partial" || normalized.status == "failed")
        && normalized.next_steps.is_none()
    {
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

    Ok(normalized)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_optional_text_and_presence_none() {
        assert_eq!(normalize_optional_text_and_presence(None), (None, false));
    }

    #[test]
    fn normalize_optional_text_and_presence_empty_string() {
        assert_eq!(
            normalize_optional_text_and_presence(Some(String::new())),
            (None, true)
        );
    }

    #[test]
    fn normalize_optional_text_and_presence_non_empty_string() {
        assert_eq!(
            normalize_optional_text_and_presence(Some("src/lib.rs".to_string())),
            (Some("src/lib.rs".to_string()), true)
        );
    }

    #[test]
    fn clear_files_changed_resets_files_changed_fields_only() {
        let cleared = clear_files_changed(DevelopmentResultElements {
            status: "partial".to_string(),
            summary: "work in progress".to_string(),
            skills_mcp: None,
            files_changed: Some("src/lib.rs".to_string()),
            files_changed_present: true,
            next_steps: Some("finish tests".to_string()),
            next_steps_present: true,
        });

        assert_eq!(cleared.status, "partial");
        assert_eq!(cleared.summary, "work in progress");
        assert_eq!(cleared.skills_mcp, None);
        assert_eq!(cleared.files_changed, None);
        assert!(!cleared.files_changed_present);
        assert_eq!(cleared.next_steps, Some("finish tests".to_string()));
        assert!(cleared.next_steps_present);
    }
}
