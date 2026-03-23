// File list parsing functions for XSD validation.
//
// These functions parse the <ralph-files> and <ralph-excluded-files> sections
// from commit message XML.

use quick_xml::events::Event;
use quick_xml::Reader;

use crate::files::llm_output_extraction::xml_helpers::{
    format_content_preview, malformed_xml_error, read_text_until_end, skip_to_end,
};
use crate::files::llm_output_extraction::xsd_validation::{XsdErrorType, XsdValidationError};
use crate::reducer::state::pipeline::ExcludedFile;

/// Example of a valid commit message XML for error messages.
const EXAMPLE_COMMIT_XML: &str = r"<ralph-commit>
<ralph-subject>feat(api): add user authentication</ralph-subject>
<ralph-body>Implements JWT-based authentication for the API.</ralph-body>
</ralph-commit>";

fn unescape_text(
    text: &quick_xml::events::BytesText<'_>,
    element_path: &str,
) -> Result<String, XsdValidationError> {
    text.unescape()
        .map(|t| t.to_string())
        .map_err(|e| XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: element_path.to_string(),
            expected: "valid XML text content".to_string(),
            found: format!("unescape error: {e}"),
            suggestion:
                "Ensure text content uses valid XML escaping (e.g., &amp; for '&', &lt; for '<')."
                    .to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        })
}

/// Parse the `<ralph-files>` section and return the list of file paths.
pub(super) fn parse_files_section(
    reader: &mut Reader<&[u8]>,
) -> Result<Vec<String>, XsdValidationError> {
    let file_list = parse_files_events(reader, Vec::new())?;
    if file_list.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-commit/ralph-files".to_string(),
            expected: "at least one ralph-file child element".to_string(),
            found: "ralph-files with no ralph-file children".to_string(),
            suggestion: "Either add ralph-file elements or omit ralph-files entirely.".to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        });
    }
    Ok(file_list)
}

fn parse_files_events(
    reader: &mut Reader<&[u8]>,
    file_list: Vec<String>,
) -> Result<Vec<String>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(ref e)) if e.name().as_ref() == b"ralph-file" => {
            let text = read_text_until_end(reader, b"ralph-file")?;
            let trimmed = text.trim().to_string();
            if !trimmed.is_empty() {
                parse_files_events(
                    reader,
                    file_list.into_iter().chain(std::iter::once(trimmed)).collect(),
                )
            } else {
                parse_files_events(reader, file_list)
            }
        }
        Ok(Event::Empty(ref e)) if e.name().as_ref() == b"ralph-file" => {
            Err(XsdValidationError {
                error_type: XsdErrorType::InvalidContent,
                element_path: "ralph-commit/ralph-files/ralph-file".to_string(),
                expected: "non-empty file path".to_string(),
                found: "<ralph-file/> (empty file path)".to_string(),
                suggestion:
                    "Provide a repo-relative path inside <ralph-file>path</ralph-file>."
                        .to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::Start(ref e)) => {
            let other = e.name().as_ref().to_vec();
            let other_name = String::from_utf8_lossy(&other).to_string();
            let _ = skip_to_end(reader, &other);
            Err(XsdValidationError {
                error_type: XsdErrorType::UnexpectedElement,
                element_path: format!("ralph-commit/ralph-files/{other_name}"),
                expected: "only <ralph-file> child elements".to_string(),
                found: format!("<{other_name}>"),
                suggestion: "Inside <ralph-files>, include only one or more <ralph-file>path</ralph-file> elements. Remove any other child elements."
                    .to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::Empty(ref e)) => {
            let other = e.name().as_ref().to_vec();
            let other_name = String::from_utf8_lossy(&other).to_string();
            Err(XsdValidationError {
                error_type: XsdErrorType::UnexpectedElement,
                element_path: format!("ralph-commit/ralph-files/{other_name}"),
                expected: "only <ralph-file> child elements".to_string(),
                found: format!("<{other_name}/>"),
                suggestion: "Inside <ralph-files>, include only one or more <ralph-file>path</ralph-file> elements. Remove any other child elements."
                    .to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::End(ref e)) if e.name().as_ref() == b"ralph-files" => Ok(file_list),
        Ok(Event::Text(ref t)) => {
            let text = unescape_text(t, "ralph-commit/ralph-files")?;
            if !text.trim().is_empty() {
                Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: "ralph-commit/ralph-files".to_string(),
                    expected: "whitespace-only text between <ralph-file> elements".to_string(),
                    found: format!("text content: {}", format_content_preview(text.trim())),
                    suggestion: "Remove any non-whitespace text inside <ralph-files>; only <ralph-file> elements are allowed."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                })
            } else {
                parse_files_events(reader, file_list)
            }
        }
        Ok(Event::CData(ref c)) => {
            let text = String::from_utf8_lossy(c.as_ref()).to_string();
            if !text.trim().is_empty() {
                Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: "ralph-commit/ralph-files".to_string(),
                    expected: "whitespace-only CDATA between <ralph-file> elements".to_string(),
                    found: format!("CDATA content: {}", format_content_preview(text.trim())),
                    suggestion: "Remove any non-whitespace CDATA inside <ralph-files>; only <ralph-file> elements are allowed."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                })
            } else {
                parse_files_events(reader, file_list)
            }
        }
        Ok(Event::Eof) => {
            Err(XsdValidationError {
                error_type: XsdErrorType::MalformedXml,
                element_path: "ralph-commit/ralph-files".to_string(),
                expected: "closing </ralph-files> tag".to_string(),
                found: "end of content without closing tag".to_string(),
                suggestion: "Add </ralph-files> to close the file list.".to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::End(ref e)) => {
            let other_end = e.name().as_ref().to_vec();
            let other_end_name = String::from_utf8_lossy(&other_end).to_string();
            Err(XsdValidationError {
                error_type: XsdErrorType::MalformedXml,
                element_path: "ralph-commit/ralph-files".to_string(),
                expected: "</ralph-files> closing tag".to_string(),
                found: format!("unexpected closing tag </{other_end_name}>"),
                suggestion: "Ensure <ralph-files> contains only <ralph-file> elements and is properly closed with </ralph-files>."
                    .to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(_) => parse_files_events(reader, file_list),
        Err(e) => Err(malformed_xml_error(&e)),
    }
}

/// Parse the `<ralph-excluded-files>` section and return the list of excluded files.
pub(super) fn parse_excluded_files_section(
    reader: &mut Reader<&[u8]>,
) -> Result<Vec<ExcludedFile>, XsdValidationError> {
    let entry_list = parse_excluded_files_events(reader, Vec::new())?;
    if entry_list.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-commit/ralph-excluded-files".to_string(),
            expected: "at least one ralph-excluded-file child element".to_string(),
            found: "ralph-excluded-files with no ralph-excluded-file children".to_string(),
            suggestion:
                "Either add ralph-excluded-file elements or omit ralph-excluded-files entirely."
                    .to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        });
    }
    Ok(entry_list)
}

fn parse_excluded_files_events(
    reader: &mut Reader<&[u8]>,
    entry_list: Vec<ExcludedFile>,
) -> Result<Vec<ExcludedFile>, XsdValidationError> {
    use crate::reducer::state::pipeline::{ExcludedFile, ExcludedFileReason};

    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(ref e)) if e.name().as_ref() == b"ralph-excluded-file" => {
            let reason_attr = e.attributes().find_map(|a| {
                a.ok().and_then(|attr| {
                    if attr.key.as_ref() == b"reason" {
                        attr.unescape_value().ok().map(|v| v.to_string())
                    } else {
                        None
                    }
                })
            });
            let Some(reason_str) = reason_attr else {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: "ralph-commit/ralph-excluded-files/ralph-excluded-file"
                        .to_string(),
                    expected: "required 'reason' attribute".to_string(),
                    found: "missing reason attribute".to_string(),
                    suggestion: "Add reason attribute: reason=\"internal-ignore\", reason=\"not-task-related\", reason=\"sensitive\", or reason=\"deferred\"."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            };
            let reason = match reason_str.as_str() {
                "internal-ignore" => ExcludedFileReason::InternalIgnore,
                "not-task-related" => ExcludedFileReason::NotTaskRelated,
                "sensitive" => ExcludedFileReason::Sensitive,
                "deferred" => ExcludedFileReason::Deferred,
                other => {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::InvalidContent,
                        element_path: "ralph-commit/ralph-excluded-files/ralph-excluded-file"
                            .to_string(),
                        expected: "reason in: internal-ignore, not-task-related, sensitive, deferred".to_string(),
                        found: format!("reason=\"{other}\""),
                        suggestion: "Use one of the allowed reason values: internal-ignore, not-task-related, sensitive, deferred."
                            .to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
            };
            let path = read_text_until_end(reader, b"ralph-excluded-file")?;
            let path = path.trim().to_string();
            if !path.is_empty() {
                parse_excluded_files_events(
                    reader,
                    entry_list
                        .into_iter()
                        .chain(std::iter::once(ExcludedFile { path, reason }))
                        .collect(),
                )
            } else {
                parse_excluded_files_events(reader, entry_list)
            }
        }
        Ok(Event::Empty(ref e)) if e.name().as_ref() == b"ralph-excluded-file" => {
            Err(XsdValidationError {
                error_type: XsdErrorType::InvalidContent,
                element_path:
                    "ralph-commit/ralph-excluded-files/ralph-excluded-file".to_string(),
                expected: "non-empty repo-relative path text".to_string(),
                found: "<ralph-excluded-file .../> (empty path)".to_string(),
                suggestion: "Provide a path: <ralph-excluded-file reason=\"deferred\">path</ralph-excluded-file>.".to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::Start(ref e)) => {
            let other = e.name().as_ref().to_vec();
            let other_name = String::from_utf8_lossy(&other).to_string();
            let _ = skip_to_end(reader, &other);
            Err(XsdValidationError {
                error_type: XsdErrorType::UnexpectedElement,
                element_path: format!("ralph-commit/ralph-excluded-files/{other_name}"),
                expected: "only <ralph-excluded-file> child elements".to_string(),
                found: format!("<{other_name}>"),
                suggestion: "Inside <ralph-excluded-files>, include only <ralph-excluded-file reason=\"...\">path</ralph-excluded-file> elements."
                    .to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::Empty(ref e)) => {
            let other = e.name().as_ref().to_vec();
            let other_name = String::from_utf8_lossy(&other).to_string();
            Err(XsdValidationError {
                error_type: XsdErrorType::UnexpectedElement,
                element_path: format!("ralph-commit/ralph-excluded-files/{other_name}"),
                expected: "only <ralph-excluded-file> child elements".to_string(),
                found: format!("<{other_name}/>"),
                suggestion: "Inside <ralph-excluded-files>, include only <ralph-excluded-file reason=\"...\">path</ralph-excluded-file> elements."
                    .to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::End(ref e)) if e.name().as_ref() == b"ralph-excluded-files" => Ok(entry_list),
        Ok(Event::Text(ref t)) => {
            let text = unescape_text(t, "ralph-commit/ralph-excluded-files")?;
            if !text.trim().is_empty() {
                Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: "ralph-commit/ralph-excluded-files".to_string(),
                    expected: "whitespace-only text between <ralph-excluded-file> elements"
                        .to_string(),
                    found: format!("text content: {}", format_content_preview(text.trim())),
                    suggestion: "Remove any non-whitespace text inside <ralph-excluded-files>."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                })
            } else {
                parse_excluded_files_events(reader, entry_list)
            }
        }
        Ok(Event::CData(ref c)) => {
            let text = String::from_utf8_lossy(c.as_ref()).to_string();
            if !text.trim().is_empty() {
                Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: "ralph-commit/ralph-excluded-files".to_string(),
                    expected: "whitespace-only CDATA between <ralph-excluded-file> elements"
                        .to_string(),
                    found: format!("CDATA content: {}", format_content_preview(text.trim())),
                    suggestion:
                        "Remove any non-whitespace CDATA inside <ralph-excluded-files>."
                            .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                })
            } else {
                parse_excluded_files_events(reader, entry_list)
            }
        }
        Ok(Event::Eof) => {
            Err(XsdValidationError {
                error_type: XsdErrorType::MalformedXml,
                element_path: "ralph-commit/ralph-excluded-files".to_string(),
                expected: "closing </ralph-excluded-files> tag".to_string(),
                found: "end of content without closing tag".to_string(),
                suggestion: "Add </ralph-excluded-files> to close the excluded file list."
                    .to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::End(ref e)) => {
            let other_end = e.name().as_ref().to_vec();
            let other_end_name = String::from_utf8_lossy(&other_end).to_string();
            Err(XsdValidationError {
                error_type: XsdErrorType::MalformedXml,
                element_path: "ralph-commit/ralph-excluded-files".to_string(),
                expected: "</ralph-excluded-files> closing tag".to_string(),
                found: format!("unexpected closing tag </{other_end_name}>"),
                suggestion: "Ensure <ralph-excluded-files> contains only <ralph-excluded-file> elements and is properly closed with </ralph-excluded-files>."
                    .to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(_) => parse_excluded_files_events(reader, entry_list),
        Err(e) => Err(malformed_xml_error(&e)),
    }
}
