//! JSON artifact ingestion for boundary modules.
//!
//! Provides conversion from `ArtifactEnvelope` JSON content to domain types
//! (`PlanElements`, `DevelopmentResultElements`). This enables JSON-first
//! artifact reading with XML fallback during the XSD-to-MCP migration.

use crate::files::llm_output_extraction::xsd_validation_development_result::DevelopmentResultElements;
use crate::files::llm_output_extraction::xsd_validation_plan::{
    ContentElement, CriticalFiles, EditAreaElements, FileAction, InlineElement, Paragraph,
    ParallelPlanElements, PlanElements, PlanSummary, PrimaryFile, Priority, ReferenceFile,
    RichContent, RiskPair, ScopeItem, Severity, Step, StepType, TargetFile, Verification,
    WorkUnitElements,
};
use crate::workspace::ArtifactEnvelope;

const FIX_RESULT_CANONICAL_STATUSES: [&str; 3] =
    ["all_issues_addressed", "issues_remain", "no_issues_found"];

/// Error returned when JSON artifact content cannot be converted to a domain type.
#[derive(Debug)]
pub(crate) struct JsonConversionError {
    pub message: String,
}

impl std::fmt::Display for JsonConversionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "JSON artifact conversion error: {}", self.message)
    }
}

fn missing_field(field: &str) -> JsonConversionError {
    JsonConversionError {
        message: format!("missing '{field}' field"),
    }
}

fn missing_or_non_string(field: &str) -> JsonConversionError {
    JsonConversionError {
        message: format!("missing or non-string '{field}' field"),
    }
}

fn get_required_value<'a>(
    v: &'a serde_json::Value,
    field: &str,
) -> Result<&'a serde_json::Value, JsonConversionError> {
    v.get(field).ok_or_else(|| missing_field(field))
}

/// Convert an `ArtifactEnvelope` with `artifact_type == "plan"` to `PlanElements`.
pub(crate) fn plan_elements_from_envelope(
    envelope: &ArtifactEnvelope,
) -> Result<PlanElements, JsonConversionError> {
    let v = &envelope.content;
    let summary = parse_plan_summary(get_required_value(v, "summary")?)?;
    let steps = parse_steps(get_required_value(v, "steps")?)?;
    let critical_files = parse_critical_files(get_required_value(v, "critical_files")?)?;
    let risks_mitigations = parse_risks_mitigations(get_required_value(v, "risks_mitigations")?)?;
    let verification_strategy =
        parse_verification_strategy(get_required_value(v, "verification_strategy")?)?;
    let skills_mcp = v.get("skills_mcp").map(parse_skills_mcp).transpose()?;
    let parallel_plan = v
        .get("parallel_plan")
        .map(parse_parallel_plan)
        .transpose()?;
    Ok(PlanElements {
        summary,
        steps,
        critical_files,
        risks_mitigations,
        verification_strategy,
        skills_mcp,
        parallel_plan,
    })
}

/// Convert an `ArtifactEnvelope` with `artifact_type == "development_result"` to
/// `DevelopmentResultElements`.
fn get_string_field(v: &serde_json::Value, field: &str) -> Result<String, JsonConversionError> {
    v.get(field)
        .and_then(|s| s.as_str())
        .ok_or_else(|| missing_or_non_string(field))
        .map(String::from)
}

pub(crate) fn development_result_from_envelope(
    envelope: &ArtifactEnvelope,
) -> Result<DevelopmentResultElements, JsonConversionError> {
    let v = &envelope.content;
    let status = get_string_field(v, "status")?;
    let summary = get_string_field(v, "summary")?;
    let files_changed = v
        .get("files_changed")
        .and_then(|s| s.as_str())
        .map(String::from);
    let files_changed_present = v.get("files_changed").is_some();
    let next_steps = v
        .get("next_steps")
        .and_then(|s| s.as_str())
        .map(String::from);
    let next_steps_present = v.get("next_steps").is_some();
    let skills_mcp = v.get("skills_mcp").map(parse_skills_mcp).transpose()?;
    Ok(DevelopmentResultElements {
        status,
        summary,
        skills_mcp,
        files_changed,
        files_changed_present,
        next_steps,
        next_steps_present,
    })
}

// ---------------------------------------------------------------------------
// Internal parsing helpers
// ---------------------------------------------------------------------------

fn parse_plan_summary(v: &serde_json::Value) -> Result<PlanSummary, JsonConversionError> {
    let context = v
        .get("context")
        .and_then(|s| s.as_str())
        .ok_or_else(|| JsonConversionError {
            message: "summary missing 'context'".to_string(),
        })?
        .to_string();

    let scope_items = v
        .get("scope_items")
        .and_then(|a| a.as_array())
        .ok_or_else(|| JsonConversionError {
            message: "summary missing 'scope_items' array".to_string(),
        })?
        .iter()
        .map(|item| {
            let description = item
                .get("text")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .to_string();
            let count = item.get("count").and_then(|s| s.as_str()).map(String::from);
            let category = item
                .get("category")
                .and_then(|s| s.as_str())
                .map(String::from);
            ScopeItem {
                description,
                count,
                category,
            }
        })
        .collect();

    Ok(PlanSummary {
        context,
        scope_items,
    })
}

fn parse_steps(v: &serde_json::Value) -> Result<Vec<Step>, JsonConversionError> {
    let arr = v.as_array().ok_or_else(|| JsonConversionError {
        message: "'steps' is not an array".to_string(),
    })?;

    arr.iter()
        .map(|item| {
            let number = item.get("number").and_then(|n| n.as_u64()).unwrap_or(0) as u32;

            let title = item
                .get("title")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .to_string();

            let kind = item
                .get("step_type")
                .and_then(|s| s.as_str())
                .and_then(|s| match s {
                    "file_change" => Some(StepType::FileChange),
                    "action" => Some(StepType::Action),
                    "research" => Some(StepType::Research),
                    _ => None,
                })
                .unwrap_or_default();

            let priority = item
                .get("priority")
                .and_then(|s| s.as_str())
                .and_then(|s| match s {
                    "critical" => Some(Priority::Critical),
                    "high" => Some(Priority::High),
                    "medium" => Some(Priority::Medium),
                    "low" => Some(Priority::Low),
                    _ => None,
                });

            let target_files = item
                .get("targets")
                .and_then(|a| a.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|t| {
                            let path = t.get("path")?.as_str()?.to_string();
                            let action = t.get("action")?.as_str().and_then(parse_file_action)?;
                            Some(TargetFile { path, action })
                        })
                        .collect()
                })
                .unwrap_or_default();

            let location = item
                .get("location")
                .and_then(|s| s.as_str())
                .map(String::from);
            let rationale = item
                .get("rationale")
                .and_then(|s| s.as_str())
                .map(String::from);

            let content_text = item
                .get("content")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .to_string();

            let content = RichContent {
                elements: vec![ContentElement::Paragraph(Paragraph {
                    content: vec![InlineElement::Text(content_text)],
                })],
            };

            let depends_on = item
                .get("depends_on")
                .and_then(|a| a.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|n| n.as_u64().map(|v| v as u32))
                        .collect()
                })
                .unwrap_or_default();

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
        })
        .collect()
}

fn parse_file_action(s: &str) -> Option<FileAction> {
    match s {
        "create" => Some(FileAction::Create),
        "modify" => Some(FileAction::Modify),
        "delete" => Some(FileAction::Delete),
        _ => None,
    }
}

fn parse_critical_files(v: &serde_json::Value) -> Result<CriticalFiles, JsonConversionError> {
    let primary_files = v
        .get("primary_files")
        .and_then(|a| a.as_array())
        .ok_or_else(|| JsonConversionError {
            message: "critical_files missing 'primary_files' array".to_string(),
        })?
        .iter()
        .filter_map(|item| {
            let path = item.get("path")?.as_str()?.to_string();
            let action = item.get("action")?.as_str().and_then(parse_file_action)?;
            let estimated_changes = item
                .get("estimated_changes")
                .and_then(|s| s.as_str())
                .map(String::from);
            Some(PrimaryFile {
                path,
                action,
                estimated_changes,
            })
        })
        .collect();

    let reference_files = v
        .get("reference_files")
        .and_then(|a| a.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|item| {
                    let path = item.get("path")?.as_str()?.to_string();
                    let purpose = item.get("purpose")?.as_str()?.to_string();
                    Some(ReferenceFile { path, purpose })
                })
                .collect()
        })
        .unwrap_or_default();

    Ok(CriticalFiles {
        primary_files,
        reference_files,
    })
}

fn parse_risks_mitigations(v: &serde_json::Value) -> Result<Vec<RiskPair>, JsonConversionError> {
    let arr = v.as_array().ok_or_else(|| JsonConversionError {
        message: "'risks_mitigations' is not an array".to_string(),
    })?;

    Ok(arr
        .iter()
        .filter_map(|item| {
            let risk = item.get("risk")?.as_str()?.to_string();
            let mitigation = item.get("mitigation")?.as_str()?.to_string();
            let severity = item
                .get("severity")
                .and_then(|s| s.as_str())
                .and_then(|s| match s {
                    "low" => Some(Severity::Low),
                    "medium" => Some(Severity::Medium),
                    "high" => Some(Severity::High),
                    "critical" => Some(Severity::Critical),
                    _ => None,
                });
            Some(RiskPair {
                severity,
                risk,
                mitigation,
            })
        })
        .collect())
}

fn parse_verification_strategy(
    v: &serde_json::Value,
) -> Result<Vec<Verification>, JsonConversionError> {
    let arr = v.as_array().ok_or_else(|| JsonConversionError {
        message: "'verification_strategy' is not an array".to_string(),
    })?;

    Ok(arr
        .iter()
        .filter_map(|item| {
            let method = item.get("method")?.as_str()?.to_string();
            let expected_outcome = item.get("expected_outcome")?.as_str()?.to_string();
            Some(Verification {
                method,
                expected_outcome,
            })
        })
        .collect())
}

fn parse_skills_mcp(
    v: &serde_json::Value,
) -> Result<crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp, JsonConversionError>
{
    use crate::files::llm_output_extraction::xsd_validation_plan::McpEntry;
    use crate::files::llm_output_extraction::xsd_validation_plan::SkillEntry;
    use crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp;

    let skills = v
        .get("skills")
        .and_then(|a| a.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|s| {
                    Some(SkillEntry {
                        name: s.as_str()?.to_string(),
                        reason: None,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    let mcps = v
        .get("mcps")
        .and_then(|a| a.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|s| {
                    Some(McpEntry {
                        name: s.as_str()?.to_string(),
                        reason: None,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    Ok(SkillsMcp {
        skills,
        mcps,
        raw_content: None,
    })
}

fn parse_parallel_plan(v: &serde_json::Value) -> Result<ParallelPlanElements, JsonConversionError> {
    let arr = v.as_array().ok_or_else(|| JsonConversionError {
        message: "'parallel_plan' is not an array".to_string(),
    })?;

    let work_units = arr
        .iter()
        .filter_map(|item| {
            let unit_id = item.get("id")?.as_str()?.to_string();
            let description = item.get("description")?.as_str()?.to_string();
            let edit_area = item.get("edit_area")?;
            let paths = edit_area
                .get("paths")
                .and_then(|a| a.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|s| s.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            let directories = edit_area
                .get("directories")
                .and_then(|a| a.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|s| s.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            let dependencies = item
                .get("depends_on")
                .and_then(|a| a.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|s| s.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();

            Some(WorkUnitElements {
                unit_id,
                description,
                edit_area: EditAreaElements { paths, directories },
                dependencies,
            })
        })
        .collect();

    Ok(ParallelPlanElements { work_units })
}

/// Convert an `ArtifactEnvelope` with `artifact_type == "issues"` to `IssuesElements`.
pub(crate) fn issues_elements_from_envelope(
    envelope: &ArtifactEnvelope,
) -> Result<crate::files::llm_output_extraction::IssuesElements, JsonConversionError> {
    let v = &envelope.content;

    match v.get("type").and_then(|t| t.as_str()) {
        Some("issues_found") => parse_canonical_issues_found(v),
        Some("no_issues_found") => parse_canonical_no_issues_found(v),
        Some(other) => Err(JsonConversionError {
            message: format!(
                "unsupported issues discriminator '{other}'; expected 'issues_found' or 'no_issues_found'"
            ),
        }),
        None => adapt_legacy_issues_payload(v),
    }
}

/// Convert an `ArtifactEnvelope` with `artifact_type == "fix_result"` to `FixResultElements`.
pub(crate) fn fix_result_from_envelope(
    envelope: &ArtifactEnvelope,
) -> Result<
    crate::files::llm_output_extraction::xsd_validation_fix_result::FixResultElements,
    JsonConversionError,
> {
    use crate::files::llm_output_extraction::xsd_validation_fix_result::FixResultElements;

    let v = &envelope.content;

    let status = v
        .get("status")
        .and_then(|s| s.as_str())
        .ok_or_else(|| JsonConversionError {
            message: "missing or non-string 'status' field".to_string(),
        })?;

    let canonical_status = normalize_fix_result_status(status)?;

    let summary = v.get("summary").and_then(|s| s.as_str()).map(String::from);

    Ok(FixResultElements {
        status: canonical_status,
        summary,
    })
}

fn check_canonical_issues_found_preconditions(
    v: &serde_json::Value,
) -> Result<(), JsonConversionError> {
    if v.get("no_issues_found").is_some() || v.get("explanation").is_some() {
        return Err(JsonConversionError {
            message:
                "ambiguous issues payload: canonical 'issues_found' cannot include no-issues fields"
                    .to_string(),
        });
    }
    Ok(())
}

fn parse_canonical_issues_found(
    v: &serde_json::Value,
) -> Result<crate::files::llm_output_extraction::IssuesElements, JsonConversionError> {
    use crate::files::llm_output_extraction::IssuesElements;
    check_canonical_issues_found_preconditions(v)?;
    let issues = parse_issue_entries(v.get("issues"), false)?;
    if issues.is_empty() {
        return Err(JsonConversionError {
            message: "canonical issues_found requires at least one issue".to_string(),
        });
    }
    Ok(IssuesElements {
        issues,
        no_issues_found: None,
    })
}

fn check_canonical_no_issues_found_preconditions(
    v: &serde_json::Value,
) -> Result<(), JsonConversionError> {
    if v.get("issues").is_some() || v.get("no_issues_found").is_some() {
        return Err(JsonConversionError {
            message:
                "ambiguous issues payload: canonical 'no_issues_found' cannot include issues fields"
                    .to_string(),
        });
    }
    Ok(())
}

fn parse_canonical_no_issues_found(
    v: &serde_json::Value,
) -> Result<crate::files::llm_output_extraction::IssuesElements, JsonConversionError> {
    use crate::files::llm_output_extraction::IssuesElements;
    check_canonical_no_issues_found_preconditions(v)?;
    let explanation = v
        .get("explanation")
        .and_then(|s| s.as_str())
        .ok_or_else(|| JsonConversionError {
            message: "canonical no_issues_found payload must include string field 'explanation'"
                .to_string(),
        })?
        .to_string();
    Ok(IssuesElements {
        issues: Vec::new(),
        no_issues_found: Some(explanation),
    })
}

fn adapt_legacy_issues_with_issues(
    v: &serde_json::Value,
) -> Result<crate::files::llm_output_extraction::IssuesElements, JsonConversionError> {
    use crate::files::llm_output_extraction::IssuesElements;
    let issues = parse_issue_entries(v.get("issues"), true)?;
    Ok(IssuesElements {
        issues,
        no_issues_found: None,
    })
}

fn adapt_legacy_issues_no_issues_found(
    v: &serde_json::Value,
) -> Result<crate::files::llm_output_extraction::IssuesElements, JsonConversionError> {
    use crate::files::llm_output_extraction::IssuesElements;
    let no_issues_found = v
        .get("no_issues_found")
        .and_then(|s| s.as_str())
        .ok_or_else(|| JsonConversionError {
            message: "legacy 'no_issues_found' must be a string".to_string(),
        })?
        .to_string();
    Ok(IssuesElements {
        issues: Vec::new(),
        no_issues_found: Some(no_issues_found),
    })
}

fn check_legacy_issues_ambiguity(
    has_issues: bool,
    has_no_issues: bool,
) -> Result<(), JsonConversionError> {
    if has_issues && has_no_issues {
        return Err(JsonConversionError {
            message: "ambiguous legacy issues payload: use either 'issues' or 'no_issues_found', not both"
                .to_string(),
        });
    }
    Ok(())
}

fn adapt_legacy_issues_payload(
    v: &serde_json::Value,
) -> Result<crate::files::llm_output_extraction::IssuesElements, JsonConversionError> {
    let has_issues = v.get("issues").is_some();
    let has_no_issues = v.get("no_issues_found").is_some();
    check_legacy_issues_ambiguity(has_issues, has_no_issues)?;
    if has_issues {
        return adapt_legacy_issues_with_issues(v);
    }
    if has_no_issues {
        return adapt_legacy_issues_no_issues_found(v);
    }
    Err(JsonConversionError {
        message: "must have canonical 'type' payload or legacy 'issues'/'no_issues_found'"
            .to_string(),
    })
}

fn parse_issue_entries(
    issues_value: Option<&serde_json::Value>,
    allow_legacy_skills_mcp: bool,
) -> Result<Vec<crate::files::llm_output_extraction::IssueEntry>, JsonConversionError> {
    use crate::files::llm_output_extraction::IssueEntry;

    let arr = issues_value
        .ok_or_else(|| JsonConversionError {
            message: "'issues' must be present".to_string(),
        })?
        .as_array()
        .ok_or_else(|| JsonConversionError {
            message: "'issues' must be an array".to_string(),
        })?;

    arr.iter()
        .enumerate()
        .map(|(idx, item)| {
            let text = item
                .get("text")
                .and_then(|s| s.as_str())
                .ok_or_else(|| JsonConversionError {
                    message: format!("issues[{idx}] missing required string field 'text'"),
                })?
                .to_string();

            let has_legacy_skills_mcp = item.get("skills_mcp").is_some();
            let has_canonical_skill_fields = item.get("skills").is_some() || item.get("mcps").is_some();

            if has_legacy_skills_mcp && has_canonical_skill_fields {
                return Err(JsonConversionError {
                    message: format!(
                        "issues[{idx}] mixes legacy 'skills_mcp' with canonical 'skills'/'mcps'"
                    ),
                });
            }

            let skills_mcp = if has_legacy_skills_mcp {
                if !allow_legacy_skills_mcp {
                    return Err(JsonConversionError {
                        message: format!(
                            "issues[{idx}] uses legacy 'skills_mcp' in canonical payload; use 'skills'/'mcps'"
                        ),
                    });
                }
                item.get("skills_mcp")
                    .map(parse_skills_mcp)
                    .transpose()?
            } else {
                parse_skills_mcp_from_canonical_fields(item)?
            };

            Ok(IssueEntry { text, skills_mcp })
        })
        .collect()
}

fn parse_string_field_array(
    item: &serde_json::Value,
    field: &str,
) -> Result<Vec<String>, JsonConversionError> {
    match item.get(field) {
        Some(value) => value
            .as_array()
            .ok_or_else(|| JsonConversionError {
                message: format!("'{field}' must be an array of strings"),
            })?
            .iter()
            .map(|v| {
                v.as_str()
                    .map(str::to_string)
                    .ok_or_else(|| JsonConversionError {
                        message: format!("'{field}' must contain only strings"),
                    })
            })
            .collect(),
        None => Ok(Vec::new()),
    }
}

fn parse_skills_mcp_from_canonical_fields(
    item: &serde_json::Value,
) -> Result<
    Option<crate::files::llm_output_extraction::xsd_validation_plan::SkillsMcp>,
    JsonConversionError,
> {
    use crate::files::llm_output_extraction::xsd_validation_plan::{
        McpEntry, SkillEntry, SkillsMcp,
    };
    let skills = parse_string_field_array(item, "skills")?;
    let mcps = parse_string_field_array(item, "mcps")?;
    if skills.is_empty() && mcps.is_empty() {
        return Ok(None);
    }
    Ok(Some(SkillsMcp {
        skills: skills
            .into_iter()
            .map(|name| SkillEntry { name, reason: None })
            .collect(),
        mcps: mcps
            .into_iter()
            .map(|name| McpEntry { name, reason: None })
            .collect(),
        raw_content: None,
    }))
}

fn normalize_fix_result_status(status: &str) -> Result<String, JsonConversionError> {
    match status {
        "all_issues_addressed" | "issues_remain" | "no_issues_found" => Ok(status.to_string()),
        "completed" => Ok("all_issues_addressed".to_string()),
        "partial" => Ok("issues_remain".to_string()),
        _ => Err(JsonConversionError {
            message: format!(
                "invalid fix_result status '{status}'; expected canonical status {} (legacy aliases: completed->all_issues_addressed, partial->issues_remain)",
                FIX_RESULT_CANONICAL_STATUSES.join(", ")
            ),
        }),
    }
}

#[cfg(test)]
mod tests;
