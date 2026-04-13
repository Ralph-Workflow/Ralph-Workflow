use super::types::ParseResult;
use crate::files::result_types::{IssueEntry, IssuesElements};
use crate::phases::context::PhaseContext;
use crate::rendering::xml::render_skills_mcp_markdown;
use std::path::Path;

/// Extract review output from the JSON artifact and write issues markdown.
///
/// Returns a `ParseResult` indicating whether the output was successfully parsed,
/// explicitly declared no issues, or failed to parse (with an error description).
///
/// # Extraction
///
/// Reads from `.agent/tmp/issues.json` (MCP artifact). If absent or invalid,
/// returns `ParseFailed` so the phase can trigger a retry.
pub(super) fn extract_and_validate_review_output_xml(
    ctx: &PhaseContext<'_>,
    _log_dir: &str,
    issues_path: &Path,
) -> anyhow::Result<ParseResult> {
    let artifact = ctx
        .workspace
        .read_artifact_json("issues")
        .ok()
        .and_then(|opt| opt);

    let Some(envelope) = artifact else {
        if ctx.config.verbosity.is_debug() {
            ctx.logger
                .info("Review output missing at .agent/tmp/issues.json");
        }
        return Ok(ParseResult::ParseFailed(
            "No review output captured. Agent did not submit a JSON artifact via MCP. \
             Ensure the agent uses the submit_issues tool."
                .to_string(),
        ));
    };

    let elements = parse_issues_json(&envelope.content);
    let artifact_json = serde_json::to_string(&envelope.content).unwrap_or_default();

    let markdown = render_issues_markdown(&elements);
    ctx.workspace.write(issues_path, &markdown)?;

    if elements.no_issues_found.is_some() {
        return Ok(ParseResult::NoIssuesExplicit {
            xml_content: artifact_json,
        });
    }

    if !elements.issues.is_empty() {
        return Ok(ParseResult::IssuesFound {
            issues: elements.issue_texts(),
            xml_content: artifact_json,
        });
    }

    Ok(ParseResult::ParseFailed(
        "Issues artifact contained no issues and no no-issues-found declaration.".to_string(),
    ))
}

fn parse_issues_json(value: &serde_json::Value) -> IssuesElements {
    // {"type": "no_issues_found", "explanation": "..."} → no issues
    if value.get("type").and_then(|t| t.as_str()) == Some("no_issues_found") {
        let explanation = value
            .get("explanation")
            .and_then(|e| e.as_str())
            .unwrap_or("No issues found.")
            .to_string();
        return IssuesElements {
            issues: Vec::new(),
            no_issues_found: Some(explanation),
        };
    }

    // {"issues": [...]} or {"type": "issues_found", "issues": [...]}
    let issues = value
        .get("issues")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|item| {
                    let text = item.as_str().map(str::to_string).or_else(|| {
                        item.get("text")
                            .and_then(|t| t.as_str())
                            .map(str::to_string)
                    })?;
                    Some(IssueEntry {
                        text,
                        skills_mcp: None,
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    IssuesElements {
        issues,
        no_issues_found: None,
    }
}

fn render_issues_markdown(elements: &IssuesElements) -> String {
    if let Some(message) = &elements.no_issues_found {
        let trimmed = message.trim();
        return if trimmed.is_empty() {
            "# Issues\n\nNo issues found.\n".to_string()
        } else {
            format!("# Issues\n\n{}\n", trimmed)
        };
    }

    if elements.issues.is_empty() {
        return "# Issues\n\nNo issues found.\n".to_string();
    }

    let issues_markdown: String = elements
        .issues
        .iter()
        .filter_map(|issue| {
            let trimmed = issue.text.trim();
            if trimmed.is_empty() {
                None
            } else {
                let skills_markdown = render_skills_mcp_markdown(issue.skills_mcp.as_ref());
                Some(format!("- [ ] {}\n{}", trimmed, skills_markdown))
            }
        })
        .collect::<Vec<_>>()
        .join("");

    format!("# Issues\n\n{}", issues_markdown)
}
