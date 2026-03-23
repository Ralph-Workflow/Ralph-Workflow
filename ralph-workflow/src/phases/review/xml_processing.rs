use super::types::ParseResult;
use crate::files::llm_output_extraction::xsd_validation::XsdValidationError;
use crate::files::llm_output_extraction::{
    archive_xml_file_with_workspace, try_extract_from_file_with_workspace, validate_issues_xml,
    xml_paths, IssuesElements,
};
use crate::phases::context::PhaseContext;
use crate::rendering::xml::render_skills_mcp_markdown;
use std::path::Path;

/// Extract review output using XML extraction and validate with XSD.
///
/// Returns a `ParseResult` indicating whether the output was successfully parsed,
/// explicitly declared no issues, or failed to parse (with an error description).
///
/// # Extraction Priority
///
/// 1. File-based XML at `.agent/tmp/issues.xml` (required)
///
/// Legacy log extraction and ISSUES.md fallback have been removed. Agents must
/// produce XML output via the reducer/effect path.
pub(super) fn extract_and_validate_review_output_xml(
    ctx: &PhaseContext<'_>,
    log_dir: &str,
    issues_path: &Path,
) -> anyhow::Result<ParseResult> {
    // Priority 1: Check for file-based XML at .agent/tmp/issues.xml
    // This is the preferred path for agents that write XML directly (e.g., opencode parser)
    if let Some(xml_content) =
        try_extract_from_file_with_workspace(ctx.workspace, Path::new(xml_paths::ISSUES_XML))
    {
        ctx.logger
            .info("Found XML in .agent/tmp/issues.xml (file-based mode)");
        return validate_and_process_issues_xml(ctx, &xml_content, issues_path);
    }

    if ctx.config.verbosity.is_debug() {
        ctx.logger.info(&format!(
            "Review output missing at .agent/tmp/issues.xml; expected log prefix: {log_dir}"
        ));
    }

    // Legacy JSON log extraction removed - fail with clear error
    Ok(ParseResult::ParseFailed(
        "No review output captured. Agent did not write to .agent/tmp/issues.xml. \
         Ensure the agent produces valid XML output via the configured effects."
            .to_string(),
    ))
}

/// Helper to validate XML and process the result for issues extraction.
pub(super) fn validate_and_process_issues_xml(
    ctx: &PhaseContext<'_>,
    xml_content: &str,
    issues_path: &Path,
) -> anyhow::Result<ParseResult> {
    // Validate the extracted XML against XSD
    let validated: Result<IssuesElements, XsdValidationError> = validate_issues_xml(xml_content);

    match validated {
        Ok(elements) => {
            let markdown = render_issues_markdown(&elements);
            ctx.workspace.write(issues_path, &markdown)?;
            archive_xml_file_with_workspace(ctx.workspace, Path::new(xml_paths::ISSUES_XML));

            if elements.no_issues_found.is_some() {
                return Ok(ParseResult::NoIssuesExplicit {
                    xml_content: xml_content.to_string(),
                });
            }

            if !elements.issues.is_empty() {
                return Ok(ParseResult::IssuesFound {
                    issues: elements.issue_texts(),
                    xml_content: xml_content.to_string(),
                });
            }

            Ok(ParseResult::ParseFailed(
                "XML validated but contains no issues or no-issues-found element.".to_string(),
            ))
        }
        Err(xsd_error) => {
            // Return the specific XSD error for retry
            Ok(ParseResult::ParseFailed(xsd_error.format_for_ai_retry()))
        }
    }
}

fn render_issues_markdown(elements: &IssuesElements) -> String {
    // Handle no_issues_found case first
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

    // Use iterator pipeline to build the markdown
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
