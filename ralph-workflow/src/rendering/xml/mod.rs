//! XML renderers for terminal output.
//!
//! This module routes XML rendering to type-specific modules.
//! Each XML output type (`DevelopmentResult`, `DevelopmentPlan`, etc.) has
//! a dedicated renderer that adds contextual headers and passes through raw content.

mod commit_message;
mod development_plan;
mod development_result;
mod fix_result;
mod helpers;
mod review_issues;

use crate::files::result_types::SkillsMcp;
use crate::reducer::ui_event::{XmlOutputContext, XmlOutputType};

/// Render skills-mcp recommendations in markdown format.
///
/// Renders structured skills and MCPs with optional reasons, or raw content if no structured data.
#[must_use]
pub fn render_skills_mcp_markdown(skills_mcp: Option<&SkillsMcp>) -> String {
    let Some(sm) = skills_mcp else {
        return String::new();
    };

    let has_structured = !sm.skills.is_empty() || !sm.mcps.is_empty();
    if !(has_structured || sm.raw_content.is_some()) {
        return String::new();
    }

    // Build skills lines using iterator pipeline
    let skills_lines: Vec<String> = sm
        .skills
        .iter()
        .map(|skill| {
            if let Some(ref reason) = skill.reason {
                format!("    - skill: {} \u{2014} {}", skill.name, reason)
            } else {
                format!("    - skill: {}", skill.name)
            }
        })
        .collect();

    // Build MCP lines using iterator pipeline
    let mcp_lines: Vec<String> = sm
        .mcps
        .iter()
        .map(|mcp| {
            if let Some(ref reason) = mcp.reason {
                format!("    - mcp: {} \u{2014} {}", mcp.name, reason)
            } else {
                format!("    - mcp: {}", mcp.name)
            }
        })
        .collect();

    // Build raw content line if applicable
    let raw_line: Option<String> = sm.raw_content.as_ref().and_then(|raw| {
        let trimmed: &str = raw.trim();
        if trimmed.is_empty() || has_structured {
            None
        } else {
            Some(format!("    - {}", trimmed))
        }
    });

    // Combine all parts using chain and collect
    std::iter::once("  - Skills & MCP:".to_string())
        .chain(skills_lines)
        .chain(mcp_lines)
        .chain(raw_line)
        .collect::<Vec<_>>()
        .join("\n")
}

/// Render content based on its XML output type.
///
/// Returns a formatted string for terminal display with contextual headers.
#[must_use]
pub fn render_xml(
    xml_type: &XmlOutputType,
    content: &str,
    output_context: &Option<XmlOutputContext>,
) -> String {
    match xml_type {
        XmlOutputType::DevelopmentResult => {
            development_result::render(content, output_context.as_ref())
        }
        XmlOutputType::DevelopmentPlan => development_plan::render(content),
        XmlOutputType::ReviewIssues => review_issues::render(content, output_context.as_ref()),
        XmlOutputType::FixResult => fix_result::render(content, output_context.as_ref()),
        XmlOutputType::CommitMessage => commit_message::render(content),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_xml_routes_to_development_result() {
        let content = "some development result content";
        let output = render_xml(&XmlOutputType::DevelopmentResult, content, &None);
        assert!(
            output.contains("some development result content"),
            "Should route to development result renderer"
        );
    }

    #[test]
    fn test_render_xml_routes_to_review_issues() {
        let content = "some review issues content";
        let output = render_xml(&XmlOutputType::ReviewIssues, content, &None);
        assert!(
            output.contains("Review Results"),
            "Should route to issues renderer with header"
        );
        assert!(
            output.contains("some review issues content"),
            "Should include raw content"
        );
    }

    #[test]
    fn test_render_xml_routes_to_commit_message() {
        let content = r"<ralph-commit>
<ralph-subject>feat: add feature</ralph-subject>
</ralph-commit>";

        let output = render_xml(&XmlOutputType::CommitMessage, content, &None);
        assert!(
            output.contains("feat: add feature"),
            "Should route to commit renderer"
        );
    }
}
