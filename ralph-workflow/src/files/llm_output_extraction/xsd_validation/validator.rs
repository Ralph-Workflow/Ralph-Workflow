// Core XSD validation implementation.
// Contains the main validation logic and parsed commit message types.

/// Example of a valid commit message XML for error messages.
const EXAMPLE_COMMIT_XML: &str = r"<ralph-commit>
<ralph-subject>feat(api): add user authentication</ralph-subject>
<ralph-body>Implements JWT-based authentication for the API.</ralph-body>
</ralph-commit>";

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
    use crate::files::llm_output_extraction::xml_helpers::check_for_illegal_xml_characters;

    const VALID_TAGS: [&str; 8] = [
        "ralph-subject",
        "ralph-body",
        "ralph-body-summary",
        "ralph-body-details",
        "ralph-body-footer",
        "ralph-skip",
        "ralph-files",
        "ralph-excluded-files",
    ];

    let content = xml_content.trim();

    // Check for illegal XML characters BEFORE parsing
    check_for_illegal_xml_characters(content)?;

    let mut reader = create_reader(content);
    reader.config_mut().trim_text(false);
    let mut buf = Vec::new();

    find_commit_root(&mut reader, &mut buf, content)?;

    // Parse child elements
    let mut subject: Option<String> = None;
    let mut body: Option<String> = None;
    let mut body_summary: Option<String> = None;
    let mut body_details: Option<String> = None;
    let mut body_footer: Option<String> = None;
    let mut skip_reason: Option<String> = None;
    let mut files: Vec<String> = Vec::new();
    let mut files_seen = false;
    let mut excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile> = Vec::new();
    let mut excluded_files_seen = false;

    loop {
        buf.clear();
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                match e.name().as_ref() {
                    b"ralph-subject" => {
                        if skip_reason.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-subject".to_string(),
                                expected: "either commit message elements OR ralph-skip, not both"
                                    .to_string(),
                                found: "mixed commit and skip elements".to_string(),
                                suggestion: "Use ralph-skip alone when no commit is needed."
                                    .to_string(),
                                example: Some(
                                    "<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>"
                                        .into(),
                                ),
                            });
                        }
                        if subject.is_some() {
                            return Err(duplicate_element_error("ralph-subject", "ralph-commit"));
                        }
                        subject = Some(read_text_with_inline_code_until_end(
                            &mut reader,
                            b"ralph-subject",
                            "ralph-commit/ralph-subject",
                        )?);
                    }
                    b"ralph-body" => {
                        if skip_reason.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-body".to_string(),
                                expected: "either commit message elements OR ralph-skip, not both"
                                    .to_string(),
                                found: "mixed commit and skip elements".to_string(),
                                suggestion: "Use ralph-skip alone when no commit is needed."
                                    .to_string(),
                                example: Some(
                                    "<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>"
                                        .into(),
                                ),
                            });
                        }
                        // Check for mixed body types
                        if body_summary.is_some() || body_details.is_some() || body_footer.is_some()
                        {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-body".to_string(),
                                expected:
                                    "either <ralph-body> OR detailed tags, not both".to_string(),
                                found: "mixed simple and detailed body elements".to_string(),
                                suggestion: "Use <ralph-body> for simple body OR <ralph-body-summary>, <ralph-body-details>, <ralph-body-footer> for detailed format.".to_string(),
                                example: Some(EXAMPLE_COMMIT_XML.into()),
                            });
                        }
                        if body.is_some() {
                            return Err(duplicate_element_error("ralph-body", "ralph-commit"));
                        }
                        body = Some(read_text_with_inline_code_until_end(
                            &mut reader,
                            b"ralph-body",
                            "ralph-commit/ralph-body",
                        )?);
                    }
                    b"ralph-body-summary" => {
                        if skip_reason.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-body-summary".to_string(),
                                expected: "either commit message elements OR ralph-skip, not both"
                                    .to_string(),
                                found: "mixed commit and skip elements".to_string(),
                                suggestion: "Use ralph-skip alone when no commit is needed."
                                    .to_string(),
                                example: Some(
                                    "<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>"
                                        .into(),
                                ),
                            });
                        }
                        if body.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-body-summary".to_string(),
                                expected:
                                    "either <ralph-body> OR detailed tags, not both".to_string(),
                                found: "mixed simple and detailed body elements".to_string(),
                                suggestion: "Use <ralph-body> for simple body OR <ralph-body-summary>, <ralph-body-details>, <ralph-body-footer> for detailed format.".to_string(),
                                example: Some(EXAMPLE_COMMIT_XML.into()),
                            });
                        }
                        if body_summary.is_some() {
                            return Err(duplicate_element_error(
                                "ralph-body-summary",
                                "ralph-commit",
                            ));
                        }
                        body_summary = Some(read_text_with_inline_code_until_end(
                            &mut reader,
                            b"ralph-body-summary",
                            "ralph-commit/ralph-body-summary",
                        )?);
                    }
                    b"ralph-body-details" => {
                        if skip_reason.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-body-details".to_string(),
                                expected: "either commit message elements OR ralph-skip, not both"
                                    .to_string(),
                                found: "mixed commit and skip elements".to_string(),
                                suggestion: "Use ralph-skip alone when no commit is needed."
                                    .to_string(),
                                example: Some(
                                    "<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>"
                                        .into(),
                                ),
                            });
                        }
                        if body.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-body-details".to_string(),
                                expected:
                                    "either <ralph-body> OR detailed tags, not both".to_string(),
                                found: "mixed simple and detailed body elements".to_string(),
                                suggestion: "Use <ralph-body> for simple body OR <ralph-body-summary>, <ralph-body-details>, <ralph-body-footer> for detailed format.".to_string(),
                                example: Some(EXAMPLE_COMMIT_XML.into()),
                            });
                        }
                        if body_details.is_some() {
                            return Err(duplicate_element_error(
                                "ralph-body-details",
                                "ralph-commit",
                            ));
                        }
                        body_details = Some(read_text_with_inline_code_until_end(
                            &mut reader,
                            b"ralph-body-details",
                            "ralph-commit/ralph-body-details",
                        )?);
                    }
                    b"ralph-body-footer" => {
                        if skip_reason.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-body-footer".to_string(),
                                expected: "either commit message elements OR ralph-skip, not both"
                                    .to_string(),
                                found: "mixed commit and skip elements".to_string(),
                                suggestion: "Use ralph-skip alone when no commit is needed."
                                    .to_string(),
                                example: Some(
                                    "<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>"
                                        .into(),
                                ),
                            });
                        }
                        if body.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-body-footer".to_string(),
                                expected:
                                    "either <ralph-body> OR detailed tags, not both".to_string(),
                                found: "mixed simple and detailed body elements".to_string(),
                                suggestion: "Use <ralph-body> for simple body OR <ralph-body-summary>, <ralph-body-details>, <ralph-body-footer> for detailed format.".to_string(),
                                example: Some(EXAMPLE_COMMIT_XML.into()),
                            });
                        }
                        if body_footer.is_some() {
                            return Err(duplicate_element_error(
                                "ralph-body-footer",
                                "ralph-commit",
                            ));
                        }
                        body_footer = Some(read_text_with_inline_code_until_end(
                            &mut reader,
                            b"ralph-body-footer",
                            "ralph-commit/ralph-body-footer",
                        )?);
                    }
                    b"ralph-skip" => {
                        if skip_reason.is_some() {
                            return Err(duplicate_element_error("ralph-skip", "ralph-commit"));
                        }
                        // Check for conflicting commit message elements
                        if subject.is_some()
                            || body.is_some()
                            || body_summary.is_some()
                            || body_details.is_some()
                            || body_footer.is_some()
                        {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-skip".to_string(),
                                expected: "either commit message elements OR ralph-skip, not both"
                                    .to_string(),
                                found: "mixed commit and skip elements".to_string(),
                                suggestion: "Use ralph-skip alone when no commit is needed."
                                    .to_string(),
                                example: Some(
                                    "<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>"
                                        .into(),
                                ),
                            });
                        }
                        skip_reason = Some(read_text_with_inline_code_until_end(
                            &mut reader,
                            b"ralph-skip",
                            "ralph-commit/ralph-skip",
                        )?);
                    }
                    b"ralph-files" => {
                        if files_seen {
                            return Err(duplicate_element_error("ralph-files", "ralph-commit"));
                        }
                        files_seen = true;
                        if skip_reason.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-files".to_string(),
                                expected: "either commit message elements OR ralph-skip, not both"
                                    .to_string(),
                                found: "ralph-files cannot be used with ralph-skip".to_string(),
                                suggestion: "Remove ralph-files when using ralph-skip.".to_string(),
                                example: Some(EXAMPLE_COMMIT_XML.into()),
                            });
                        }
                        files = parse_files_section(&mut reader, &mut buf)?;
                    }
                    b"ralph-excluded-files" => {
                        if excluded_files_seen {
                            return Err(duplicate_element_error(
                                "ralph-excluded-files",
                                "ralph-commit",
                            ));
                        }
                        excluded_files_seen = true;
                        if skip_reason.is_some() {
                            return Err(XsdValidationError {
                                error_type: XsdErrorType::UnexpectedElement,
                                element_path: "ralph-commit/ralph-excluded-files".to_string(),
                                expected: "either commit message elements OR ralph-skip, not both"
                                    .to_string(),
                                found: "ralph-excluded-files cannot be used with ralph-skip"
                                    .to_string(),
                                suggestion: "Remove ralph-excluded-files when using ralph-skip."
                                    .to_string(),
                                example: Some(EXAMPLE_COMMIT_XML.into()),
                            });
                        }
                        excluded_files = parse_excluded_files_section(&mut reader, &mut buf)?;
                    }
                    other => {
                        // Skip unknown element but report error
                        let _ = skip_to_end(&mut reader, other);
                        return Err(unexpected_element_error(other, &VALID_TAGS, "ralph-commit"));
                    }
                }
            }
            Ok(Event::Empty(e)) => match e.name().as_ref() {
                b"ralph-subject" => {
                    return Err(XsdValidationError {
                            error_type: XsdErrorType::InvalidContent,
                            element_path: "ralph-subject".to_string(),
                            expected: "non-empty subject line".to_string(),
                            found: "<ralph-subject/> (empty subject)".to_string(),
                            suggestion:
                                "Provide a conventional commit subject inside <ralph-subject> like 'feat: add feature'.".to_string(),
                            example: Some(EXAMPLE_COMMIT_XML.into()),
                        });
                }
                b"ralph-skip" => {
                    return Err(XsdValidationError {
                            error_type: XsdErrorType::InvalidContent,
                            element_path: "ralph-skip".to_string(),
                            expected: "non-empty skip reason".to_string(),
                            found: "<ralph-skip/> (empty skip reason)".to_string(),
                            suggestion:
                                "Provide a reason inside <ralph-skip> explaining why no commit is needed.".to_string(),
                            example: Some(
                                "<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>".into(),
                            ),
                        });
                }
                b"ralph-files" => {
                    return Err(XsdValidationError {
                            error_type: XsdErrorType::InvalidContent,
                            element_path: "ralph-commit/ralph-files".to_string(),
                            expected: "at least one ralph-file child element".to_string(),
                            found: "<ralph-files/>".to_string(),
                            suggestion:
                                "Either add one or more <ralph-file>path</ralph-file> entries or omit <ralph-files> entirely.".to_string(),
                            example: Some(EXAMPLE_COMMIT_XML.into()),
                        });
                }
                b"ralph-excluded-files" => {
                    return Err(XsdValidationError {
                            error_type: XsdErrorType::InvalidContent,
                            element_path: "ralph-commit/ralph-excluded-files".to_string(),
                            expected: "at least one ralph-excluded-file child element".to_string(),
                            found: "<ralph-excluded-files/>".to_string(),
                            suggestion:
                                "Either add one or more <ralph-excluded-file reason=\"...\">path</ralph-excluded-file> entries or omit <ralph-excluded-files> entirely.".to_string(),
                            example: Some(EXAMPLE_COMMIT_XML.into()),
                        });
                }
                other => {
                    return Err(unexpected_element_error(other, &VALID_TAGS, "ralph-commit"));
                }
            },
            Ok(Event::Text(e)) => {
                let text = unescape_text(&e, "ralph-commit")?;
                let trimmed = text.trim();
                if !trimmed.is_empty() {
                    return Err(text_outside_tags_error(trimmed, "ralph-commit"));
                }
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"ralph-commit" => break,
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-commit".to_string(),
                    expected: "closing </ralph-commit> tag".to_string(),
                    found: "end of content without closing tag".to_string(),
                    suggestion: "Add </ralph-commit> at the end of your commit message."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(_) => {} // Skip comments, etc.
            Err(e) => return Err(malformed_xml_error(&e)),
        }
    }

    ensure_no_trailing_root_content(&mut reader, &mut buf)?;

    // Validate that either skip_reason OR subject is present (but not both)
    if skip_reason.is_none() && subject.is_none() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-commit".to_string(),
            expected: "either <ralph-subject> or <ralph-skip>".to_string(),
            found: "neither commit message nor skip directive".to_string(),
            suggestion: "Provide either a commit message or skip directive.".to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        });
    }

    // If skip_reason is present, return early with skip
    if let Some(skip) = skip_reason {
        let skip = skip.trim();
        if skip.is_empty() {
            return Err(XsdValidationError {
                error_type: XsdErrorType::InvalidContent,
                element_path: "ralph-skip".to_string(),
                expected: "non-empty skip reason".to_string(),
                found: "empty skip reason".to_string(),
                suggestion: "The <ralph-skip> must contain a reason why no commit is needed."
                    .to_string(),
                example: Some(
                    "<ralph-commit><ralph-skip>No staged changes found via git status</ralph-skip></ralph-commit>"
                        .into(),
                ),
            });
        }
        return Ok(CommitMessageElements {
            subject: String::new(),
            body: None,
            body_summary: None,
            body_details: None,
            body_footer: None,
            skip_reason: Some(skip.to_string()),
            files: vec![],
            excluded_files: vec![],
        });
    }

    // Normal commit message path: validate subject
    let subject = subject.expect("subject must be Some if skip_reason is None");
    let subject = subject.trim();
    if subject.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-subject".to_string(),
            expected: "non-empty subject line".to_string(),
            found: "empty subject".to_string(),
            suggestion:
                "The <ralph-subject> must contain a non-empty commit subject like 'feat: add feature'."
                    .to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        });
    }

    // Validate conventional commit format
    if !is_conventional_commit_subject(subject) {
        return Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-subject".to_string(),
            expected: "conventional commit format (type: description or type(scope): description)"
                .to_string(),
            found: subject.to_string(),
            suggestion:
                "Use conventional commit format: type(scope): description. Valid types: feat, fix, docs, style, refactor, perf, test, build, ci, chore."
                    .to_string(),
            example: Some(EXAMPLE_COMMIT_XML.into()),
        });
    }

    Ok(CommitMessageElements {
        subject: subject.to_string(),
        body: body.filter(|s| !s.is_empty()),
        body_summary: body_summary.filter(|s| !s.is_empty()),
        body_details: body_details.filter(|s| !s.is_empty()),
        body_footer: body_footer.filter(|s| !s.is_empty()),
        skip_reason: None,
        files,
        excluded_files,
    })
}

fn find_commit_root(
    reader: &mut quick_xml::Reader<&[u8]>,
    buf: &mut Vec<u8>,
    content: &str,
) -> Result<(), XsdValidationError> {
    loop {
        match reader.read_event_into(buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == b"ralph-commit" => return Ok(()),
            Ok(Event::Empty(e)) if e.name().as_ref() == b"ralph-commit" => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "ralph-commit".to_string(),
                    expected: "either <ralph-subject> or <ralph-skip>".to_string(),
                    found: "<ralph-commit/> (empty root element)".to_string(),
                    suggestion:
                        "Use <ralph-commit>...</ralph-commit> and include either <ralph-subject> or <ralph-skip>.".to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::Start(e)) => {
                let tag_name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "ralph-commit".to_string(),
                    expected: "<ralph-commit> as root element".to_string(),
                    found: format!("<{tag_name}> (wrong root element)"),
                    suggestion: "Use <ralph-commit> as the root element.".to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::Empty(e)) => {
                let tag_name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "ralph-commit".to_string(),
                    expected: "<ralph-commit> as root element".to_string(),
                    found: format!("<{tag_name}/> (wrong root element)"),
                    suggestion: "Use <ralph-commit> as the root element.".to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "ralph-commit".to_string(),
                    expected: "<ralph-commit> as root element".to_string(),
                    found: format_content_preview(content),
                    suggestion:
                        "Wrap your commit message in <ralph-commit>...</ralph-commit> tags."
                            .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::Text(e)) => reject_non_whitespace_text(&unescape_text(&e, "ralph-commit")?)?,
            Ok(Event::CData(e)) => {
                reject_non_whitespace_text(&String::from_utf8_lossy(e.as_ref()))?;
            }
            Ok(_) => {}
            Err(e) => return Err(malformed_xml_error(&e)),
        }
        buf.clear();
    }
}

fn ensure_no_trailing_root_content(
    reader: &mut quick_xml::Reader<&[u8]>,
    buf: &mut Vec<u8>,
) -> Result<(), XsdValidationError> {
    loop {
        buf.clear();
        match reader.read_event_into(buf) {
            Ok(Event::Eof) => return Ok(()),
            Ok(Event::Text(e)) => reject_non_whitespace_text(&unescape_text(&e, "ralph-commit")?)?,
            Ok(Event::CData(e)) => {
                reject_non_whitespace_text(&String::from_utf8_lossy(e.as_ref()))?;
            }
            Ok(_) => {}
            Err(e) => return Err(malformed_xml_error(&e)),
        }
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

fn read_text_with_inline_code_until_end(
    reader: &mut quick_xml::Reader<&[u8]>,
    end_tag: &[u8],
    element_path: &str,
) -> Result<String, XsdValidationError> {
    let mut buf = Vec::new();
    let mut text = String::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Text(t)) => {
                text.push_str(&unescape_text(&t, element_path)?);
            }
            Ok(Event::CData(c)) => {
                text.push_str(&String::from_utf8_lossy(c.as_ref()));
            }
            Ok(Event::Start(e)) if e.name().as_ref() == b"code" => {
                text.push_str(&read_text_with_inline_code_until_end(
                    reader,
                    b"code",
                    &format!("{element_path}/code"),
                )?);
            }
            Ok(Event::Empty(e)) if e.name().as_ref() == b"code" => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: format!("{element_path}/code"),
                    expected: "non-empty inline <code> content".to_string(),
                    found: "<code/> (empty inline code element)".to_string(),
                    suggestion: "Use <code>text</code> when you need inline code, or remove the empty <code/> element.".to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::Start(e)) => {
                let other = e.name().as_ref().to_vec();
                let other_name = String::from_utf8_lossy(&other);
                let _ = skip_to_end(reader, &other);
                return Err(XsdValidationError {
                    error_type: XsdErrorType::UnexpectedElement,
                    element_path: format!("{element_path}/{other_name}"),
                    expected: "text content with optional inline <code> elements".to_string(),
                    found: format!("<{other_name}>"),
                    suggestion: "Use plain text and optional inline <code>...</code> only; remove any other nested tags.".to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::Empty(e)) => {
                let other = e.name().as_ref().to_vec();
                let other_name = String::from_utf8_lossy(&other);
                return Err(XsdValidationError {
                    error_type: XsdErrorType::UnexpectedElement,
                    element_path: format!("{element_path}/{other_name}"),
                    expected: "text content with optional inline <code> elements".to_string(),
                    found: format!("<{other_name}/>"),
                    suggestion: "Use plain text and optional inline <code>...</code> only; remove any other nested tags.".to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::End(e)) if e.name().as_ref() == end_tag => break,
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: element_path.to_string(),
                    expected: format!("closing </{}> tag", String::from_utf8_lossy(end_tag)),
                    found: "end of content without closing tag".to_string(),
                    suggestion: format!(
                        "Add </{}> to close the element.",
                        String::from_utf8_lossy(end_tag)
                    ),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(_) => {}
            Err(e) => return Err(malformed_xml_error(&e)),
        }
        buf.clear();
    }

    Ok(text.trim().to_string())
}

/// Parse the `<ralph-files>` section and return the list of file paths.
fn parse_files_section(
    reader: &mut quick_xml::Reader<&[u8]>,
    buf: &mut Vec<u8>,
) -> Result<Vec<String>, XsdValidationError> {
    let mut file_list: Vec<String> = Vec::new();
    loop {
        buf.clear();
        match reader.read_event_into(buf) {
            Ok(Event::Start(ref e)) if e.name().as_ref() == b"ralph-file" => {
                let text = read_text_with_inline_code_until_end(
                    reader,
                    b"ralph-file",
                    "ralph-commit/ralph-files/ralph-file",
                )?;
                let trimmed = text.trim().to_string();
                if !trimmed.is_empty() {
                    file_list.push(trimmed);
                }
            }
            Ok(Event::Empty(ref e)) if e.name().as_ref() == b"ralph-file" => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path: "ralph-commit/ralph-files/ralph-file".to_string(),
                    expected: "non-empty file path".to_string(),
                    found: "<ralph-file/> (empty file path)".to_string(),
                    suggestion:
                        "Provide a repo-relative path inside <ralph-file>path</ralph-file>."
                            .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::Start(ref e)) => {
                let other = e.name().as_ref().to_vec();
                let other_name = String::from_utf8_lossy(&other);
                let _ = skip_to_end(reader, &other);
                return Err(XsdValidationError {
                    error_type: XsdErrorType::UnexpectedElement,
                    element_path: format!("ralph-commit/ralph-files/{other_name}"),
                    expected: "only <ralph-file> child elements".to_string(),
                    found: format!("<{other_name}>"),
                    suggestion: "Inside <ralph-files>, include only one or more <ralph-file>path</ralph-file> elements. Remove any other child elements."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::Empty(ref e)) => {
                let other = e.name().as_ref().to_vec();
                let other_name = String::from_utf8_lossy(&other);
                return Err(XsdValidationError {
                    error_type: XsdErrorType::UnexpectedElement,
                    element_path: format!("ralph-commit/ralph-files/{other_name}"),
                    expected: "only <ralph-file> child elements".to_string(),
                    found: format!("<{other_name}/>"),
                    suggestion: "Inside <ralph-files>, include only one or more <ralph-file>path</ralph-file> elements. Remove any other child elements."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::End(ref e)) if e.name().as_ref() == b"ralph-files" => break,
            Ok(Event::Text(ref t)) => {
                let text = unescape_text(t, "ralph-commit/ralph-files")?;
                if !text.trim().is_empty() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::InvalidContent,
                        element_path: "ralph-commit/ralph-files".to_string(),
                        expected: "whitespace-only text between <ralph-file> elements".to_string(),
                        found: format!("text content: {}", format_content_preview(text.trim())),
                        suggestion: "Remove any non-whitespace text inside <ralph-files>; only <ralph-file> elements are allowed."
                            .to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
            }
            Ok(Event::CData(ref c)) => {
                let text = String::from_utf8_lossy(c.as_ref());
                if !text.trim().is_empty() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::InvalidContent,
                        element_path: "ralph-commit/ralph-files".to_string(),
                        expected: "whitespace-only CDATA between <ralph-file> elements".to_string(),
                        found: format!("CDATA content: {}", format_content_preview(text.trim())),
                        suggestion: "Remove any non-whitespace CDATA inside <ralph-files>; only <ralph-file> elements are allowed."
                            .to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
            }
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-commit/ralph-files".to_string(),
                    expected: "closing </ralph-files> tag".to_string(),
                    found: "end of content without closing tag".to_string(),
                    suggestion: "Add </ralph-files> to close the file list.".to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::End(ref e)) => {
                let other_end = e.name().as_ref().to_vec();
                let other_end_name = String::from_utf8_lossy(&other_end);
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-commit/ralph-files".to_string(),
                    expected: "</ralph-files> closing tag".to_string(),
                    found: format!("unexpected closing tag </{other_end_name}>"),
                    suggestion: "Ensure <ralph-files> contains only <ralph-file> elements and is properly closed with </ralph-files>."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(_) => {}
            Err(e) => return Err(malformed_xml_error(&e)),
        }
    }
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

/// Parse the `<ralph-excluded-files>` section and return the list of excluded files.
fn parse_excluded_files_section(
    reader: &mut quick_xml::Reader<&[u8]>,
    buf: &mut Vec<u8>,
) -> Result<Vec<crate::reducer::state::pipeline::ExcludedFile>, XsdValidationError> {
    use crate::reducer::state::pipeline::{ExcludedFile, ExcludedFileReason};

    let mut entry_list: Vec<ExcludedFile> = Vec::new();
    loop {
        buf.clear();
        match reader.read_event_into(buf) {
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
                    entry_list.push(ExcludedFile { path, reason });
                }
            }
            Ok(Event::Empty(ref e)) if e.name().as_ref() == b"ralph-excluded-file" => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::InvalidContent,
                    element_path:
                        "ralph-commit/ralph-excluded-files/ralph-excluded-file".to_string(),
                    expected: "non-empty repo-relative path text".to_string(),
                    found: "<ralph-excluded-file .../> (empty path)".to_string(),
                    suggestion: "Provide a path: <ralph-excluded-file reason=\"deferred\">path</ralph-excluded-file>.".to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::Start(ref e)) => {
                let other = e.name().as_ref().to_vec();
                let other_name = String::from_utf8_lossy(&other);
                let _ = skip_to_end(reader, &other);
                return Err(XsdValidationError {
                    error_type: XsdErrorType::UnexpectedElement,
                    element_path: format!("ralph-commit/ralph-excluded-files/{other_name}"),
                    expected: "only <ralph-excluded-file> child elements".to_string(),
                    found: format!("<{other_name}>"),
                    suggestion: "Inside <ralph-excluded-files>, include only <ralph-excluded-file reason=\"...\">path</ralph-excluded-file> elements."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::Empty(ref e)) => {
                let other = e.name().as_ref().to_vec();
                let other_name = String::from_utf8_lossy(&other);
                return Err(XsdValidationError {
                    error_type: XsdErrorType::UnexpectedElement,
                    element_path: format!("ralph-commit/ralph-excluded-files/{other_name}"),
                    expected: "only <ralph-excluded-file> child elements".to_string(),
                    found: format!("<{other_name}/>"),
                    suggestion: "Inside <ralph-excluded-files>, include only <ralph-excluded-file reason=\"...\">path</ralph-excluded-file> elements."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::End(ref e)) if e.name().as_ref() == b"ralph-excluded-files" => break,
            Ok(Event::Text(ref t)) => {
                let text = unescape_text(t, "ralph-commit/ralph-excluded-files")?;
                if !text.trim().is_empty() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::InvalidContent,
                        element_path: "ralph-commit/ralph-excluded-files".to_string(),
                        expected: "whitespace-only text between <ralph-excluded-file> elements"
                            .to_string(),
                        found: format!("text content: {}", format_content_preview(text.trim())),
                        suggestion: "Remove any non-whitespace text inside <ralph-excluded-files>."
                            .to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
            }
            Ok(Event::Eof) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-commit/ralph-excluded-files".to_string(),
                    expected: "closing </ralph-excluded-files> tag".to_string(),
                    found: "end of content without closing tag".to_string(),
                    suggestion: "Add </ralph-excluded-files> to close the excluded file list."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(Event::CData(ref c)) => {
                let text = String::from_utf8_lossy(c.as_ref());
                if !text.trim().is_empty() {
                    return Err(XsdValidationError {
                        error_type: XsdErrorType::InvalidContent,
                        element_path: "ralph-commit/ralph-excluded-files".to_string(),
                        expected: "whitespace-only CDATA between <ralph-excluded-file> elements"
                            .to_string(),
                        found: format!("CDATA content: {}", format_content_preview(text.trim())),
                        suggestion:
                            "Remove any non-whitespace CDATA inside <ralph-excluded-files>."
                                .to_string(),
                        example: Some(EXAMPLE_COMMIT_XML.into()),
                    });
                }
            }
            Ok(Event::End(ref e)) => {
                let other_end = e.name().as_ref().to_vec();
                let other_end_name = String::from_utf8_lossy(&other_end);
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-commit/ralph-excluded-files".to_string(),
                    expected: "</ralph-excluded-files> closing tag".to_string(),
                    found: format!("unexpected closing tag </{other_end_name}>"),
                    suggestion: "Ensure <ralph-excluded-files> contains only <ralph-excluded-file> elements and is properly closed with </ralph-excluded-files>."
                        .to_string(),
                    example: Some(EXAMPLE_COMMIT_XML.into()),
                });
            }
            Ok(_) => {}
            Err(e) => return Err(malformed_xml_error(&e)),
        }
    }
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

/// Parsed commit message elements from valid XML.
///
/// This struct contains all the elements that were successfully
/// extracted and validated from the XML content.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitMessageElements {
    /// The commit subject line (required)
    /// Format: type(scope): description
    pub subject: String,
    /// Optional simple body content (mutually exclusive with detailed elements)
    pub body: Option<String>,
    /// Optional body summary (for detailed format)
    pub body_summary: Option<String>,
    /// Optional body details (for detailed format)
    pub body_details: Option<String>,
    /// Optional body footer (for detailed format)
    pub body_footer: Option<String>,
    /// Optional skip reason (mutually exclusive with commit message)
    /// When present, indicates AI determined no commit is needed
    pub skip_reason: Option<String>,
    /// Optional list of files to selectively stage for this commit.
    ///
    /// When empty (the default), all changed files are committed.
    /// When non-empty, only the listed paths are staged.
    pub files: Vec<String>,
    /// Files excluded from this commit with documented reasons.
    ///
    /// Populated from `<ralph-excluded-files>` in the commit XML.
    /// Audit/observability only — does not affect commit execution.
    pub excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile>,
}

impl CommitMessageElements {
    /// Format all body elements into a single body string.
    ///
    /// Combines the simple body or detailed elements into a formatted
    /// commit message body string suitable for git commit.
    pub(crate) fn format_body(&self) -> String {
        // If simple body exists, use it directly
        if let Some(ref body) = self.body {
            return body.clone();
        }

        // Otherwise, combine detailed elements
        let mut parts = Vec::new();

        if let Some(ref summary) = self.body_summary {
            parts.push(summary.trim());
        }

        if let Some(ref details) = self.body_details {
            parts.push(details.trim());
        }

        if let Some(ref footer) = self.body_footer {
            parts.push(footer.trim());
        }

        if parts.is_empty() {
            String::new()
        } else {
            parts.join("\n\n")
        }
    }
}
