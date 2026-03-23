// Step parsing functions (parse_steps, parse_single_step, parse_file_element, parse_target_files)
// Critical files parsers (parse_critical_files and helpers) are in critical_files_parsers.rs

// Note: normalize_tag_name is imported in main_validator.rs and available in this module
// via the include! statement that combines all validation/*.rs files

/// Known sub-element tags for step parsing.
/// Used for fuzzy tag name matching (typo tolerance).
const STEP_SUB_ELEMENT_TAGS: &[&str] = &[
    "title",
    "content",
    "target-files",
    "depends-on",
    "location",
    "rationale",
    "file",
];

use std::collections::HashSet;

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
///
/// The `original_tag` parameter is used for fuzzy matching - when the opening tag was misspelled,
/// this allows the parser to accept either the canonical closing tag OR the original misspelled one.
fn parse_steps(
    reader: &mut Reader<&[u8]>,
    original_tag: &[u8],
) -> Result<Vec<Step>, XsdValidationError> {
    let canonical_tag = b"ralph-implementation-steps";
    let steps = parse_steps_events(reader, original_tag, canonical_tag, Vec::new())?;

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

    let explicit_numbers: HashSet<u32> = steps
        .iter()
        .filter_map(|step| step.explicit_number)
        .collect();
    let (renumbered_steps, _, _) = steps.into_iter().fold(
        (Vec::new(), explicit_numbers, 1u32),
        |(renumbered, used_numbers, next_candidate), parsed| {
            let ParsedStep {
                step,
                parse_order,
                dependency_targets,
                ..
            } = parsed;

            let assigned_number = if step.number == 0 {
                next_unused_number(&used_numbers, next_candidate)
            } else {
                step.number
            };

            let updated_used_numbers = used_numbers
                .into_iter()
                .chain(std::iter::once(assigned_number))
                .collect();
            let updated_step = ParsedStep {
                step: Step {
                    number: assigned_number,
                    ..step
                },
                explicit_number: Some(assigned_number),
                parse_order,
                dependency_targets,
            };
            (
                renumbered
                    .into_iter()
                    .chain(std::iter::once(updated_step))
                    .collect(),
                updated_used_numbers,
                assigned_number.saturating_add(1),
            )
        },
    );

    let final_numbers_by_parse_order: HashMap<u32, u32> = renumbered_steps
        .iter()
        .map(|step| (step.parse_order, step.step.number))
        .collect();

    Ok(renumbered_steps
        .into_iter()
        .map(|parsed| {
            let ParsedStep {
                step,
                dependency_targets,
                ..
            } = parsed;
            let Step {
                depends_on,
                number,
                kind,
                priority,
                title,
                target_files,
                location,
                rationale,
                content,
            } = step;
            let depends_on = dependency_targets
                .into_iter()
                .zip(depends_on)
                .map(|(target, original)| {
                    target.map_or(original, |parse_order| {
                        final_numbers_by_parse_order[&parse_order]
                    })
                })
                .collect();
            Step {
                number,
                kind,
                priority,
                title,
                target_files,
                location,
                rationale,
                content,
                depends_on,
            }
        })
        .collect())
}

fn parse_steps_events(
    reader: &mut Reader<&[u8]>,
    original_tag: &[u8],
    canonical_tag: &[u8],
    steps: Vec<ParsedStep>,
) -> Result<Vec<ParsedStep>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"step" => {
            let attrs = get_attributes(&e);
            let parse_order = u32::try_from(steps.len() + 1).map_err(|_| XsdValidationError {
                error_type: XsdErrorType::InvalidContent,
                element_path: "ralph-implementation-steps".to_string(),
                expected: "step count that fits within u32".to_string(),
                found: format!("{} steps", steps.len() + 1),
                suggestion: "Reduce the number of implementation steps in the plan.".to_string(),
                example: None,
            })?;
            let step = parse_single_step(reader, &attrs)?;
            let explicit_number = (step.number != 0).then_some(step.number);
            let dependency_targets = step
                .depends_on
                .iter()
                .copied()
                .map(|dependency_number| resolve_dependency_parse_order(dependency_number, &steps))
                .collect();
            let parsed = ParsedStep {
                step,
                explicit_number,
                parse_order,
                dependency_targets,
            };
            parse_steps_events(
                reader,
                original_tag,
                canonical_tag,
                steps.into_iter().chain(std::iter::once(parsed)).collect(),
            )
        }
        Ok(Event::End(e))
            if e.name().as_ref() == canonical_tag || e.name().as_ref() == original_tag =>
        {
            Ok(steps)
        }
        Ok(Event::Eof) => Ok(steps),
        Ok(_) => parse_steps_events(reader, original_tag, canonical_tag, steps),
        Err(e) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: "ralph-implementation-steps".to_string(),
            expected: "valid XML".to_string(),
            found: format!("parse error: {e}"),
            suggestion: "Check XML syntax".to_string(),
            example: None,
        }),
    }
}

fn next_unused_number(used: &HashSet<u32>, start: u32) -> u32 {
    std::iter::successors(Some(start), |value| value.checked_add(1))
        .find(|value| !used.contains(value))
        .unwrap_or(u32::MAX)
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
    e.attributes()
        .flatten()
        .map(|attr| {
            format!(
                " {name}={quote}{value}{quote}",
                name = String::from_utf8_lossy(attr.key.as_ref()),
                value = String::from_utf8_lossy(&attr.value),
                quote = '"'
            )
        })
        .collect::<Vec<_>>()
        .concat()
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

    let initial_state = SingleStepState {
        kind: attrs
            .get("type")
            .and_then(|s| StepType::from_str(s))
            .unwrap_or_default(),
        priority: attrs.get("priority").and_then(|s| Priority::from_str(s)),
        title: None,
        target_files: Vec::new(),
        location: None,
        rationale: None,
        content_fragments: Vec::new(),
        depends_on: Vec::new(),
        bare_content_elements: Vec::new(),
    };
    let parsed_state = parse_single_step_events(reader, number, initial_state)?;
    let SingleStepState {
        kind,
        priority,
        title,
        target_files,
        location,
        rationale,
        content_fragments,
        depends_on,
        ..
    } = flush_bare_content_fragments(parsed_state)?;

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
    let kind = if kind == StepType::FileChange && target_files.is_empty() {
        StepType::Action
    } else {
        kind
    };

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

#[derive(Clone)]
struct SingleStepState {
    kind: StepType,
    priority: Option<Priority>,
    title: Option<String>,
    target_files: Vec<TargetFile>,
    location: Option<String>,
    rationale: Option<String>,
    content_fragments: Vec<RichContent>,
    depends_on: Vec<u32>,
    bare_content_elements: Vec<String>,
}

fn parse_single_step_events(
    reader: &mut Reader<&[u8]>,
    number: u32,
    state: SingleStepState,
) -> Result<SingleStepState, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => {
            let updated_state = handle_single_step_start_event(reader, e, state)?;
            parse_single_step_events(reader, number, updated_state)
        }
        Ok(Event::Empty(e)) => {
            let updated_state = handle_single_step_empty_event(e, state)?;
            parse_single_step_events(reader, number, updated_state)
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"step" => Ok(state),
        Ok(Event::Eof) => Ok(state),
        Ok(_) => parse_single_step_events(reader, number, state),
        Err(e) => Err(XsdValidationError {
            error_type: XsdErrorType::MalformedXml,
            element_path: format!("step[{number}]"),
            expected: "valid XML".to_string(),
            found: format!("parse error: {e}"),
            suggestion: "Check XML syntax".to_string(),
            example: None,
        }),
    }
}

fn flush_bare_content_fragments(
    state: SingleStepState,
) -> Result<SingleStepState, XsdValidationError> {
    if state.bare_content_elements.is_empty() {
        Ok(state)
    } else {
        let bare_content_xml = state.bare_content_elements.join("");
        Ok(SingleStepState {
            content_fragments: state
                .content_fragments
                .into_iter()
                .chain(std::iter::once(parse_rich_content(&bare_content_xml)?))
                .collect(),
            bare_content_elements: Vec::new(),
            ..state
        })
    }
}

fn handle_single_step_start_event(
    reader: &mut Reader<&[u8]>,
    event: quick_xml::events::BytesStart<'_>,
    state: SingleStepState,
) -> Result<SingleStepState, XsdValidationError> {
    match event.name().as_ref() {
        b"title" => Ok(SingleStepState {
            title: Some(read_text_until_end(reader, b"title")?),
            ..state
        }),
        b"target-files" => Ok(SingleStepState {
            target_files: state
                .target_files
                .into_iter()
                .chain(parse_target_files(reader)?)
                .collect(),
            ..state
        }),
        b"location" => Ok(SingleStepState {
            location: Some(read_text_until_end(reader, b"location")?),
            ..state
        }),
        b"rationale" => Ok(SingleStepState {
            rationale: Some(read_text_until_end(reader, b"rationale")?),
            ..state
        }),
        b"content" => {
            let flushed_state = flush_bare_content_fragments(state)?;
            let inner = read_inner_xml(reader, b"content")?;
            Ok(SingleStepState {
                content_fragments: flushed_state
                    .content_fragments
                    .into_iter()
                    .chain(std::iter::once(parse_rich_content(&inner)?))
                    .collect(),
                ..flushed_state
            })
        }
        b"depends-on" => {
            let dep_attrs = get_attributes(&event);
            let parsed_dep = dep_attrs.get("step").and_then(|s| s.parse().ok());
            let _ = skip_to_end(reader, b"depends-on");
            Ok(SingleStepState {
                depends_on: state.depends_on.into_iter().chain(parsed_dep).collect(),
                ..state
            })
        }
        b"file" => {
            let file_attrs = get_attributes(&event);
            let file = parse_file_element(&file_attrs)?;
            let _ = skip_to_end(reader, b"file");
            Ok(SingleStepState {
                target_files: state
                    .target_files
                    .into_iter()
                    .chain(std::iter::once(file))
                    .collect(),
                ..state
            })
        }
        name if BARE_CONTENT_ELEMENTS.contains(&name) => {
            let attrs_str = attrs_to_string(&event);
            let inner = read_inner_xml(reader, name)?;
            let element_xml = reconstruct_element(name, &attrs_str, &inner);
            Ok(SingleStepState {
                bare_content_elements: state
                    .bare_content_elements
                    .into_iter()
                    .chain(std::iter::once(element_xml))
                    .collect(),
                ..state
            })
        }
        other => handle_single_step_fuzzy_start_event(reader, &event, other, state),
    }
}

fn handle_single_step_fuzzy_start_event(
    reader: &mut Reader<&[u8]>,
    event: &quick_xml::events::BytesStart<'_>,
    other: &[u8],
    state: SingleStepState,
) -> Result<SingleStepState, XsdValidationError> {
    let tag_name = String::from_utf8_lossy(other);
    match normalize_tag_name(&tag_name, STEP_SUB_ELEMENT_TAGS) {
        Some("title") => Ok(SingleStepState {
            title: Some(read_text_until_end_fuzzy(reader, b"title", other)?),
            ..state
        }),
        Some("content") => {
            let flushed_state = flush_bare_content_fragments(state)?;
            let inner = read_inner_xml(reader, b"content")?;
            Ok(SingleStepState {
                content_fragments: flushed_state
                    .content_fragments
                    .into_iter()
                    .chain(std::iter::once(parse_rich_content(&inner)?))
                    .collect(),
                ..flushed_state
            })
        }
        Some("target-files") => Ok(SingleStepState {
            target_files: state
                .target_files
                .into_iter()
                .chain(parse_target_files(reader)?)
                .collect(),
            ..state
        }),
        Some("location") => Ok(SingleStepState {
            location: Some(read_text_until_end_fuzzy(reader, b"location", other)?),
            ..state
        }),
        Some("rationale") => Ok(SingleStepState {
            rationale: Some(read_text_until_end_fuzzy(reader, b"rationale", other)?),
            ..state
        }),
        Some("depends-on") => {
            let dep_attrs = get_attributes(event);
            let parsed_dep = dep_attrs.get("step").and_then(|s| s.parse().ok());
            let _ = skip_to_end(reader, b"depends-on");
            Ok(SingleStepState {
                depends_on: state.depends_on.into_iter().chain(parsed_dep).collect(),
                ..state
            })
        }
        Some("file") => {
            let file_attrs = get_attributes(event);
            let file = parse_file_element(&file_attrs)?;
            let _ = skip_to_end(reader, b"file");
            Ok(SingleStepState {
                target_files: state
                    .target_files
                    .into_iter()
                    .chain(std::iter::once(file))
                    .collect(),
                ..state
            })
        }
        _ => {
            let _ = skip_to_end(reader, other);
            Ok(state)
        }
    }
}

fn handle_single_step_empty_event(
    event: quick_xml::events::BytesStart<'_>,
    state: SingleStepState,
) -> Result<SingleStepState, XsdValidationError> {
    match event.name().as_ref() {
        b"depends-on" => {
            let dep_attrs = get_attributes(&event);
            let parsed_dep = dep_attrs.get("step").and_then(|s| s.parse().ok());
            Ok(SingleStepState {
                depends_on: state.depends_on.into_iter().chain(parsed_dep).collect(),
                ..state
            })
        }
        b"file" => {
            let file_attrs = get_attributes(&event);
            let file = parse_file_element(&file_attrs)?;
            Ok(SingleStepState {
                target_files: state
                    .target_files
                    .into_iter()
                    .chain(std::iter::once(file))
                    .collect(),
                ..state
            })
        }
        other => {
            let tag_name = String::from_utf8_lossy(other);
            match normalize_tag_name(&tag_name, STEP_SUB_ELEMENT_TAGS) {
                Some("depends-on") => {
                    let dep_attrs = get_attributes(&event);
                    let parsed_dep = dep_attrs.get("step").and_then(|s| s.parse().ok());
                    Ok(SingleStepState {
                        depends_on: state.depends_on.into_iter().chain(parsed_dep).collect(),
                        ..state
                    })
                }
                Some("file") => {
                    let file_attrs = get_attributes(&event);
                    let file = parse_file_element(&file_attrs)?;
                    Ok(SingleStepState {
                        target_files: state
                            .target_files
                            .into_iter()
                            .chain(std::iter::once(file))
                            .collect(),
                        ..state
                    })
                }
                _ => Ok(state),
            }
        }
    }
}

fn resolve_dependency_parse_order(
    dependency_number: u32,
    parsed_steps: &[ParsedStep],
) -> Option<u32> {
    let matches: Vec<_> = parsed_steps
        .iter()
        .filter_map(|parsed_step| {
            let is_match = parsed_step.explicit_number == Some(dependency_number)
                || (parsed_step.explicit_number.is_none()
                    && parsed_step.parse_order == dependency_number);
            is_match.then_some(parsed_step.parse_order)
        })
        .take(2)
        .collect();

    match matches.as_slice() {
        [only] => Some(*only),
        _ => None,
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
    parse_target_files_events(reader, Vec::new())
}

fn parse_target_files_events(
    reader: &mut Reader<&[u8]>,
    files: Vec<TargetFile>,
) -> Result<Vec<TargetFile>, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"file" => {
            let attrs = get_attributes(&e);
            let file = parse_file_element(&attrs)?;
            let _ = skip_to_end(reader, b"file");
            parse_target_files_events(
                reader,
                files.into_iter().chain(std::iter::once(file)).collect(),
            )
        }
        Ok(Event::Empty(e)) if e.name().as_ref() == b"file" => {
            let attrs = get_attributes(&e);
            let file = parse_file_element(&attrs)?;
            parse_target_files_events(
                reader,
                files.into_iter().chain(std::iter::once(file)).collect(),
            )
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"target-files" => Ok(files),
        Ok(Event::Eof) | Err(_) => Ok(files),
        Ok(_) => parse_target_files_events(reader, files),
    }
}

