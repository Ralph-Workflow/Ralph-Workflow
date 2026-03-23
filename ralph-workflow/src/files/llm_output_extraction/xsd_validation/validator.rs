// Core XSD validation implementation.
// Contains the main validation logic and parsed commit message types.

mod types;
mod file_list_parsers;

pub use types::CommitMessageElements;

use crate::files::llm_output_extraction::xml_helpers::check_for_illegal_xml_characters;
use crate::reducer::state::pipeline::ExcludedFile;
use quick_xml::Reader;

use self::file_list_parsers::{parse_excluded_files_section, parse_files_section};

/// Example of a valid commit message XML for error messages.
const EXAMPLE_COMMIT_XML: &str = r"<ralph-commit>
<ralph-subject>feat(api): add user authentication</ralph-subject>
<ralph-body>Implements JWT-based authentication for the API.</ralph-body>
</ralph-commit>";

/// Result type for text-reading functions that return the reader state.
type TextReadResult<'a> = Result<(String, Reader<&'a [u8]>), XsdValidationError>;

/// Result type for commit element parsing functions.
type CommitParseResult<'a> = Result<(Reader<&'a [u8]>, ValidatorState), XsdValidationError>;

fn configure_validation_reader(mut reader: Reader<&[u8]>) -> Reader<&[u8]> {
    reader.config_mut().trim_text(false);
    reader
}

#[derive(Default)]
struct ValidatorState {
    subject: Option<String>,
    body: Option<String>,
    body_summary: Option<String>,
    body_details: Option<String>,
    body_footer: Option<String>,
    skip_reason: Option<String>,
    files: Vec<String>,
    files_seen: bool,
    excluded_files: Vec<ExcludedFile>,
    excluded_files_seen: bool,
}

impl ValidatorState {
    fn with_subject(mut self, subject: String) -> Self {
        self.subject = Some(subject);
        self
    }

    fn with_body(mut self, body: String) -> Self {
        self.body = Some(body);
        self
    }

    fn with_body_summary(mut self, summary: String) -> Self {
        self.body_summary = Some(summary);
        self
    }

    fn with_body_details(mut self, details: String) -> Self {
        self.body_details = Some(details);
        self
    }

    fn with_body_footer(mut self, footer: String) -> Self {
        self.body_footer = Some(footer);
        self
    }

    fn with_skip_reason(mut self, reason: String) -> Self {
        self.skip_reason = Some(reason);
        self
    }

    fn with_files(mut self, files: Vec<String>) -> Self {
        self.files = files;
        self.files_seen = true;
        self
    }

    fn with_excluded_files(mut self, excluded_files: Vec<ExcludedFile>) -> Self {
        self.excluded_files = excluded_files;
        self.excluded_files_seen = true;
        self
    }
}

/// Validate XML content against the XSD schema.
///
/// This function validates that the XML content conforms to the expected
/// commit message format defined in `commit_message.xsd`:
///
/// ```xml
/// <ralph-commit>
///   <ralph-subject>type(scope): description</ralph-subject>
///   <ralph-body>Optional body text</ralph-body>
///   <ralph-body-summary>Optional summary</ralph-body-summary>
///   <ralph-body-details>Optional details</ralph-body-details>
///   <ralph-body-footer>Optional footer</ralph-body-footer>
/// </ralph-commit>
/// ```
///
/// # Arguments
///
/// * `xml_content` - The XML content to validate
///
/// # Returns
///
/// * `Ok(CommitMessageElements)` if the XML is valid and contains all required elements
/// * `Err(XsdValidationError)` if the XML is invalid or doesn't conform to the schema
///
/// # Errors
///
/// Returns an `XsdValidationError` if the XML is malformed, contains illegal characters,
/// is missing the root `<ralph-commit>` element, is missing the required `<ralph-subject>` element,
/// or if the `<ralph-subject>` does not conform to the conventional commit format.
///
/// # Panics
///
/// Panics if an internal invariant is violated: `subject` is `None` when `skip_reason` is also
/// `None` after parsing. This should never happen in practice.
///
/// # Examples
///
/// ```ignore
/// use ralph_workflow::files::llm_output_extraction::xsd_validation::validate_xml_against_xsd;
///
/// let xml = r#"<ralph-commit>
/// <ralph-subject>feat: add new feature</ralph-subject>
/// </ralph-commit>"#;
/// let result = validate_xml_against_xsd(xml);
/// assert!(result.is_ok());
/// ```
pub fn validate_xml_against_xsd(
    xml_content: &str,
) -> Result<CommitMessageElements, XsdValidationError> {
    let content = xml_content.trim();

    // Check for illegal XML characters BEFORE parsing
    check_for_illegal_xml_characters(content)?;

    let reader = configure_validation_reader(create_reader(content));

    let reader = find_commit_root(reader, content)?;

    let (reader, state) = parse_commit_elements(reader, ValidatorState::default())?;

    ensure_no_trailing_root_content(reader)?;

    // Validate that either skip_reason OR subject is present (but not both)
    if state.skip_reason.is_none() && state.subject.is_none() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-commit".to_string(),
            expected: "either <ralph-subject> or <ralph-skip>".to_string(),
            found: "neither commit message nor skip directive".to_string(),
            suggestion: "Provide either a commit message or skip directive.".to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        });
    }

    if let Some(skip_reason) = state.skip_reason {
        let skip_trimmed = skip_reason.trim();
        if skip_trimmed.is_empty() {
            return Err(XsdValidationError {
                error_type: XsdErrorType::InvalidContent,
                element_path: "ralph-skip".to_string(),
                expected: "non-empty skip reason".to_string(),
                found: "empty skip reason".to_string(),
                suggestion: "The <ralph-skip> must contain a reason why no commit is needed.".to_string(),
                example: Some("<ralph-commit><ralph-skip>No staged changes found via git status</ralph-skip></ralph-commit>".into()),
            });
        }
        return Ok(CommitMessageElements {
            subject: String::new(),
            body: None,
            body_summary: None,
            body_details: None,
            body_footer: None,
            skip_reason: Some(skip_trimmed.to_string()),
            files: Vec::new(),
            excluded_files: Vec::new(),
        });
    }

    let subject = state
        .subject
        .expect("subject must be Some if skip_reason is None");
    let subject = subject.trim();
    if subject.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-subject".to_string(),
            expected: "non-empty subject line".to_string(),
            found: "empty subject".to_string(),
            suggestion: "The <ralph-subject> must contain a non-empty commit subject like 'feat: add feature'.".to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        });
    }

    if !is_conventional_commit_subject(subject) {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-subject".to_string(),
            expected: "conventional commit format (type: description or type(scope): description)".to_string(),
            found: subject.to_string(),
            suggestion: "Use conventional commit format: type(scope): description. Valid types: feat, fix, docs, style, refactor, perf, test, build, ci, chore.".to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        });
    }

    Ok(CommitMessageElements {
        subject: subject.to_string(),
        body: state.body.filter(|s| !s.is_empty()),
        body_summary: state.body_summary.filter(|s| !s.is_empty()),
        body_details: state.body_details.filter(|s| !s.is_empty()),
        body_footer: state.body_footer.filter(|s| !s.is_empty()),
        skip_reason: None,
        files: state.files,
        excluded_files: state.excluded_files,
    })
}

fn parse_commit_elements<'a>(
    mut reader: Reader<&'a [u8]>,
    state: ValidatorState,
) -> CommitParseResult<'a> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => match e.name().as_ref() {
            b"ralph-subject" => {
                if state.skip_reason.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-subject".to_string(),
                        expected: "either commit message elements OR ralph-skip, not both".to_string(),
                        found: "mixed commit and skip elements".to_string(),
                        suggestion: "Use ralph-skip alone when no commit is needed.".to_string(),
                        example: Some("<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>".into()),
                    });
                }
                if state.subject.is_some() {
                    return Err(duplicate_element_error("ralph-subject", "ralph-commit"));
                }
                let (text, reader) = read_text_with_inline_code_until_end(
                    reader,
                    b"ralph-subject",
                    "ralph-commit/ralph-subject",
                )?;
                parse_commit_elements(reader, state.with_subject(text))
            }
            b"ralph-body" => {
                if state.skip_reason.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-body".to_string(),
                        expected: "either commit message elements OR ralph-skip, not both".to_string(),
                        found: "mixed commit and skip elements".to_string(),
                        suggestion: "Use ralph-skip alone when no commit is needed.".to_string(),
                        example: Some("<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>".into()),
                    });
                }
                if state.body_summary.is_some() || state.body_details.is_some() || state.body_footer.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-body".to_string(),
                        expected: "either <ralph-body> OR detailed tags, not both".to_string(),
                        found: "mixed simple and detailed body elements".to_string(),
                        suggestion: "Use <ralph-body> for simple body OR <ralph-body-summary>, <ralph-body-details>, <ralph-body-footer> for detailed format.".to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
                if state.body.is_some() {
                    return Err(duplicate_element_error("ralph-body", "ralph-commit"));
                }
                let (text, reader) = read_text_with_inline_code_until_end(
                    reader,
                    b"ralph-body",
                    "ralph-commit/ralph-body",
                )?;
                parse_commit_elements(reader, state.with_body(text))
            }
            b"ralph-body-summary" => {
                if state.skip_reason.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-body-summary".to_string(),
                        expected: "either commit message elements OR ralph-skip, not both".to_string(),
                        found: "mixed commit and skip elements".to_string(),
                        suggestion: "Use ralph-skip alone when no commit is needed.".to_string(),
                        example: Some("<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>".into()),
                    });
                }
                if state.body.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-body-summary".to_string(),
                        expected: "either <ralph-body> OR detailed tags, not both".to_string(),
                        found: "mixed simple and detailed body elements".to_string(),
                        suggestion: "Use <ralph-body> for simple body OR <ralph-body-summary>, <ralph-body-details>, <ralph-body-footer> for detailed format.".to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
                if state.body_summary.is_some() {
                    return Err(duplicate_element_error("ralph-body-summary", "ralph-commit"));
                }
                let (text, reader) = read_text_with_inline_code_until_end(
                    reader,
                    b"ralph-body-summary",
                    "ralph-commit/ralph-body-summary",
                )?;
                parse_commit_elements(reader, state.with_body_summary(text))
            }
            b"ralph-body-details" => {
                if state.skip_reason.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-body-details".to_string(),
                        expected: "either commit message elements OR ralph-skip, not both".to_string(),
                        found: "mixed commit and skip elements".to_string(),
                        suggestion: "Use ralph-skip alone when no commit is needed.".to_string(),
                        example: Some("<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>".into()),
                    });
                }
                if state.body.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-body-details".to_string(),
                        expected: "either <ralph-body> OR detailed tags, not both".to_string(),
                        found: "mixed simple and detailed body elements".to_string(),
                        suggestion: "Use <ralph-body> for simple body OR <ralph-body-summary>, <ralph-body-details>, <ralph-body-footer> for detailed format.".to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
                if state.body_details.is_some() {
                    return Err(duplicate_element_error("ralph-body-details", "ralph-commit"));
                }
                let (text, reader) = read_text_with_inline_code_until_end(
                    reader,
                    b"ralph-body-details",
                    "ralph-commit/ralph-body-details",
                )?;
                parse_commit_elements(reader, state.with_body_details(text))
            }
            b"ralph-body-footer" => {
                if state.skip_reason.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-body-footer".to_string(),
                        expected: "either commit message elements OR ralph-skip, not both".to_string(),
                        found: "mixed commit and skip elements".to_string(),
                        suggestion: "Use ralph-skip alone when no commit is needed.".to_string(),
                        example: Some("<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>".into()),
                    });
                }
                if state.body.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-body-footer".to_string(),
                        expected: "either <ralph-body> OR detailed tags, not both".to_string(),
                        found: "mixed simple and detailed body elements".to_string(),
                        suggestion: "Use <ralph-body> for simple body OR <ralph-body-summary>, <ralph-body-details>, <ralph-body-footer> for detailed format.".to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
                if state.body_footer.is_some() {
                    return Err(duplicate_element_error("ralph-body-footer", "ralph-commit"));
                }
                let (text, reader) = read_text_with_inline_code_until_end(
                    reader,
                    b"ralph-body-footer",
                    "ralph-commit/ralph-body-footer",
                )?;
                parse_commit_elements(reader, state.with_body_footer(text))
            }
            b"ralph-skip" => {
                if state.skip_reason.is_some() {
                    return Err(duplicate_element_error("ralph-skip", "ralph-commit"));
                }
                if state.subject.is_some() || state.body.is_some() || state.body_summary.is_some() || state.body_details.is_some() || state.body_footer.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-skip".to_string(),
                        expected: "either commit message elements OR ralph-skip, not both".to_string(),
                        found: "mixed commit and skip elements".to_string(),
                        suggestion: "Use ralph-skip alone when no commit is needed.".to_string(),
                        example: Some("<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>".into()),
                    });
                }
                let (text, reader) = read_text_with_inline_code_until_end(
                    reader,
                    b"ralph-skip",
                    "ralph-commit/ralph-skip",
                )?;
                parse_commit_elements(reader, state.with_skip_reason(text))
            }
            b"ralph-files" => {
                if state.files_seen {
                    return Err(duplicate_element_error("ralph-files", "ralph-commit"));
                }
                if state.skip_reason.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-files".to_string(),
                        expected: "either commit message elements OR ralph-skip, not both".to_string(),
                        found: "ralph-files cannot be used with ralph-skip".to_string(),
                        suggestion: "Remove ralph-files when using ralph-skip.".to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
                let files = parse_files_section(&mut reader)?;
                parse_commit_elements(reader, state.with_files(files))
            }
            b"ralph-excluded-files" => {
                if state.excluded_files_seen {
                    return Err(duplicate_element_error("ralph-excluded-files", "ralph-commit"));
                }
                if state.skip_reason.is_some() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::UnexpectedElement,
                        element_path: "ralph-commit/ralph-excluded-files".to_string(),
                        expected: "either commit message elements OR ralph-skip, not both".to_string(),
                        found: "ralph-excluded-files cannot be used with ralph-skip".to_string(),
                        suggestion: "Remove ralph-excluded-files when using ralph-skip.".to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
                let excluded = parse_excluded_files_section(&mut reader)?;
                parse_commit_elements(reader, state.with_excluded_files(excluded))
            }
            other => {
                let _ = skip_to_end(&mut reader, other);
                parse_commit_elements(reader, state)
            }
        },
        Ok(Event::Empty(e)) => match e.name().as_ref() {
            b"ralph-subject" => {
                Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: "ralph-subject".to_string(),
                    expected: "non-empty subject line".to_string(),
                    found: "<ralph-subject/> (empty subject)".to_string(),
                    suggestion: "Provide a conventional commit subject inside <ralph-subject> like 'feat: add feature'.".to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                })
            }
            b"ralph-skip" => {
                Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: "ralph-skip".to_string(),
                    expected: "non-empty skip reason".to_string(),
                    found: "<ralph-skip/> (empty skip reason)".to_string(),
                    suggestion: "Provide a reason inside <ralph-skip> explaining why no commit is needed.".to_string(),
                    example: Some("<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>".into()),
                })
            }
            b"ralph-files" => {
                Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: "ralph-commit/ralph-files".to_string(),
                    expected: "at least one ralph-file child element".to_string(),
                    found: "<ralph-files/>".to_string(),
                    suggestion: "Either add one or more <ralph-file>path</ralph-file> entries or omit <ralph-files> entirely.".to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                })
            }
            b"ralph-excluded-files" => {
                Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: "ralph-commit/ralph-excluded-files".to_string(),
                    expected: "at least one ralph-excluded-file child element".to_string(),
                    found: "<ralph-excluded-files/>".to_string(),
                    suggestion: r#"Either add one or more <ralph-excluded-file reason="...">path</ralph-excluded-file> entries or omit <ralph-excluded-files> entirely."#.to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                })
            }
            _ => {
                parse_commit_elements(reader, state)
            }
        },
        Ok(Event::End(e)) if e.name().as_ref() == b"ralph-commit" => {
            Ok((reader, state))
        }
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "ralph-commit".to_string(),
            expected: "closing </ralph-commit> tag".to_string(),
            found: "end of content without closing tag".to_string(),
            suggestion: "Add </ralph-commit> at the end of your commit message.".to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        }),
        Ok(Event::Text(_) | _) => {
            parse_commit_elements(reader, state)
        }
        Err(e) => Err(malformed_xml_error(&e)),
    }
}

fn find_commit_root<'a>(
    mut reader: Reader<&'a [u8]>,
    _content: &str,
) -> Result<Reader<&'a [u8]>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"ralph-commit" => {
            Ok(reader)
        }
        Ok(Event::Empty(e)) if e.name().as_ref() == b"ralph-commit" => Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-commit".to_string(),
            expected: "either <ralph-subject> or <ralph-skip>".to_string(),
            found: "<ralph-commit/> (empty root element)".to_string(),
            suggestion: "Use <ralph-commit>...</ralph-commit> and include either <ralph-subject> or <ralph-skip>.".to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        }),
        Ok(Event::Start(e)) => {
            let tag_name = String::from_utf8_lossy(e.name().as_ref()).to_string();
            Err(XsdValidationError {
                error_type: XsdErrorType::MissingRequiredElement,
                element_path: "ralph-commit".to_string(),
                expected: "<ralph-commit> as root element".to_string(),
                found: format!("<{tag_name}> (wrong root element)"),
                suggestion: "Use <ralph-commit> as the root element.".to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::Empty(e)) => {
            let tag_name = String::from_utf8_lossy(e.name().as_ref()).to_string();
            Err(XsdValidationError {
                error_type: XsdErrorType::MissingRequiredElement,
                element_path: "ralph-commit".to_string(),
                expected: "<ralph-commit> as root element".to_string(),
                found: format!("<{tag_name}/> (wrong root element)"),
                suggestion: "Use <ralph-commit> as the root element.".to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-commit".to_string(),
            expected: "<ralph-commit> as root element".to_string(),
            found: format_content_preview(_content),
            suggestion: "Wrap your commit message in <ralph-commit>...</ralph-commit> tags.".to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        }),
        Ok(Event::Text(e)) => {
            reject_non_whitespace_text(&unescape_text(&e, "ralph-commit")?)?;
            find_commit_root(reader, _content)
        }
        Ok(Event::CData(e)) => {
            reject_non_whitespace_text(&String::from_utf8_lossy(e.as_ref()))?;
            find_commit_root(reader, _content)
        }
        Ok(_) => {
            find_commit_root(reader, _content)
        }
        Err(e) => Err(malformed_xml_error(&e)),
    }
}

fn ensure_no_trailing_root_content(
    mut reader: Reader<&[u8]>,
) -> Result<(), XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Eof) => Ok(()),
        Ok(Event::Text(e)) => {
            reject_non_whitespace_text(&unescape_text(&e, "ralph-commit")?)?;
            ensure_no_trailing_root_content(reader)
        }
        Ok(Event::CData(e)) => {
            reject_non_whitespace_text(&String::from_utf8_lossy(e.as_ref()))?;
            ensure_no_trailing_root_content(reader)
        }
        Ok(_) => {
            ensure_no_trailing_root_content(reader)
        }
        Err(e) => Err(malformed_xml_error(&e)),
    }
}

fn reject_non_whitespace_text(text: &str) -> Result<(), XsdValidationError> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        Ok(())
    } else {
        Err(text_outside_tags_error(trimmed, "ralph-commit"))
    }
}

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

fn read_text_with_inline_code_until_end<'a>(
    reader: Reader<&'a [u8]>,
    end_tag: &[u8],
    element_path: &str,
) -> TextReadResult<'a> {
    read_text_with_inline_code_until_end_with_acc(reader, end_tag, element_path, String::new())
}

fn read_text_with_inline_code_until_end_with_acc<'a>(
    mut reader: Reader<&'a [u8]>,
    end_tag: &[u8],
    element_path: &str,
    text: String,
) -> TextReadResult<'a> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Text(t)) => {
            let text = text + &unescape_text(&t, element_path)?;
            read_text_with_inline_code_until_end_with_acc(reader, end_tag, element_path, text)
        }
        Ok(Event::CData(c)) => {
            let text = text + &String::from_utf8_lossy(c.as_ref());
            read_text_with_inline_code_until_end_with_acc(reader, end_tag, element_path, text)
        }
        Ok(Event::Start(e)) if e.name().as_ref() == b"code" => {
            let nested_path = format!("{}/code", element_path);
            let (inner, reader) = read_text_with_inline_code_until_end(
                reader,
                b"code",
                &nested_path,
            )?;
            let text = text + &inner;
            read_text_with_inline_code_until_end_with_acc(reader, end_tag, element_path, text)
        }
        Ok(Event::Empty(e)) if e.name().as_ref() == b"code" => Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: format!("{}/code", element_path),
            expected: "non-empty inline <code> content".to_string(),
            found: "<code/> (empty inline code element)".to_string(),
            suggestion: "Use <code>text</code> when you need inline code, or remove the empty <code/> element.".to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        }),
        Ok(Event::Start(e)) => {
            let other = e.name().as_ref().to_vec();
            let other_name = String::from_utf8_lossy(&other);
            let _ = skip_to_end(&mut reader, &other);
            let _ = skip_to_end(&mut reader, &other);
            Err(XsdValidationError {
                error_type: XsdErrorType::UnexpectedElement,
                element_path: format!("{}/{}", element_path, other_name),
                expected: "text content with optional inline <code> elements".to_string(),
                found: format!("<{other_name}>"),
                suggestion: "Use plain text and optional inline <code>...</code> only; remove any other nested tags.".to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::Empty(e)) => {
            let other = e.name().as_ref().to_vec();
            let other_name = String::from_utf8_lossy(&other);
            Err(XsdValidationError {
                error_type: XsdErrorType::UnexpectedElement,
                element_path: format!("{}/{}", element_path, other_name),
                expected: "text content with optional inline <code> elements".to_string(),
                found: format!("<{other_name}/>"),
                suggestion: "Use plain text and optional inline <code>...</code> only; remove any other nested tags.".to_string(),
                example: Some(EXAMPLE_COMMIT_XML.into()),
            })
        }
        Ok(Event::End(e)) if e.name().as_ref() == end_tag => {
            Ok((text.trim().to_string(), reader))
        }
        Ok(Event::Eof) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: element_path.to_string(),
            expected: format!("closing </{}> tag", String::from_utf8_lossy(end_tag)),
            found: "end of content without closing tag".to_string(),
            suggestion: format!("Add </{}> to close the element.", String::from_utf8_lossy(end_tag)),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        }),
        Ok(_) => {
            read_text_with_inline_code_until_end_with_acc(reader, end_tag, element_path, text)
        }
        Err(e) => Err(malformed_xml_error(&e)),
    }
}
