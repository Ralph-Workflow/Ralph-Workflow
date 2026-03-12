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
const CONTINUATION_VALID_STATUSES: [&str; 2] = ["partial", "failed"];
const CONTINUATION_BLOCKER_INDICATORS: [&str; 9] = [
    "because",
    "blocked by",
    "prevented by",
    "due to",
    "still failing",
    "still blocked",
    "could not",
    "unable to",
    "stalled on",
];
const CONTINUATION_BOOKKEEPING_PREFIXES: [&str; 6] = [
    "files changed:",
    "tests run:",
    "work completed:",
    "activity summary:",
    "commands run:",
    "summary:",
];
const CONTINUATION_PLAN_SCOPE_TERMS: [&str; 3] = ["full plan", "entire plan", "remaining plan"];
const CONTINUATION_PLAN_COMPLETION_TERMS: [&str; 6] = [
    "finish",
    "complete",
    "verify",
    "verification",
    "done",
    "beyond the plan",
];
const CONTINUATION_VAGUE_STEP_PREFIXES: [&str; 6] = [
    "keep investigating",
    "try another fix",
    "continue later",
    "work on it more",
    "make more progress",
    "keep going",
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
                    let _ = skip_to_end(&mut reader, other);
                    return Err(unexpected_element_error(
                        other,
                        &VALID_TAGS,
                        "ralph-development-result",
                    ));
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
        files_changed_present,
        next_steps: next_steps.filter(|s| !s.is_empty()),
        next_steps_present,
    })
}

/// Validate continuation-mode development result XML.
///
/// Continuation outputs are stricter than normal development results: they must
/// explain why the full plan was not completed, provide an ordered recovery
/// checklist, and omit bookkeeping fields like `ralph-files-changed`.
///
/// # Errors
///
/// Returns error if the XML is invalid or violates the continuation contract.
pub fn validate_continuation_development_result_xml(
    xml_content: &str,
) -> Result<DevelopmentResultElements, XsdValidationError> {
    let elements = validate_development_result_xml(xml_content)?;

    if !CONTINUATION_VALID_STATUSES.contains(&elements.status.as_str()) {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-status".to_string(),
            expected: format!(
                "one of: {} for continuation output",
                CONTINUATION_VALID_STATUSES.join(", ")
            ),
            found: elements.status,
            suggestion:
                "Continuation output exists only when the full plan was not completed, so use <ralph-status>partial</ralph-status> or <ralph-status>failed</ralph-status>."
                    .to_string(),
            example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
        });
    }

    if elements.files_changed_present {
        return Err(XsdValidationError {
            error_type: XsdErrorType::UnexpectedElement,
            element_path: "ralph-files-changed".to_string(),
            expected:
                "continuation output must omit file bookkeeping and keep only recovery-critical information"
                    .to_string(),
            found: "ralph-files-changed element present".to_string(),
            suggestion:
                "Remove <ralph-files-changed> and keep the continuation focused on why the full plan was not completed plus the ordered recovery checklist."
                    .to_string(),
            example: Some(EXAMPLE_DEVELOPMENT_RESULT_XML.into()),
        });
    }

    let Some(next_steps) = elements.next_steps.as_ref() else {
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
    };

    if !summary_explains_blocker(&elements.summary) {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-summary".to_string(),
            expected: "a blocker-focused explanation of why the full plan was not completed"
                .to_string(),
            found: elements.summary.clone(),
            suggestion:
                "Rewrite <ralph-summary> to explain why the full plan was not completed, for example by naming the blocker with wording like `because`, `blocked by`, or `due to`."
                    .to_string(),
            example: Some(
                r"<ralph-summary>The full plan was not completed because verification still fails.</ralph-summary>"
                    .to_string()
                    .into(),
            ),
        });
    }

    if !has_ordered_recovery_steps(next_steps) {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-next-steps".to_string(),
            expected: "an ordered recovery checklist with at least two numbered steps"
                .to_string(),
            found: next_steps.clone(),
            suggestion:
                "Rewrite <ralph-next-steps> as an ordered checklist that describes how to finish the remaining plan in sequence."
                    .to_string(),
            example: Some(
                r"<ralph-next-steps>1. Fix the blocker.
2. Re-run the relevant tests.
3. Finish the remaining plan and run verification.</ralph-next-steps>"
                    .to_string()
                    .into(),
            ),
        });
    }

    if continuation_contains_bookkeeping(next_steps) {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-next-steps".to_string(),
            expected: "an ordered recovery checklist focused on finishing the remaining plan"
                .to_string(),
            found: next_steps.clone(),
            suggestion:
                "Rewrite <ralph-next-steps> as a recovery checklist for finishing the remaining plan. Remove bookkeeping such as file lists, tests run, work completed, or other activity summaries."
                    .to_string(),
            example: Some(
                r"<ralph-next-steps>1. Fix the blocker.
2. Re-run the relevant tests.
3. Finish the remaining plan and run verification.</ralph-next-steps>"
                    .to_string()
                    .into(),
            ),
        });
    }

    if continuation_contains_vague_steps(next_steps) {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-next-steps".to_string(),
            expected: "an actionable recovery checklist for finishing the remaining plan"
                .to_string(),
            found: next_steps.clone(),
            suggestion:
                "Rewrite <ralph-next-steps> so each numbered step is concrete recovery work for finishing the remaining plan. Replace vague lines such as `keep investigating`, `try another fix`, or `continue later` with specific actions."
                    .to_string(),
            example: Some(
                r"<ralph-next-steps>1. Implement the missing validation guard.
2. Re-run the focused continuation tests.
3. Finish the remaining plan and run repository verification.</ralph-next-steps>"
                    .to_string()
                    .into(),
            ),
        });
    }

    if !checklist_finishes_remaining_plan(next_steps) {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-next-steps".to_string(),
            expected: "an ordered recovery checklist whose final steps explicitly finish the remaining plan"
                .to_string(),
            found: next_steps.clone(),
            suggestion:
                "Rewrite <ralph-next-steps> so the checklist ends by finishing or verifying the remaining/full plan, not just by addressing the local blocker."
                    .to_string(),
            example: Some(
                r"<ralph-next-steps>1. Fix the blocker.
2. Re-run the relevant tests.
3. Finish the remaining plan and run repository verification.</ralph-next-steps>"
                    .to_string()
                    .into(),
            ),
        });
    }

    Ok(elements)
}

fn has_ordered_recovery_steps(next_steps: &str) -> bool {
    let mut step_count = 0;
    let mut expected = 1;

    for line in next_steps.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        let Some(rest) = trimmed.strip_prefix(&format!("{expected}.")) else {
            return false;
        };

        if rest.trim().is_empty() {
            return false;
        }

        step_count += 1;
        expected += 1;
    }

    step_count >= 2
}

fn checklist_finishes_remaining_plan(next_steps: &str) -> bool {
    let Some(last_step) = next_steps
        .lines()
        .map(str::trim)
        .rfind(|line| !line.is_empty())
    else {
        return false;
    };

    let normalized = trim_ordered_step_prefix(last_step).to_ascii_lowercase();
    CONTINUATION_PLAN_SCOPE_TERMS
        .iter()
        .any(|term| normalized.contains(term))
        && CONTINUATION_PLAN_COMPLETION_TERMS
            .iter()
            .any(|term| normalized.contains(term))
}

fn summary_explains_blocker(summary: &str) -> bool {
    let lowercase = summary.trim().to_ascii_lowercase();
    !lowercase.is_empty()
        && CONTINUATION_BLOCKER_INDICATORS
            .iter()
            .any(|indicator| lowercase.contains(indicator))
        && CONTINUATION_PLAN_SCOPE_TERMS
            .iter()
            .any(|term| lowercase.contains(term))
        && !continuation_contains_bookkeeping(summary)
}

fn continuation_contains_bookkeeping(text: &str) -> bool {
    text.lines().any(|line| {
        let trimmed = line.trim();
        let normalized = trim_ordered_step_prefix(trimmed).to_ascii_lowercase();
        CONTINUATION_BOOKKEEPING_PREFIXES
            .iter()
            .any(|prefix| normalized.starts_with(prefix))
    })
}

fn continuation_contains_vague_steps(text: &str) -> bool {
    text.lines().any(|line| {
        let trimmed = line.trim();
        let normalized = trim_ordered_step_prefix(trimmed).to_ascii_lowercase();
        CONTINUATION_VAGUE_STEP_PREFIXES
            .iter()
            .any(|prefix| normalized.starts_with(prefix))
    })
}

fn trim_ordered_step_prefix(line: &str) -> &str {
    let Some((prefix, rest)) = line.split_once('.') else {
        return line;
    };

    if prefix.chars().all(|c| c.is_ascii_digit()) {
        rest.trim_start()
    } else {
        line
    }
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
