//! Development phase execution.
//!
//! This module handles the development phase of the Ralph pipeline, which consists
//! of iterative planning and execution cycles. Each iteration:
//! 1. Creates a PLAN.md from PROMPT.md
//! 2. Executes the plan
//! 3. Deletes PLAN.md
//! 4. Optionally runs fast checks

use crate::files::llm_output_extraction::PlanElements;

/// Format plan elements as markdown for PLAN.md.
pub(crate) fn format_plan_as_markdown(elements: &PlanElements) -> String {
    let summary = format_summary_section(elements);
    let skills = format_skills_section(elements);
    let steps = format_steps_section(elements);
    let critical = format_critical_files_section(elements);
    let risks = format_risks_section(elements);
    let verification = format_verification_section(elements);

    [summary, skills, steps, critical, risks, verification]
        .into_iter()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

fn format_summary_section(elements: &PlanElements) -> String {
    let scope_items: String = elements
        .summary
        .scope_items
        .iter()
        .map(|item| {
            let count_suffix = item
                .count
                .as_ref()
                .map(|c| format!(" **{c}** "))
                .unwrap_or_default();
            let category_suffix = item
                .category
                .as_ref()
                .map(|c| format!(" ({c})"))
                .unwrap_or_default();
            format!("- {count_suffix}{}{category_suffix}", item.description)
        })
        .collect::<Vec<_>>()
        .join("\n");

    format!(
        "## Summary\n\n{}\n\n### Scope\n\n{}\n",
        elements.summary.context, scope_items
    )
}

fn format_skills_section(elements: &PlanElements) -> String {
    let sm = match elements.skills_mcp.as_ref() {
        Some(sm) => sm,
        None => return String::new(),
    };
    let has_structured = !sm.skills.is_empty() || !sm.mcps.is_empty();
    if !has_structured && sm.raw_content.is_none() {
        return String::new();
    }

<<<<<<< Updated upstream
    let raw_content = sm
        .raw_content
        .as_ref()
        .and_then(|raw| {
            let trimmed = raw.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed.to_string())
=======
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
>>>>>>> Stashed changes
            }
        })
        .map(|c| format!("\n{c}"))
        .unwrap_or_default();

    let parts: Vec<String> = sm
        .skills
        .iter()
        .map(|skill| {
            skill
                .reason
                .as_ref()
                .map(|r| format!("- **Skill:** {} \u{2014} {}", skill.name, r))
                .unwrap_or_else(|| format!("- **Skill:** {}", skill.name))
        })
        .chain(sm.mcps.iter().map(|mcp| {
            mcp.reason
                .as_ref()
                .map(|r| format!("- **MCP:** {} \u{2014} {}", mcp.name, r))
                .unwrap_or_else(|| format!("- **MCP:** {}", mcp.name))
        }))
        .chain(std::iter::once(raw_content))
        .filter(|s| !s.is_empty())
        .collect();

    if parts.is_empty() {
        return String::new();
    }

    format!("### Skills & MCP Recommendations\n\n{}\n", parts.join("\n"))
}

fn format_steps_section(elements: &PlanElements) -> String {
    let steps: Vec<String> = elements.steps.iter().map(format_step_item).collect();

    format!("## Implementation Steps\n\n{}", steps.join("\n"))
}

fn format_step_item(
    step: &crate::files::llm_output_extraction::xsd_validation_plan::Step,
) -> String {
    let step_type_str = match step.kind {
        crate::files::llm_output_extraction::xsd_validation_plan::StepType::FileChange => {
            "file-change"
        }
        crate::files::llm_output_extraction::xsd_validation_plan::StepType::Action => "action",
        crate::files::llm_output_extraction::xsd_validation_plan::StepType::Research => "research",
    };

    let priority_str = step
        .priority
        .as_ref()
        .map(|p| {
            let s = match p {
                crate::files::llm_output_extraction::xsd_validation_plan::Priority::Critical => {
                    "critical"
                }
                crate::files::llm_output_extraction::xsd_validation_plan::Priority::High => "high",
                crate::files::llm_output_extraction::xsd_validation_plan::Priority::Medium => {
                    "medium"
                }
                crate::files::llm_output_extraction::xsd_validation_plan::Priority::Low => "low",
            };
            format!(" [{s}]")
        })
        .unwrap_or_default();

    let header = format!(
        "### Step {} ({}){}:  {}\n",
        step.number, step_type_str, priority_str, step.title
    );

    let target_files = if step.target_files.is_empty() {
        String::new()
    } else {
        let files: Vec<String> = step
            .target_files
            .iter()
            .map(|tf| {
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
                format!("- `{}` ({})", tf.path, action_str)
            })
            .collect();
        format!("**Target Files:**\n{}\n", files.join("\n"))
    };

    let location = step
        .location
        .as_ref()
        .map(|l| format!("**Location:** {l}\n"))
        .unwrap_or_default();

    let rationale = step
        .rationale
        .as_ref()
        .map(|r| format!("**Rationale:** {r}\n"))
        .unwrap_or_default();

    let content = format_rich_content(&step.content);

    let dependencies = if step.depends_on.is_empty() {
        String::new()
    } else {
        let deps: Vec<String> = step
            .depends_on
            .iter()
            .map(|d| format!("Step {d}"))
            .collect();
        format!("**Depends on:** {}\n\n", deps.join(", "))
    };

    [
        header,
        target_files,
        location,
        rationale,
        content,
        dependencies,
    ]
    .into_iter()
    .filter(|s| !s.is_empty())
    .collect::<Vec<_>>()
    .join("\n")
}

fn format_critical_files_section(elements: &PlanElements) -> String {
    let primary: Vec<String> = elements
        .critical_files
        .primary_files
        .iter()
        .map(|pf| {
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
            pf.estimated_changes
                .as_ref()
                .map(|est| format!("- `{}` ({}) - {}", pf.path, action_str, est))
                .unwrap_or_else(|| format!("- `{}` ({})", pf.path, action_str))
        })
        .collect();

    let reference: Vec<String> = elements
        .critical_files
        .reference_files
        .iter()
        .map(|rf| format!("- `{}` - {}", rf.path, rf.purpose))
        .collect();

    if primary.is_empty() && reference.is_empty() {
        return String::new();
    }

    let primary_section = if primary.is_empty() {
        String::new()
    } else {
        format!("### Primary Files\n\n{}\n", primary.join("\n"))
    };

    let reference_section = if reference.is_empty() {
        String::new()
    } else {
        format!("### Reference Files\n\n{}\n", reference.join("\n"))
    };

    format!(
        "## Critical Files\n\n{}{}",
        primary_section, reference_section
    )
}

fn format_risks_section(elements: &PlanElements) -> String {
    if elements.risks_mitigations.is_empty() {
        return String::new();
    }

    let risks: Vec<String> = elements
        .risks_mitigations
        .iter()
        .map(|rp| {
            let severity_str = rp
                .severity
                .as_ref()
                .map(|s| {
                    let sev_str = match s {
                        crate::files::llm_output_extraction::xsd_validation_plan::Severity::Low => {
                            "low"
                        }
                        crate::files::llm_output_extraction::xsd_validation_plan::Severity::Medium => {
                            "medium"
                        }
                        crate::files::llm_output_extraction::xsd_validation_plan::Severity::High => {
                            "high"
                        }
                        crate::files::llm_output_extraction::xsd_validation_plan::Severity::Critical => {
                            "critical"
                        }
                    };
                    format!(" [{sev_str}]")
                })
                .unwrap_or_default();
            format!(
                "**Risk{}:** {}\n**Mitigation:** {}\n",
                severity_str, rp.risk, rp.mitigation
            )
        })
        .collect();

    format!("## Risks & Mitigations\n\n{}", risks.join("\n"))
}

fn format_verification_section(elements: &PlanElements) -> String {
    if elements.verification_strategy.is_empty() {
        return String::new();
    }

    let items: Vec<String> = elements
        .verification_strategy
        .iter()
        .enumerate()
        .map(|(i, v)| {
            format!(
                "{}. **{}**\n   Expected: {}",
                i + 1,
                v.method,
                v.expected_outcome
            )
        })
        .collect();

    format!("## Verification Strategy\n\n{}", items.join("\n\n"))
}

/// Format rich content elements to markdown.
fn format_rich_content(
    content: &crate::files::llm_output_extraction::xsd_validation_plan::RichContent,
) -> String {
    use crate::files::llm_output_extraction::xsd_validation_plan::ContentElement;

    let elements: Vec<String> = content
        .elements
        .iter()
        .map(|element| match element {
            ContentElement::Paragraph(p) => {
                format!("{}\n\n", format_inline_content(&p.content))
            }
            ContentElement::CodeBlock(cb) => {
                let lang = cb.language.as_deref().unwrap_or("");
                let end_newline = if cb.content.ends_with('\n') {
                    String::new()
                } else {
                    "\n".to_string()
                };
                format!("```{lang}\n{}{}```\n\n", cb.content, end_newline)
            }
            ContentElement::Table(t) => format_table(t),
            ContentElement::List(l) => format_list(l, 0),
            ContentElement::Heading(h) => {
                let prefix = "#".repeat(h.level as usize);
                format!("{} {}\n\n", prefix, h.text)
            }
        })
        .collect();

    elements.join("")
}

fn format_table(t: &crate::files::llm_output_extraction::xsd_validation_plan::Table) -> String {
    let caption = t
        .caption
        .as_ref()
        .map(|c| format!("**{c}**\n"))
        .unwrap_or_default();

    let column_count = if !t.columns.is_empty() {
        t.columns.len()
    } else {
        t.rows.first().map(|r| r.cells.len()).unwrap_or(0)
    };

    if column_count == 0 {
        return caption;
    }

    let header = if !t.columns.is_empty() {
        format!("| {} |\n", t.columns.join(" | "))
    } else {
        String::new()
    };

    let separator = format!(
        "| {} |\n",
        (0..column_count)
            .map(|_| "---")
            .collect::<Vec<_>>()
            .join(" | ")
    );

    let rows: Vec<String> = t
        .rows
        .iter()
        .map(|row| {
            let cells: Vec<String> = row
                .cells
                .iter()
                .map(|c| format_inline_content(&c.content))
                .collect();
            format!("| {} |", cells.join(" | "))
        })
        .collect();

    format!("{}{}{}{}", caption, header, separator, rows.join("\n"))
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

    let indent_str = "  ".repeat(indent);

    let items: Vec<String> = list
        .items
        .iter()
        .enumerate()
        .map(|(i, item)| {
            let marker = match list.list_type {
                ListType::Ordered => format!("{}. ", i + 1),
                ListType::Unordered => "- ".to_string(),
            };

            let content = format_inline_content(&item.content);
            let nested = item
                .nested_list
                .as_ref()
                .map(|n| format_list(n, indent + 1))
                .unwrap_or_default();

            let nested_with_newline = if nested.is_empty() {
                String::new()
            } else {
                format!("\n{nested}")
            };

            format!("{}{}{}{}", indent_str, marker, content, nested_with_newline)
        })
        .collect();

    format!("{}\n", items.join("\n"))
}
