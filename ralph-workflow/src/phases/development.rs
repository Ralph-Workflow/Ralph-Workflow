//! Development phase execution.
//!
//! This module handles the development phase of the Ralph pipeline, which consists
//! of iterative planning and execution cycles. Each iteration:
//! 1. Creates a PLAN.md from PROMPT.md
//! 2. Executes the plan
//! 3. Deletes PLAN.md
//! 4. Optionally runs fast checks

use std::fmt::Write;

use crate::files::llm_output_extraction::PlanElements;

/// Format plan elements as markdown for PLAN.md.
pub(crate) fn format_plan_as_markdown(elements: &PlanElements) -> String {
    let mut result = String::new();

    // Summary section
    result.push_str("## Summary\n\n");
    result.push_str(&elements.summary.context);
    result.push_str("\n\n");

    // Scope items
    result.push_str("### Scope\n\n");
    for item in &elements.summary.scope_items {
        if let Some(ref count) = item.count {
            writeln!(result, "- **{}** {}", count, item.description).unwrap();
        } else {
            write!(result, "- {}", item.description).unwrap();
        }
        if let Some(ref category) = item.category {
            write!(result, " ({category})").unwrap();
        }
        result.push('\n');
    }
    result.push('\n');

    // Skills & MCP recommendations (if present)
    if let Some(ref sm) = elements.skills_mcp {
        let has_structured = !sm.skills.is_empty() || !sm.mcps.is_empty();
        if has_structured || sm.raw_content.is_some() {
            result.push_str("### Skills & MCP Recommendations\n\n");
            for skill in &sm.skills {
                if let Some(ref reason) = skill.reason {
                    writeln!(result, "- **Skill:** {} \u{2014} {}", skill.name, reason).unwrap();
                } else {
                    writeln!(result, "- **Skill:** {}", skill.name).unwrap();
                }
            }
            for mcp in &sm.mcps {
                if let Some(ref reason) = mcp.reason {
                    writeln!(result, "- **MCP:** {} \u{2014} {}", mcp.name, reason).unwrap();
                } else {
                    writeln!(result, "- **MCP:** {}", mcp.name).unwrap();
                }
            }
            if let Some(ref raw) = sm.raw_content {
                let trimmed = raw.trim();
                if !trimmed.is_empty() {
                    writeln!(result, "\n{trimmed}").unwrap();
                }
            }
            result.push('\n');
        }
    }

    // Implementation steps
    result.push_str("## Implementation Steps\n\n");
    for step in &elements.steps {
        // Step header
        let step_type_str = match step.kind {
            crate::files::llm_output_extraction::xsd_validation_plan::StepType::FileChange => {
                "file-change"
            }
            crate::files::llm_output_extraction::xsd_validation_plan::StepType::Action => "action",
            crate::files::llm_output_extraction::xsd_validation_plan::StepType::Research => {
                "research"
            }
        };
        let priority_str = step.priority.map_or(String::new(), |p| {
            format!(
                " [{}]",
                match p {
                    crate::files::llm_output_extraction::xsd_validation_plan::Priority::Critical =>
                        "critical",
                    crate::files::llm_output_extraction::xsd_validation_plan::Priority::High =>
                        "high",
                    crate::files::llm_output_extraction::xsd_validation_plan::Priority::Medium =>
                        "medium",
                    crate::files::llm_output_extraction::xsd_validation_plan::Priority::Low =>
                        "low",
                }
            )
        });

        write!(
            result,
            "### Step {} ({}){}:  {}\n\n",
            step.number, step_type_str, priority_str, step.title
        )
        .unwrap();

        // Target files
        if !step.target_files.is_empty() {
            result.push_str("**Target Files:**\n");
            for tf in &step.target_files {
                let action_str = match tf.action {
                    crate::files::llm_output_extraction::xsd_validation_plan::FileAction::Create => {
                        "create"
                    }
                    crate::files::llm_output_extraction::xsd_validation_plan::FileAction::Modify => {
                        "modify"
                    }
                    crate::files::llm_output_extraction::xsd_validation_plan::FileAction::Delete => {
                        "delete"
                    }
                };
                writeln!(result, "- `{}` ({})", tf.path, action_str).unwrap();
            }
            result.push('\n');
        }

        // Location
        if let Some(ref location) = step.location {
            writeln!(result, "**Location:** {location}").unwrap();
        }

        // Rationale
        if let Some(ref rationale) = step.rationale {
            writeln!(result, "**Rationale:** {rationale}\n").unwrap();
        }

        // Content
        result.push_str(&format_rich_content(&step.content));
        result.push('\n');

        // Dependencies
        if !step.depends_on.is_empty() {
            result.push_str("**Depends on:** ");
            let deps: Vec<String> = step
                .depends_on
                .iter()
                .map(|d| format!("Step {d}"))
                .collect();
            result.push_str(&deps.join(", "));
            result.push_str("\n\n");
        }
    }

    // Critical files
    result.push_str("## Critical Files\n\n");
    result.push_str("### Primary Files\n\n");
    for pf in &elements.critical_files.primary_files {
        let action_str = match pf.action {
            crate::files::llm_output_extraction::xsd_validation_plan::FileAction::Create => {
                "create"
            }
            crate::files::llm_output_extraction::xsd_validation_plan::FileAction::Modify => {
                "modify"
            }
            crate::files::llm_output_extraction::xsd_validation_plan::FileAction::Delete => {
                "delete"
            }
        };
        if let Some(ref est) = pf.estimated_changes {
            writeln!(result, "- `{}` ({}) - {}", pf.path, action_str, est).unwrap();
        } else {
            writeln!(result, "- `{}` ({})", pf.path, action_str).unwrap();
        }
    }
    result.push('\n');

    if !elements.critical_files.reference_files.is_empty() {
        result.push_str("### Reference Files\n\n");
        for rf in &elements.critical_files.reference_files {
            writeln!(result, "- `{}` - {}", rf.path, rf.purpose).unwrap();
        }
        result.push('\n');
    }

    // Risks and mitigations
    result.push_str("## Risks & Mitigations\n\n");
    for rp in &elements.risks_mitigations {
        let severity_str = rp.severity.map_or(String::new(), |s| {
            format!(
                " [{}]",
                match s {
                    crate::files::llm_output_extraction::xsd_validation_plan::Severity::Low =>
                        "low",
                    crate::files::llm_output_extraction::xsd_validation_plan::Severity::Medium =>
                        "medium",
                    crate::files::llm_output_extraction::xsd_validation_plan::Severity::High =>
                        "high",
                    crate::files::llm_output_extraction::xsd_validation_plan::Severity::Critical =>
                        "critical",
                }
            )
        });
        writeln!(result, "**Risk{}:** {}", severity_str, rp.risk).unwrap();
        writeln!(result, "**Mitigation:** {}\n\n", rp.mitigation).unwrap();
    }

    // Verification strategy
    result.push_str("## Verification Strategy\n\n");
    for (i, v) in elements.verification_strategy.iter().enumerate() {
        writeln!(result, "{}. **{}**", i + 1, v.method).unwrap();
        write!(result, "   Expected: {}\n\n", v.expected_outcome).unwrap();
    }

    result
}

/// Format rich content elements to markdown.
fn format_rich_content(
    content: &crate::files::llm_output_extraction::xsd_validation_plan::RichContent,
) -> String {
    use crate::files::llm_output_extraction::xsd_validation_plan::ContentElement;

    let mut result = String::new();

    for element in &content.elements {
        match element {
            ContentElement::Paragraph(p) => {
                result.push_str(&format_inline_content(&p.content));
                result.push_str("\n\n");
            }
            ContentElement::CodeBlock(cb) => {
                let lang = cb.language.as_deref().unwrap_or("");
                writeln!(result, "```{lang}").unwrap();
                result.push_str(&cb.content);
                if !cb.content.ends_with('\n') {
                    result.push('\n');
                }
                result.push_str("```\n\n");
            }
            ContentElement::Table(t) => {
                if let Some(ref caption) = t.caption {
                    writeln!(result, "**{caption}**").unwrap();
                }
                // Header row
                if !t.columns.is_empty() {
                    result.push_str("| ");
                    result.push_str(&t.columns.join(" | "));
                    result.push_str(" |\n");
                    result.push('|');
                    for _ in &t.columns {
                        result.push_str(" --- |");
                    }
                    result.push('\n');
                } else if let Some(first_row) = t.rows.first() {
                    // Infer column count from first row
                    result.push('|');
                    for _ in &first_row.cells {
                        result.push_str(" --- |");
                    }
                    result.push('\n');
                }
                // Data rows
                for row in &t.rows {
                    result.push_str("| ");
                    let cells: Vec<String> = row
                        .cells
                        .iter()
                        .map(|c| format_inline_content(&c.content))
                        .collect();
                    result.push_str(&cells.join(" | "));
                    result.push_str(" |\n");
                }
                result.push('\n');
            }
            ContentElement::List(l) => {
                result.push_str(&format_list(l, 0));
                result.push('\n');
            }
            ContentElement::Heading(h) => {
                let prefix = "#".repeat(h.level as usize);
                write!(result, "{} {}\n\n", prefix, h.text).unwrap();
            }
        }
    }

    result
}

/// Format inline content elements.
fn format_inline_content(
    content: &[crate::files::llm_output_extraction::xsd_validation_plan::InlineElement],
) -> String {
    use crate::files::llm_output_extraction::xsd_validation_plan::InlineElement;

    content
        .iter()
        .map(|e| match e {
            InlineElement::Text(s) => s.clone(),
            InlineElement::Emphasis(s) => format!("**{s}**"),
            InlineElement::Code(s) => format!("`{s}`"),
            InlineElement::Link { href, text } => format!("[{text}]({href})"),
        })
        .collect::<String>()
}

/// Format a list element with proper indentation.
fn format_list(
    list: &crate::files::llm_output_extraction::xsd_validation_plan::List,
    indent: usize,
) -> String {
    use crate::files::llm_output_extraction::xsd_validation_plan::ListType;

    let mut result = String::new();
    let indent_str = "  ".repeat(indent);

    for (i, item) in list.items.iter().enumerate() {
        let marker = match list.list_type {
            ListType::Ordered => format!("{}. ", i + 1),
            ListType::Unordered => "- ".to_string(),
        };

        result.push_str(&indent_str);
        result.push_str(&marker);
        result.push_str(&format_inline_content(&item.content));
        result.push('\n');

        if let Some(ref nested) = item.nested_list {
            result.push_str(&format_list(nested, indent + 1));
        }
    }

    result
}
