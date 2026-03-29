// Main validation function (validate_plan_xml)

// Note: XsdErrorType, XsdValidationError, Event, and Reader
// are already imported in the parent module (xsd_validation_plan/mod.rs)
// and are available via `use super::*;` in validation/mod.rs

use crate::files::llm_output_extraction::xml_helpers::tolerant_parsing::normalize_tag_name;

/// Known child element tags for plan validation.
/// Used for fuzzy tag name matching (typo tolerance).
const KNOWN_PLAN_TAGS: &[&str] = &[
    "ralph-summary",
    "skills-mcp",
    "ralph-parallel-plan",
    "ralph-implementation-steps",
    "ralph-critical-files",
    "ralph-risks-mitigations",
    "ralph-verification-strategy",
];

/// All optional sections accumulated during plan parsing.
struct PlanAccum {
    summary: Option<PlanSummary>,
    steps: Option<Vec<Step>>,
    critical_files: Option<CriticalFiles>,
    risks_mitigations: Option<Vec<RiskPair>>,
    verification_strategy: Option<Vec<Verification>>,
    skills_mcp: Option<SkillsMcp>,
    parallel_plan: Option<ParallelPlanElements>,
}

impl PlanAccum {
    fn empty() -> Self {
        Self {
            summary: None,
            steps: None,
            critical_files: None,
            risks_mitigations: None,
            verification_strategy: None,
            skills_mcp: None,
            parallel_plan: None,
        }
    }
}

fn plan_malformed_xml_error(e: &quick_xml::Error) -> XsdValidationError {
    XsdValidationError {
        error_type: XsdErrorType::MalformedXml,
        element_path: "ralph-plan".to_string(),
        expected: "valid XML".to_string(),
        found: format!("parse error: {e}"),
        suggestion: "Check XML syntax".to_string(),
        example: None,
    }
}

/// Scan events until `<ralph-plan>` is found; return `Ok(false)` on EOF.
fn find_ralph_plan_root(reader: &mut Reader<&[u8]>) -> Result<bool, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"ralph-plan" => Ok(true),
        Ok(Event::Eof) => Ok(false),
        Ok(_) => find_ralph_plan_root(reader),
        Err(e) => Err(plan_malformed_xml_error(&e)),
    }
}

/// Recursively parse `<ralph-plan>` body events, accumulating section results.
fn parse_plan_events(
    reader: &mut Reader<&[u8]>,
    acc: PlanAccum,
) -> Result<PlanAccum, XsdValidationError> {
    match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => {
            let tag = e.name().as_ref().to_vec();
            dispatch_plan_tag(reader, tag, acc)
        }
        Ok(Event::Empty(e)) if e.name().as_ref() == b"skills-mcp" => parse_plan_events(
            reader,
            PlanAccum {
                skills_mcp: Some(SkillsMcp {
                    skills: Vec::new(),
                    mcps: Vec::new(),
                    raw_content: None,
                }),
                ..acc
            },
        ),
        Ok(Event::End(e)) if e.name().as_ref() == b"ralph-plan" => Ok(acc),
        Ok(Event::Eof) => Ok(acc),
        Ok(_) => parse_plan_events(reader, acc),
        Err(e) => Err(plan_malformed_xml_error(&e)),
    }
}

/// Map a tag (exact or fuzzy) to its canonical form; `None` means skip/unknown.
fn canonical_plan_tag(tag: &[u8]) -> Option<&'static str> {
    match tag {
        b"ralph-summary" => Some("ralph-summary"),
        b"skills-mcp" => Some("skills-mcp"),
        b"ralph-parallel-plan" => Some("ralph-parallel-plan"),
        b"ralph-implementation-steps" => Some("ralph-implementation-steps"),
        b"ralph-critical-files" => Some("ralph-critical-files"),
        b"ralph-risks-mitigations" => Some("ralph-risks-mitigations"),
        b"ralph-verification-strategy" => Some("ralph-verification-strategy"),
        _ => normalize_tag_name(&String::from_utf8_lossy(tag), KNOWN_PLAN_TAGS),
    }
}

/// Route a `Start` tag to its section parser and continue accumulation.
fn dispatch_plan_tag(
    reader: &mut Reader<&[u8]>,
    tag: Vec<u8>,
    acc: PlanAccum,
) -> Result<PlanAccum, XsdValidationError> {
    use crate::files::llm_output_extraction::xml_helpers::parse_skills_mcp;

    match canonical_plan_tag(&tag) {
        Some("ralph-summary") => {
            let summary = parse_summary(reader, &tag)?;
            parse_plan_events(reader, PlanAccum { summary: Some(summary), ..acc })
        }
        Some("skills-mcp") => {
            let skills_mcp = parse_skills_mcp(reader);
            parse_plan_events(reader, PlanAccum { skills_mcp: Some(skills_mcp), ..acc })
        }
        Some("ralph-parallel-plan") => {
            let parallel_plan = parse_parallel_plan(reader, &tag)?;
            parse_plan_events(reader, PlanAccum { parallel_plan: Some(parallel_plan), ..acc })
        }
        Some("ralph-implementation-steps") => {
            let steps = parse_steps(reader, &tag)?;
            parse_plan_events(reader, PlanAccum { steps: Some(steps), ..acc })
        }
        Some("ralph-critical-files") => {
            let critical_files = parse_critical_files(reader, &tag)?;
            parse_plan_events(reader, PlanAccum { critical_files: Some(critical_files), ..acc })
        }
        Some("ralph-risks-mitigations") => {
            let risks_mitigations = parse_risks_mitigations(reader, &tag)?;
            parse_plan_events(reader, PlanAccum { risks_mitigations: Some(risks_mitigations), ..acc })
        }
        Some("ralph-verification-strategy") => {
            let verification_strategy = parse_verification_strategy(reader, &tag)?;
            parse_plan_events(reader, PlanAccum { verification_strategy: Some(verification_strategy), ..acc })
        }
        _ => {
            let _ = skip_to_end(reader, &tag);
            parse_plan_events(reader, acc)
        }
    }
}

/// Parse a `<ralph-parallel-plan>` element and its work units.
///
/// This handles the optional parallel plan section for Phase 4 parallel execution.
fn parse_parallel_plan(
    reader: &mut Reader<&[u8]>,
    _parent_tag: &[u8],
) -> Result<ParallelPlanElements, XsdValidationError> {
    let work_units = std::iter::from_fn(|| match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"work-unit" => {
            Some(Some(parse_work_unit(reader, &e)))
        }
        Ok(Event::End(_)) | Ok(Event::Eof) => None,
        Ok(_) => Some(None),
        Err(e) => Some(Some(Err(plan_malformed_xml_error(&e)))),
    })
    .flatten()
    .collect::<Result<Vec<_>, _>>()?;
    Ok(ParallelPlanElements { work_units })
}

/// Parse a single `<work-unit>` element.
fn parse_work_unit(
    reader: &mut Reader<&[u8]>,
    start_event: &BytesStart,
) -> Result<WorkUnitElements, XsdValidationError> {
    // Extract unit_id from the work-unit element's id attribute
    let unit_id = start_event
        .attributes()
        .flatten()
        .find(|attr| attr.key.as_ref() == b"id")
        .map(|attr| String::from_utf8_lossy(&attr.value).to_string())
        .unwrap_or_default();

    enum WuField {
        Description(String),
        EditArea(EditAreaElements),
        Dependencies(Vec<String>),
    }

    let fields = std::iter::from_fn(|| match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => {
            let tag = e.name().as_ref().to_vec();
            match tag.as_slice() {
                b"description" => {
                    let desc = match reader.read_event_into(&mut Vec::new()) {
                        Ok(Event::Text(t)) => t.unescape().unwrap_or_default().to_string(),
                        _ => String::new(),
                    };
                    Some(Some(Ok(WuField::Description(desc))))
                }
                b"edit-area" => Some(Some(
                    parse_edit_area(reader, &tag).map(WuField::EditArea),
                )),
                b"dependencies" => Some(Some(
                    parse_dependencies(reader).map(WuField::Dependencies),
                )),
                other => {
                    let _ = skip_to_end(reader, other);
                    Some(None)
                }
            }
        }
        Ok(Event::Empty(e)) if e.name().as_ref() == b"edit-area" => {
            Some(Some(Ok(WuField::EditArea(parse_edit_area_empty(&e)))))
        }
        Ok(Event::Empty(e)) if e.name().as_ref() == b"dependencies" => {
            let _ = e;
            Some(None)
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"work-unit" => None,
        Ok(Event::Eof) => None,
        Ok(_) => Some(None),
        Err(e) => Some(Some(Err(plan_malformed_xml_error(&e)))),
    })
    .flatten()
    .collect::<Result<Vec<_>, _>>()?;

    let (description, edit_area, dependencies) = fields.into_iter().fold(
        (String::new(), None::<EditAreaElements>, Vec::<String>::new()),
        |(desc, ea, deps), field| match field {
            WuField::Description(d) => (d, ea, deps),
            WuField::EditArea(e) => (desc, Some(e), deps),
            WuField::Dependencies(d) => (desc, ea, d),
        },
    );

    Ok(WorkUnitElements {
        unit_id,
        description,
        edit_area: edit_area.unwrap_or(EditAreaElements {
            paths: Vec::new(),
            directories: Vec::new(),
        }),
        dependencies,
    })
}

/// Parse an `<edit-area>` element.
fn parse_edit_area(
    reader: &mut Reader<&[u8]>,
    _tag: &[u8],
) -> Result<EditAreaElements, XsdValidationError> {
    enum EaField {
        Paths(Vec<String>),
        Directories(Vec<String>),
    }

    let fields = std::iter::from_fn(|| match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) => {
            let tag = e.name().as_ref().to_vec();
            match tag.as_slice() {
                b"paths" => Some(Some(parse_path_list(reader).map(EaField::Paths))),
                b"directories" => {
                    Some(Some(parse_directory_list(reader).map(EaField::Directories)))
                }
                other => {
                    let _ = skip_to_end(reader, other);
                    Some(None)
                }
            }
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"edit-area" => None,
        Ok(Event::Eof) => None,
        Ok(_) => Some(None),
        Err(e) => Some(Some(Err(plan_malformed_xml_error(&e)))),
    })
    .flatten()
    .collect::<Result<Vec<_>, _>>()?;

    let (paths, directories) = fields.into_iter().fold(
        (Vec::<String>::new(), Vec::<String>::new()),
        |(paths, dirs), field| match field {
            EaField::Paths(p) => (p, dirs),
            EaField::Directories(d) => (paths, d),
        },
    );
    Ok(EditAreaElements { paths, directories })
}

/// Parse an empty `<edit-area/>` element.
fn parse_edit_area_empty(_e: &BytesStart) -> EditAreaElements {
    EditAreaElements {
        paths: Vec::new(),
        directories: Vec::new(),
    }
}

/// Parse a `<paths>` element containing multiple `<path>` elements.
fn parse_path_list(reader: &mut Reader<&[u8]>) -> Result<Vec<String>, XsdValidationError> {
    std::iter::from_fn(|| match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"path" => {
            Some(Some(Ok(match reader.read_event_into(&mut Vec::new()) {
                Ok(Event::Text(t)) => t.unescape().unwrap_or_default().to_string(),
                _ => String::new(),
            })))
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"paths" => None,
        Ok(Event::Eof) => None,
        Ok(_) => Some(None),
        Err(e) => Some(Some(Err(plan_malformed_xml_error(&e)))),
    })
    .flatten()
    .collect()
}

/// Parse a `<directories>` element containing multiple `<directory>` elements.
fn parse_directory_list(reader: &mut Reader<&[u8]>) -> Result<Vec<String>, XsdValidationError> {
    std::iter::from_fn(|| match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"directory" => {
            Some(Some(Ok(match reader.read_event_into(&mut Vec::new()) {
                Ok(Event::Text(t)) => t.unescape().unwrap_or_default().to_string(),
                _ => String::new(),
            })))
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"directories" => None,
        Ok(Event::Eof) => None,
        Ok(_) => Some(None),
        Err(e) => Some(Some(Err(plan_malformed_xml_error(&e)))),
    })
    .flatten()
    .collect()
}

/// Parse a `<dependencies>` element containing `<depends-on>` elements.
fn parse_dependencies(reader: &mut Reader<&[u8]>) -> Result<Vec<String>, XsdValidationError> {
    std::iter::from_fn(|| match reader.read_event_into(&mut Vec::new()) {
        Ok(Event::Start(e)) if e.name().as_ref() == b"depends-on" => {
            let unit_id = e
                .attributes()
                .flatten()
                .find(|attr| attr.key.as_ref() == b"unit-id")
                .map(|attr| String::from_utf8_lossy(&attr.value).to_string());
            // consume </depends-on>
            let _ = reader.read_event_into(&mut Vec::new());
            Some(unit_id.map(Ok))
        }
        Ok(Event::End(e)) if e.name().as_ref() == b"dependencies" => None,
        Ok(Event::Eof) => None,
        Ok(_) => Some(None),
        Err(e) => Some(Some(Err(plan_malformed_xml_error(&e)))),
    })
    .flatten()
    .collect()
}

/// Validate plan XML content against the structured XSD schema.
///
/// # Arguments
///
/// * `xml_content` - The XML content to validate
///
/// # Returns
///
/// * `Ok(PlanElements)` if the XML is valid and contains all required elements
/// * `Err(XsdValidationError)` if the XML is invalid or doesn't conform to the schema
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn validate_plan_xml(xml_content: &str) -> Result<PlanElements, XsdValidationError> {
    use crate::files::llm_output_extraction::xml_helpers::check_for_illegal_xml_characters;

    let content = xml_content.trim();
    check_for_illegal_xml_characters(content)?;

    // `let reader = &mut ...` is NOT `let mut reader = ...` — binding is immutable.
    let reader = &mut Reader::from_str(content);
    if !find_ralph_plan_root(reader)? {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-plan".to_string(),
            expected: "<ralph-plan> as root element".to_string(),
            found: "no <ralph-plan> found".to_string(),
            suggestion: "Wrap your plan in <ralph-plan> tags".to_string(),
            example: None,
        });
    }

    let acc = parse_plan_events(reader, PlanAccum::empty())?;

    let summary = acc.summary.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-summary".to_string(),
        expected: "<ralph-summary> element".to_string(),
        found: "no <ralph-summary> found".to_string(),
        suggestion:
            "Add <ralph-summary><context>...</context><scope-items>...</scope-items></ralph-summary>"
                .to_string(),
        example: None,
    })?;

    let steps = acc.steps.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-implementation-steps".to_string(),
        expected: "<ralph-implementation-steps> element".to_string(),
        found: "no <ralph-implementation-steps> found".to_string(),
        suggestion: "Add <ralph-implementation-steps><step>...</step></ralph-implementation-steps>"
            .to_string(),
        example: None,
    })?;

    let critical_files = acc.critical_files.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-critical-files".to_string(),
        expected: "<ralph-critical-files> element".to_string(),
        found: "no <ralph-critical-files> found".to_string(),
        suggestion:
            "Add <ralph-critical-files><primary-files>...</primary-files></ralph-critical-files>"
                .to_string(),
        example: None,
    })?;

    let risks_mitigations = acc.risks_mitigations.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-risks-mitigations".to_string(),
        expected: "<ralph-risks-mitigations> element".to_string(),
        found: "no <ralph-risks-mitigations> found".to_string(),
        suggestion:
            "Add <ralph-risks-mitigations><risk-pair>...</risk-pair></ralph-risks-mitigations>"
                .to_string(),
        example: None,
    })?;

    let verification_strategy = acc.verification_strategy.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-verification-strategy".to_string(),
        expected: "<ralph-verification-strategy> element".to_string(),
        found: "no <ralph-verification-strategy> found".to_string(),
        suggestion: "Add <ralph-verification-strategy><verification>...</verification></ralph-verification-strategy>".to_string(),
        example: None,
    })?;

    Ok(PlanElements {
        summary,
        steps,
        critical_files,
        risks_mitigations,
        verification_strategy,
        skills_mcp: acc.skills_mcp,
        parallel_plan: acc.parallel_plan,
    })
}
