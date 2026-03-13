// Step parsing functions (parse_steps, parse_single_step, parse_file_element, parse_target_files, parse_critical_files)

struct ParsedStep {
    step: Step,
    explicit_number: Option<u32>,
    parse_order: u32,
    dependency_targets: Vec<Option<u32>>,
}

/// Parse the ralph-implementation-steps section.
///
/// After collecting all steps, auto-assigns sequential numbers to any steps that
/// were parsed with sentinel number 0 (i.e., the number attribute was missing).
/// Explicit step numbers are reserved first, then unnumbered steps receive the
/// next unused positive numbers in document order.
fn parse_steps(reader: &mut Reader<&[u8]>) -> Result<Vec<Step>, XsdValidationError> {
    let mut steps = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) if e.name().as_ref() == b"step" => {
                let attrs = get_attributes(&e);
                let parse_order =
                    u32::try_from(steps.len() + 1).map_err(|_| XsdValidationError {
                        error_type: XsdErrorType::InvalidContent,
                        element_path: "ralph-implementation-steps".to_string(),
                        expected: "step count that fits within u32".to_string(),
                        found: format!("{} steps", steps.len() + 1),
                        suggestion: "Reduce the number of implementation steps in the plan."
                            .to_string(),
                        example: None,
                    })?;
                let step = parse_single_step(reader, &attrs)?;
                let explicit_number = (step.number != 0).then_some(step.number);
                let dependency_targets = step
                    .depends_on
                    .iter()
                    .copied()
                    .map(|dependency_number| {
                        resolve_dependency_parse_order(dependency_number, &steps)
                    })
                    .collect();
                let step = ParsedStep {
                    step,
                    explicit_number,
                    parse_order,
                    dependency_targets,
                };
                steps.push(step);
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"ralph-implementation-steps" => break,
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-implementation-steps".to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    if steps.is_empty() {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-implementation-steps".to_string(),
            expected: "at least one <step> element".to_string(),
            found: "no steps".to_string(),
            suggestion: "Add <step number=\"1\">...</step>".to_string(),
            example: None,
        });
    }

    let mut used_numbers: std::collections::HashSet<u32> = steps
        .iter()
        .filter_map(|step| step.explicit_number)
        .collect();
    let mut next_auto = 1u32;
    for step in &mut steps {
        if step.step.number == 0 {
            while used_numbers.contains(&next_auto) {
                next_auto += 1;
            }
            step.step.number = next_auto;
            used_numbers.insert(next_auto);
            next_auto += 1;
        }
    }

    let final_numbers_by_parse_order: std::collections::HashMap<u32, u32> = steps
        .iter()
        .map(|step| (step.parse_order, step.step.number))
        .collect();

    Ok(steps
        .into_iter()
        .map(|mut parsed| {
            parsed.step.depends_on = parsed
                .dependency_targets
                .into_iter()
                .zip(parsed.step.depends_on.iter().copied())
                .map(|(target, original)| {
                    target.map_or(original, |parse_order| {
                        final_numbers_by_parse_order[&parse_order]
                    })
                })
                .collect();
            parsed.step
        })
        .collect())
}

/// Content element names that can appear directly under a step (without a `<content>` wrapper).
const BARE_CONTENT_ELEMENTS: &[&[u8]] =
    &[b"paragraph", b"code-block", b"list", b"heading", b"table"];

/// Reconstruct an XML element from a start event and its inner XML content.
fn reconstruct_element(name: &[u8], attrs_str: &str, inner: &str) -> String {
    format!(
        "<{name_str}{attrs_str}>{inner}</{name_str}>",
        name_str = String::from_utf8_lossy(name),
        attrs_str = attrs_str,
        inner = inner,
    )
}

/// Extract attribute string from a quick-xml `BytesStart` for re-serialization.
fn attrs_to_string(e: &quick_xml::events::BytesStart<'_>) -> String {
    let mut result = String::new();
    for attr in e.attributes().flatten() {
        result.push(' ');
        result.push_str(&String::from_utf8_lossy(attr.key.as_ref()));
        result.push_str("=\"");
        result.push_str(&String::from_utf8_lossy(&attr.value));
        result.push('"');
    }
    result
}

/// Parse a single step element.
///
/// Tolerant behavior:
/// - Missing `number` attribute: uses sentinel 0 so caller can auto-assign
/// - Bare `<file>` elements (without `<target-files>` wrapper): collected as target files
/// - Bare content elements (paragraph, code-block, list, heading, table) without `<content>` wrapper
fn parse_single_step(
    reader: &mut Reader<&[u8]>,
    attrs: &HashMap<String, String>,
) -> Result<Step, XsdValidationError> {
    // Tolerant: if number attribute is missing, use 0 as sentinel for auto-assignment by caller.
    let number: u32 = if let Some(num_str) = attrs.get("number") {
        let parsed_number = num_str.parse().map_err(|_| XsdValidationError {
            error_type: XsdErrorType::InvalidContent,
            element_path: "step/@number".to_string(),
            expected: "positive integer".to_string(),
            found: num_str.clone(),
            suggestion: "Use a positive integer for step number".to_string(),
            example: None,
        })?;

        if parsed_number == 0 {
            return Err(XsdValidationError {
                error_type: XsdErrorType::InvalidContent,
                element_path: "step/@number".to_string(),
                expected: "positive integer greater than 0".to_string(),
                found: num_str.clone(),
                suggestion:
                    "Omit the number attribute to auto-assign it, or use a value of 1 or greater"
                        .to_string(),
                example: None,
            });
        }

        parsed_number
    } else {
        // Sentinel 0 means "auto-assign" — parse_steps will renumber these.
        0
    };

    let mut kind = attrs
        .get("type")
        .and_then(|s| StepType::from_str(s))
        .unwrap_or_default();

    let priority = attrs.get("priority").and_then(|s| Priority::from_str(s));

    let mut title = None;
    let mut target_files = Vec::new();
    let mut location = None;
    let mut rationale = None;
    let mut content_fragments = Vec::new();
    let mut depends_on = Vec::new();
    // Accumulator for bare content elements (no <content> wrapper)
    let mut bare_content_xml = String::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => match e.name().as_ref() {
                b"title" => {
                    title = Some(read_text_until_end(reader, b"title")?);
                }
                b"target-files" => {
                    let mut wrapped = parse_target_files(reader)?;
                    target_files.append(&mut wrapped);
                }
                b"location" => {
                    location = Some(read_text_until_end(reader, b"location")?);
                }
                b"rationale" => {
                    rationale = Some(read_text_until_end(reader, b"rationale")?);
                }
                b"content" => {
                    if !bare_content_xml.is_empty() {
                        content_fragments.push(parse_rich_content(&bare_content_xml)?);
                        bare_content_xml.clear();
                    }
                    let inner = read_inner_xml(reader, b"content")?;
                    content_fragments.push(parse_rich_content(&inner)?);
                }
                b"depends-on" => {
                    let dep_attrs = get_attributes(&e);
                    if let Some(step_num) = dep_attrs.get("step").and_then(|s| s.parse().ok()) {
                        depends_on.push(step_num);
                    }
                    let _ = skip_to_end(reader, b"depends-on");
                }
                b"file" => {
                    // Tolerant: bare <file> element without <target-files> wrapper.
                    let file_attrs = get_attributes(&e);
                    let file = parse_file_element(&file_attrs)?;
                    target_files.push(file);
                    // Skip to end of file element (it may have text content even if unlikely)
                    let _ = skip_to_end(reader, b"file");
                }
                name if BARE_CONTENT_ELEMENTS.contains(&name) => {
                    // Tolerant: bare content element without <content> wrapper.
                    let attrs_str = attrs_to_string(&e);
                    let inner = read_inner_xml(reader, name)?;
                    let element_xml = reconstruct_element(name, &attrs_str, &inner);
                    bare_content_xml.push_str(&element_xml);
                }
                _ => {
                    let _ = skip_to_end(reader, e.name().as_ref());
                }
            },
            Ok(Event::Empty(e)) => match e.name().as_ref() {
                b"depends-on" => {
                    let dep_attrs = get_attributes(&e);
                    if let Some(step_num) = dep_attrs.get("step").and_then(|s| s.parse().ok()) {
                        depends_on.push(step_num);
                    }
                }
                b"file" => {
                    // Tolerant: self-closing bare <file> element without <target-files> wrapper.
                    let file_attrs = get_attributes(&e);
                    let file = parse_file_element(&file_attrs)?;
                    target_files.push(file);
                }
                _ => {}
            },
            Ok(Event::End(e)) if e.name().as_ref() == b"step" => break,
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: format!("step[{number}]"),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    // If no explicit <content> wrapper was found but bare content elements were accumulated,
    // parse those as the step content.
    if !bare_content_xml.is_empty() {
        content_fragments.push(parse_rich_content(&bare_content_xml)?);
    }

    let title = title.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: format!("step[{number}]/title"),
        expected: "<title> element".to_string(),
        found: "no <title> found".to_string(),
        suggestion: "Add <title>Step title</title>".to_string(),
        example: None,
    })?;

    // Tolerant: file-change step without target-files is reclassified as action.
    // The step content still describes what to do; the type metadata is secondary.
    if kind == StepType::FileChange && target_files.is_empty() {
        kind = StepType::Action;
    }

    let content = (!content_fragments.is_empty())
        .then(|| merge_rich_content_fragments(content_fragments))
        .ok_or_else(|| XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: format!("step[{number}]/content"),
            expected: "<content> element".to_string(),
            found: "no <content> found".to_string(),
            suggestion: "Add <content><paragraph>...</paragraph></content>".to_string(),
            example: None,
        })?;

    Ok(Step {
        number,
        kind,
        priority,
        title,
        target_files,
        location,
        rationale,
        content,
        depends_on,
    })
}

fn resolve_dependency_parse_order(
    dependency_number: u32,
    parsed_steps: &[ParsedStep],
) -> Option<u32> {
    let mut matches = parsed_steps.iter().filter_map(|parsed_step| {
        let is_match = parsed_step.explicit_number == Some(dependency_number)
            || (parsed_step.explicit_number.is_none()
                && parsed_step.parse_order == dependency_number);
        is_match.then_some(parsed_step.parse_order)
    });

    let first = matches.next()?;
    if matches.next().is_some() {
        None
    } else {
        Some(first)
    }
}

fn merge_rich_content_fragments(fragments: Vec<RichContent>) -> RichContent {
    RichContent {
        elements: fragments
            .into_iter()
            .flat_map(|fragment| fragment.elements)
            .collect(),
    }
}

/// Helper to parse a single <file> element's attributes into a `TargetFile`
fn parse_file_element(attrs: &HashMap<String, String>) -> Result<TargetFile, XsdValidationError> {
    let path = attrs
        .get("path")
        .cloned()
        .ok_or_else(|| XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "target-files/file".to_string(),
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
            element_path: "target-files/file".to_string(),
            expected: "action attribute".to_string(),
            found: "no action attribute".to_string(),
            suggestion: "Add action=\"create|modify|delete\" to the file element".to_string(),
            example: None,
        })?;

    let action = FileAction::from_str(&action_str).ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::InvalidContent,
        element_path: "target-files/file/@action".to_string(),
        expected: "create, modify, or delete".to_string(),
        found: action_str,
        suggestion: "Use action=\"create\", action=\"modify\", or action=\"delete\"".to_string(),
        example: None,
    })?;

    Ok(TargetFile { path, action })
}

/// Parse target-files
fn parse_target_files(reader: &mut Reader<&[u8]>) -> Result<Vec<TargetFile>, XsdValidationError> {
    let mut files = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                if e.name().as_ref() == b"file" {
                    let attrs = get_attributes(&e);
                    let file = parse_file_element(&attrs)?;
                    files.push(file);
                    // Skip to </file> end tag
                    let _ = skip_to_end(reader, b"file");
                }
            }
            Ok(Event::Empty(e)) if e.name().as_ref() == b"file" => {
                let attrs = get_attributes(&e);
                let file = parse_file_element(&attrs)?;
                files.push(file);
                // No need to skip - self-closing tag has no end
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"target-files" => break,
            Ok(Event::Eof) | Err(_) => break,
            Ok(_) => {}
        }
        buf.clear();
    }

    Ok(files)
}

/// Parse the ralph-critical-files section.
///
/// Tolerant behavior: bare `<file>` elements directly under `<ralph-critical-files>`
/// (without a `<primary-files>` or `<reference-files>` wrapper) are classified by
/// their unambiguous attributes:
/// - File with `action` only → primary file
/// - File with `purpose` only → reference file
/// - File with both or neither → rejected as ambiguous
fn parse_critical_files(reader: &mut Reader<&[u8]>) -> Result<CriticalFiles, XsdValidationError> {
    let mut primary_files = Vec::new();
    let mut reference_files = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => match e.name().as_ref() {
                b"primary-files" => {
                    let mut wrapped = parse_primary_files(reader)?;
                    primary_files.append(&mut wrapped);
                }
                b"reference-files" => {
                    let mut wrapped = parse_reference_files(reader)?;
                    reference_files.append(&mut wrapped);
                }
                b"file" => {
                    // Tolerant: bare file element without wrapper.
                    let file_attrs = get_attributes(&e);
                    classify_bare_critical_file(
                        &file_attrs,
                        &mut primary_files,
                        &mut reference_files,
                    )?;
                    let _ = skip_to_end(reader, b"file");
                }
                _ => {
                    let _ = skip_to_end(reader, e.name().as_ref());
                }
            },
            Ok(Event::Empty(e)) if e.name().as_ref() == b"file" => {
                // Tolerant: self-closing bare file element without wrapper.
                let file_attrs = get_attributes(&e);
                classify_bare_critical_file(&file_attrs, &mut primary_files, &mut reference_files)?;
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"ralph-critical-files" => break,
            Ok(Event::Eof) => break,
            Ok(_) => {}
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-critical-files".to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

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

/// Classify a bare `<file>` element under `<ralph-critical-files>` into primary or reference.
///
/// Classification rules:
/// - Has `action` only → primary file
/// - Has `purpose` only → reference file
/// - Has both or neither → rejected as ambiguous
fn classify_bare_critical_file(
    attrs: &HashMap<String, String>,
    primary_files: &mut Vec<PrimaryFile>,
    reference_files: &mut Vec<ReferenceFile>,
) -> Result<(), XsdValidationError> {
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
            primary_files.push(PrimaryFile {
                path,
                action,
                estimated_changes: attrs.get("estimated-changes").cloned(),
            });
            Ok(())
        }
        (None, Some(purpose)) => {
            reference_files.push(ReferenceFile {
                path,
                purpose: purpose.clone(),
            });
            Ok(())
        }
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
    let mut files = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
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

                let action_str =
                    attrs
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

                let action =
                    FileAction::from_str(&action_str).ok_or_else(|| XsdValidationError {
                        error_type: XsdErrorType::InvalidContent,
                        element_path: "primary-files/file/@action".to_string(),
                        expected: "create, modify, or delete".to_string(),
                        found: action_str,
                        suggestion:
                            "Use action=\"create\", action=\"modify\", or action=\"delete\""
                                .to_string(),
                        example: None,
                    })?;

                files.push(PrimaryFile {
                    path,
                    action,
                    estimated_changes: attrs.get("estimated-changes").cloned(),
                });
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"primary-files" => break,
            Ok(Event::Eof) | Err(_) => break,
            Ok(_) => {}
        }
        buf.clear();
    }

    Ok(files)
}

/// Parse reference-files
fn parse_reference_files(
    reader: &mut Reader<&[u8]>,
) -> Result<Vec<ReferenceFile>, XsdValidationError> {
    let mut files = Vec::new();
    let mut buf = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
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

                files.push(ReferenceFile { path, purpose });
            }
            Ok(Event::End(e)) if e.name().as_ref() == b"reference-files" => break,
            Ok(Event::Eof) | Err(_) => break,
            Ok(_) => {}
        }
        buf.clear();
    }

    Ok(files)
}
