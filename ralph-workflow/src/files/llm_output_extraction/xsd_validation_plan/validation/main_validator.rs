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
    "ralph-implementation-steps",
    "ralph-critical-files",
    "ralph-risks-mitigations",
    "ralph-verification-strategy",
];

/// Validate plan XML content against the structured XSD schema.
///
/// This validates that the XML content conforms to the expected
/// structured plan format with rich content elements.
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
    use crate::files::llm_output_extraction::xml_helpers::{
        check_for_illegal_xml_characters, parse_skills_mcp,
    };

    let content = xml_content.trim();

    // Check for illegal XML characters BEFORE parsing
    check_for_illegal_xml_characters(content)?;

    let mut reader = Reader::from_str(content);
    reader.config_mut().trim_text(true);

    let mut buf = Vec::new();
    let mut summary = None;
    let mut steps = None;
    let mut critical_files = None;
    let mut risks_mitigations = None;
    let mut verification_strategy = None;
    let mut skills_mcp = None;
    let mut found_root = false;

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => match e.name().as_ref() {
                b"ralph-plan" => {
                    found_root = true;
                }
                b"ralph-summary" if found_root => {
                    summary = Some(parse_summary(&mut reader, b"ralph-summary")?);
                }
                b"skills-mcp" if found_root => {
                    skills_mcp = Some(parse_skills_mcp(&mut reader));
                }
                b"skills-mcp" if found_root => {
                    skills_mcp = Some(parse_skills_mcp(&mut reader)?);
                }
                b"skills-mcp" if found_root => {
                    skills_mcp = Some(parse_skills_mcp(&mut reader)?);
                }
                b"skills-mcp" if found_root => {
                    skills_mcp = Some(parse_skills_mcp(&mut reader)?);
                }
                b"skills-mcp" if found_root => {
                    skills_mcp = Some(parse_skills_mcp(&mut reader)?);
                }
                b"ralph-implementation-steps" if found_root => {
                    steps = Some(parse_steps(&mut reader, b"ralph-implementation-steps")?);
                }
                b"ralph-critical-files" if found_root => {
                    critical_files = Some(parse_critical_files(&mut reader, b"ralph-critical-files")?);
                }
                b"ralph-risks-mitigations" if found_root => {
                    risks_mitigations = Some(parse_risks_mitigations(&mut reader, b"ralph-risks-mitigations")?);
                }
                b"ralph-verification-strategy" if found_root => {
                    verification_strategy = Some(parse_verification_strategy(&mut reader, b"ralph-verification-strategy")?);
                }
                _ if found_root => {
                    // Tolerant: try fuzzy tag matching before skipping.
                    // If the tag is a known tag with minor typo, route to correct handler.
                    let element_name = e.name();
                    let tag_name = String::from_utf8_lossy(element_name.as_ref());
                    if let Some(canonical) = normalize_tag_name(&tag_name, KNOWN_PLAN_TAGS) {
                        // Re-parse with the canonical tag name
                        match canonical {
                            "ralph-summary" => {
                                summary = Some(parse_summary(&mut reader, e.name().as_ref())?);
                            }
                            "skills-mcp" => {
                                skills_mcp = Some(parse_skills_mcp(&mut reader));
                            }
                            "ralph-implementation-steps" => {
                                steps = Some(parse_steps(&mut reader, e.name().as_ref())?);
                            }
                            "ralph-critical-files" => {
                                critical_files = Some(parse_critical_files(&mut reader, e.name().as_ref())?);
                            }
                            "ralph-risks-mitigations" => {
                                risks_mitigations = Some(parse_risks_mitigations(&mut reader, e.name().as_ref())?);
                            }
                            "ralph-verification-strategy" => {
                                verification_strategy = Some(parse_verification_strategy(&mut reader, e.name().as_ref())?);
                            }
                            _ => {
                                // Should not happen - canonical tags are from our known list
                                let _ = skip_to_end(&mut reader, e.name().as_ref());
                            }
                        }
                    } else {
                        // Skip unknown elements
                        let _ = skip_to_end(&mut reader, e.name().as_ref());
                    }
                }
                _ => {
                    // Skip unknown elements before root is found
                    let _ = skip_to_end(&mut reader, e.name().as_ref());
                }
            },
            Ok(Event::Empty(e)) => match e.name().as_ref() {
                b"skills-mcp" if found_root => {
                    // Self-closing <skills-mcp/> - empty skills-mcp
                    use crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp;
                    skills_mcp = Some(SkillsMcp {
                        skills: Vec::new(),
                        mcps: Vec::new(),
                        raw_content: None,
                    });
                }
                _ => {
                    // Skip unknown empty elements
                }
            },
            Ok(Event::End(e)) if e.name().as_ref() == b"ralph-plan" => break,
            Ok(Event::Eof) => break,
            Ok(Event::Text(_) | _) => {} // Tolerant: skip stray text and other events
            Err(e) => {
                return Err(XsdValidationError {
                    error_type: XsdErrorType::MalformedXml,
                    element_path: "ralph-plan".to_string(),
                    expected: "valid XML".to_string(),
                    found: format!("parse error: {e}"),
                    suggestion: "Check XML syntax".to_string(),
                    example: None,
                });
            }
        }
        buf.clear();
    }

    if !found_root {
        return Err(XsdValidationError {
            error_type: XsdErrorType::MissingRequiredElement,
            element_path: "ralph-plan".to_string(),
            expected: "<ralph-plan> as root element".to_string(),
            found: "no <ralph-plan> found".to_string(),
            suggestion: "Wrap your plan in <ralph-plan> tags".to_string(),
            example: None,
        });
    }

    let summary = summary.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-summary".to_string(),
        expected: "<ralph-summary> element".to_string(),
        found: "no <ralph-summary> found".to_string(),
        suggestion:
            "Add <ralph-summary><context>...</context><scope-items>...</scope-items></ralph-summary>"
                .to_string(),
        example: None,
    })?;

    let steps = steps.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-implementation-steps".to_string(),
        expected: "<ralph-implementation-steps> element".to_string(),
        found: "no <ralph-implementation-steps> found".to_string(),
        suggestion: "Add <ralph-implementation-steps><step>...</step></ralph-implementation-steps>"
            .to_string(),
        example: None,
    })?;

    let critical_files = critical_files.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-critical-files".to_string(),
        expected: "<ralph-critical-files> element".to_string(),
        found: "no <ralph-critical-files> found".to_string(),
        suggestion:
            "Add <ralph-critical-files><primary-files>...</primary-files></ralph-critical-files>"
                .to_string(),
        example: None,
    })?;

    let risks_mitigations = risks_mitigations.ok_or_else(|| XsdValidationError {
        error_type: XsdErrorType::MissingRequiredElement,
        element_path: "ralph-risks-mitigations".to_string(),
        expected: "<ralph-risks-mitigations> element".to_string(),
        found: "no <ralph-risks-mitigations> found".to_string(),
        suggestion:
            "Add <ralph-risks-mitigations><risk-pair>...</risk-pair></ralph-risks-mitigations>"
                .to_string(),
        example: None,
    })?;

    let verification_strategy = verification_strategy.ok_or_else(|| XsdValidationError {
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
        skills_mcp,
    })
}
