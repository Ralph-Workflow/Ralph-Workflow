// Critical files parsing functions (parse_critical_files, parse_primary_files, parse_reference_files)

// Note: All types (Reader, Event, XsdValidationError, XsdErrorType, HashMap,
// CriticalFiles, PrimaryFile, ReferenceFile, FileAction) and helpers (get_attributes,
// skip_to_end) are available via the include! chain in mod.rs/validation.rs.

/// Parse the ralph-critical-files section.
///
/// Tolerant behavior: bare `<file>` elements directly under `<ralph-critical-files>`
/// (without a `<primary-files>` or `<reference-files>` wrapper) are classified by
/// their unambiguous attributes:
/// - File with `action` only → primary file
/// - File with `purpose` only → reference file
/// - File with both or neither → rejected as ambiguous
///
/// The `original_tag` parameter is used for fuzzy matching - when the opening tag was misspelled,
/// this allows the parser to accept either the canonical closing tag OR the original misspelled one.
fn parse_critical_files(
    reader: &mut Reader<&[u8]>,
    original_tag: &[u8],
) -> Result<CriticalFiles, XsdValidationError> {
    let canonical_tag = b"ralph-critical-files";
    let (primary_files, reference_files) =
        parse_critical_files_events(reader, original_tag, canonical_tag, Vec::new(), Vec::new())?;

    if primary_files.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-critical-files/primary-files".to_string(),
            expected: "at least one <file> element".to_string(),
            found: "no files".to_string(),
            suggestion: "Add <file path=\"...\" action=\"modify\"/> to primary-files".to_string(),
            example: None,
        });
    }

    Ok(CriticalFiles {
        primary_files,
        reference_files,
    })
}

fn parse_critical_files_events(
    reader: &mut Reader<&[u8]>,
    original_tag: &[u8],
    canonical_tag: &[u8],
    primary_files: Vec<PrimaryFile>,
    reference_files: Vec<ReferenceFile>,
) -> Result<(Vec<PrimaryFile>, Vec<ReferenceFile>), XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => match e.name().as_ref() {
            b"primary-files" => {
                let additional_primary = parse_primary_files(reader)?;
                parse_critical_files_events(
                    reader,
                    original_tag,
                    canonical_tag,
                    primary_files
                        .into_iter()
                        .chain(additional_primary)
                        .collect(),
                    reference_files,
                )
            }
            b"reference-files" => {
                let additional_reference = parse_reference_files(reader)?;
                parse_critical_files_events(
                    reader,
                    original_tag,
                    canonical_tag,
                    primary_files,
                    reference_files
                        .into_iter()
                        .chain(additional_reference)
                        .collect(),
                )
            }
            b"file" => {
                let classification = classify_bare_critical_file(&get_attributes(&e))?;
                let _ = skip_to_end(reader, b"file");
                match classification {
                    BareCriticalFile::Primary(file) => parse_critical_files_events(
                        reader,
                        original_tag,
                        canonical_tag,
                        primary_files
                            .into_iter()
                            .chain(std::iter::once(file))
                            .collect(),
                        reference_files,
                    ),
                    BareCriticalFile::Reference(file) => parse_critical_files_events(
                        reader,
                        original_tag,
                        canonical_tag,
                        primary_files,
                        reference_files
                            .into_iter()
                            .chain(std::iter::once(file))
                            .collect(),
                    ),
                }
            }
            _ => {
                let _ = skip_to_end(reader, e.name().as_ref());
                parse_critical_files_events(
                    reader,
                    original_tag,
                    canonical_tag,
                    primary_files,
                    reference_files,
                )
            }
        },
        Ok(Event::Empty(e)) if e.name().as_ref() == b"file" => {
            match classify_bare_critical_file(&get_attributes(&e))? {
                BareCriticalFile::Primary(file) => parse_critical_files_events(
                    reader,
                    original_tag,
                    canonical_tag,
                    primary_files
                        .into_iter()
                        .chain(std::iter::once(file))
                        .collect(),
                    reference_files,
                ),
                BareCriticalFile::Reference(file) => parse_critical_files_events(
                    reader,
                    original_tag,
                    canonical_tag,
                    primary_files,
                    reference_files
                        .into_iter()
                        .chain(std::iter::once(file))
                        .collect(),
                ),
            }
        }
        Ok(Event::End(e))
            if e.name().as_ref() == canonical_tag || e.name().as_ref() == original_tag =>
        {
            Ok((primary_files, reference_files))
        }
        Ok(Event::Eof) => Ok((primary_files, reference_files)),
        Ok(_) => parse_critical_files_events(
            reader,
            original_tag,
            canonical_tag,
            primary_files,
            reference_files,
        ),
        Err(e) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "ralph-critical-files".to_string(),
            expected: "valid XML".to_string(),
            found: format!("parse error: {e}"),
            suggestion: "Check XML syntax".to_string(),
            example: None,
        }),
    }
}

/// Classify a bare `<file>` element under `<ralph-critical-files>` into primary or reference.
///
/// Classification rules:
/// - Has `action` only → primary file
/// - Has `purpose` only → reference file
/// - Has both or neither → rejected as ambiguous
enum BareCriticalFile {
    Primary(PrimaryFile),
    Reference(ReferenceFile),
}

fn classify_bare_critical_file(
    attrs: &HashMap<String, String>,
) -> Result<BareCriticalFile, XsdValidationError> {
    let Some(path) = attrs.get("path").cloned() else {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-critical-files/file".to_string(),
            expected: "path attribute".to_string(),
            found: "no path attribute".to_string(),
            suggestion: "Add path=\"...\" to the critical file element".to_string(),
            example: None,
        });
    };

    match (attrs.get("action"), attrs.get("purpose")) {
        (Some(action_str), None) => {
            let action = FileAction::from_str(action_str).ok_or_else(|| XsdValidationError {
                error_type: XsdErrorType::InvalidContent,
                element_path: "ralph-critical-files/file/@action".to_string(),
                expected: "create, modify, or delete".to_string(),
                found: action_str.clone(),
                suggestion: "Use action=\"create\", action=\"modify\", or action=\"delete\""
                    .to_string(),
                example: None,
            })?;
            Ok(BareCriticalFile::Primary(PrimaryFile {
                path,
                action,
                estimated_changes: attrs.get("estimated-changes").cloned(),
            }))
        }
        (None, Some(purpose)) => Ok(BareCriticalFile::Reference(ReferenceFile {
            path,
            purpose: purpose.clone(),
        })),
        (Some(_), Some(_)) => Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-critical-files/file".to_string(),
            expected: "exactly one classification attribute: action or purpose".to_string(),
            found: format!("file {path:?} has both action and purpose"),
            suggestion:
                "Keep action for a primary file or purpose for a reference file, but not both"
                    .to_string(),
            example: None,
        }),
        (None, None) => Err(XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "ralph-critical-files/file".to_string(),
            expected: "exactly one classification attribute: action or purpose".to_string(),
            found: format!("file {path:?} has neither action nor purpose"),
            suggestion: "Add action for a primary file or purpose for a reference file".to_string(),
            example: None,
        }),
    }
}

/// Parse primary-files
fn parse_primary_files(reader: &mut Reader<&[u8]>) -> Result<Vec<PrimaryFile>, XsdValidationError> {
    parse_primary_files_events(reader, Vec::new())
}

fn parse_primary_files_events(
    reader: &mut Reader<&[u8]>,
    files: Vec<PrimaryFile>,
) -> Result<Vec<PrimaryFile>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e) | Event::Empty(e)) if e.name().as_ref() == b"file" => {
            let attrs = get_attributes(&e);
            let path = attrs
                .get("path")
                .cloned()
                .ok_or_else(|| XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "primary-files/file".to_string(),
                    expected: "path attribute".to_string(),
                    found: "no path attribute".to_string(),
                    suggestion: "Add path=\"...\" to the file element".to_string(),
                    example: None,
                })?;

            let action_str = attrs
                .get("action")
                .cloned()
                .ok_or_else(|| XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "primary-files/file".to_string(),
                    expected: "action attribute".to_string(),
                    found: "no action attribute".to_string(),
                    suggestion: "Add action=\"create|modify|delete\" to the file element"
                        .to_string(),
                    example: None,
                })?;

            let action = FileAction::from_str(&action_str).ok_or_else(|| XsdValidationError {
                error_type: XsdErrorType::InvalidContent,
                element_path: "primary-files/file/@action".to_string(),
                expected: "create, modify, or delete".to_string(),
                found: action_str,
                suggestion: "Use action=\"create\", action=\"modify\", or action=\"delete\""
                    .to_string(),
                example: None,
            })?;

            parse_primary_files_events(
                reader,
                files
                    .into_iter()
                    .chain(std::iter::once(PrimaryFile {
                        path,
                        action,
                        estimated_changes: attrs.get("estimated-changes").cloned(),
                    }))
                    .collect(),
            )
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"primary-files" => Ok(files),
        Ok(Event::Eof) | Err(_) => Ok(files),
        Ok(_) => parse_primary_files_events(reader, files),
    }
}

/// Parse reference-files
fn parse_reference_files(
    reader: &mut Reader<&[u8]>,
) -> Result<Vec<ReferenceFile>, XsdValidationError> {
    parse_reference_files_events(reader, Vec::new())
}

fn parse_reference_files_events(
    reader: &mut Reader<&[u8]>,
    files: Vec<ReferenceFile>,
) -> Result<Vec<ReferenceFile>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e) | Event::Empty(e)) if e.name().as_ref() == b"file" => {
            let attrs = get_attributes(&e);
            let path = attrs
                .get("path")
                .cloned()
                .ok_or_else(|| XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "reference-files/file".to_string(),
                    expected: "path attribute".to_string(),
                    found: "no path attribute".to_string(),
                    suggestion: "Add path=\"...\" to the file element".to_string(),
                    example: None,
                })?;

            let purpose = attrs
                .get("purpose")
                .cloned()
                .ok_or_else(|| XsdValidationError {
                    error_type: XsdErrorType::MissingRequiredElement,
                    element_path: "reference-files/file".to_string(),
                    expected: "purpose attribute".to_string(),
                    found: "no purpose attribute".to_string(),
                    suggestion: "Add purpose=\"...\" to the file element".to_string(),
                    example: None,
                })?;

            parse_reference_files_events(
                reader,
                files
                    .into_iter()
                    .chain(std::iter::once(ReferenceFile { path, purpose }))
                    .collect(),
            )
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"reference-files" => Ok(files),
        Ok(Event::Eof) | Err(_) => Ok(files),
        Ok(_) => parse_reference_files_events(reader, files),
    }
}
