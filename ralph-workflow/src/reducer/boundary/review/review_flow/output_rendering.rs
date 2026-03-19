// Review phase output rendering.
//
// This module handles converting validated XML output to human-readable markdown format,
// extracting code snippets from referenced files, and archiving processed XML files.
//
// ## Responsibilities
//
// - Converting validated XML to Markdown format (.agent/ISSUES.md)
// - Extracting code snippets from files referenced in issues
// - Parsing file locations from issue text (standard and GitHub formats)
// - Reading source files to extract snippet context
// - Archiving processed XML files
// - Determining final review outcome (clean vs issues found)
//
// ## File Location Formats
//
// The snippet extractor supports two formats:
// - Standard: `path/to/file.rs:10-20` or `path/to/file.rs:10`
// - GitHub: `path/to/file.rs#L10-L20` or `path/to/file.rs#L10`
//
// ## Snippet Extraction
//
// When an issue references a file location, the extractor:
// 1. Parses the file path and line range
// 2. Reads the file from the workspace
// 3. Extracts the relevant lines with line numbers
// 4. Deduplicates snippets (same file/line range)
// 5. Attaches snippets to UI events for display
//
// ## See Also
//
// - `validation.rs` - XML validation that produces the input for rendering

use crate::rendering::xml::render_skills_mcp_markdown;

/// Extract code snippets from files referenced in issues.
///
/// Parses issue text for file locations in standard (`file:line-line`) or GitHub
/// (`file#Lline-Lline`) format, reads the files from the workspace, and extracts
/// the referenced line ranges.
///
/// Deduplicates snippets to avoid redundant extraction when multiple issues
/// reference the same location.
fn extract_issue_snippets(
    issues: &[String],
    workspace: &dyn crate::workspace::Workspace,
) -> Vec<XmlCodeSnippet> {
    let location_re =
        crate::reducer::handler::review::review_flow::regex_cache::issue_location_regex();
    let gh_location_re =
        crate::reducer::handler::review::review_flow::regex_cache::issue_gh_location_regex();

    let seen: std::collections::HashSet<(String, u32, u32)> = issues
        .iter()
        .filter_map(|issue| {
            let capture = location_re
                .captures(issue)
                .or_else(|| gh_location_re.captures(issue))?;
            let file = capture.name("file")?.as_str().trim().replace('\\', "/");
            let file = normalize_issue_file_path_to_workspace_relative(&file, workspace)?;
            let start = capture.name("start")?.as_str().parse::<u32>().ok()?;
            let end = capture
                .name("end")
                .and_then(|m| m.as_str().parse::<u32>().ok())
                .unwrap_or(start);
            Some((file, start, end))
        })
        .collect();

    seen.into_iter()
        .filter_map(|(file, start, end)| {
            let content = workspace.read(Path::new(&file)).ok()?;
            let snippet = extract_snippet_lines(&content, start, end)?;
            Some(XmlCodeSnippet {
                file,
                line_start: start,
                line_end: end,
                content: snippet,
            })
        })
        .collect()
}

fn normalize_issue_file_path_to_workspace_relative(
    file: &str,
    workspace: &dyn crate::workspace::Workspace,
) -> Option<String> {
    let trimmed = file.trim();
    if trimmed.is_empty() {
        return None;
    }

    // Reject UNC-like paths regardless of platform.
    if trimmed.starts_with("//") {
        return None;
    }

    let normalized = trimmed.replace('\\', "/");

    if is_safe_workspace_relative_path(&normalized) {
        return Some(normalized);
    }

    let root = workspace.root();
    let path = Path::new(&normalized);

    // Accept absolute paths only when they are under the workspace root.
    if path.is_absolute() {
        let stripped = path.strip_prefix(root).ok()?;
        let candidate = stripped.to_string_lossy().replace('\\', "/");
        if is_safe_workspace_relative_path(&candidate) {
            return Some(candidate);
        }
        return None;
    }

    // Normalize Windows drive-style paths like "C:\\repo\\src\\lib.rs".
    // Only accept them when they clearly refer to the current workspace root.
    let bytes = normalized.as_bytes();
    if bytes.len() >= 2 && bytes[1] == b':' {
        let first = bytes[0] as char;
        if first.is_ascii_alphabetic() {
            let remainder = normalized[2..].trim_start_matches('/');
            let base = root.file_name()?.to_str()?;
            let remainder = remainder.strip_prefix(base)?;
            let remainder = remainder.trim_start_matches('/');
            if remainder.is_empty() {
                return None;
            }

            let candidate = remainder.to_string();
            if is_safe_workspace_relative_path(&candidate) {
                return Some(candidate);
            }
            return None;
        }
    }

    None
}

fn is_safe_workspace_relative_path(path_str: &str) -> bool {
    use std::path::Component;

    let trimmed = path_str.trim();
    if trimmed.is_empty() {
        return false;
    }

    let bytes = trimmed.as_bytes();
    if bytes.len() >= 2 && bytes[1] == b':' {
        let first = bytes[0] as char;
        if first.is_ascii_alphabetic() {
            return false;
        }
    }

    if trimmed.starts_with("//") {
        return false;
    }

    let path = Path::new(trimmed);
    if path.is_absolute() {
        return false;
    }

    !path.components().any(|component| {
        matches!(
            component,
            Component::ParentDir | Component::RootDir | Component::Prefix(_)
        )
    })
}

/// Extract a snippet from file content for the given line range.
///
/// Returns the extracted lines with line numbers prepended (e.g., "42 | code here").
/// Line numbers are 1-based. Returns `None` if the range is invalid.
fn extract_snippet_lines(content: &str, start: u32, end: u32) -> Option<String> {
    if start < 1 || end < 1 || end < start {
        return None;
    }

    let lines: Vec<&str> = content.lines().collect();
    if lines.is_empty() {
        return None;
    }

    let start_idx = start.saturating_sub(1) as usize;
    if start_idx >= lines.len() {
        return None;
    }

    let end_idx = end.saturating_sub(1) as usize;
    let end_idx = end_idx.min(lines.len().saturating_sub(1));

    let snippet = lines[start_idx..=end_idx]
        .iter()
        .enumerate()
        .map(|(offset, line)| {
            let line_no = u32::try_from(offset)
                .ok()
                .map(|offset| start + offset)
                .unwrap_or(0);
            format!("{line_no} | {line}")
        })
        .collect::<Vec<_>>()
        .join("\n");

    Some(snippet)
}

/// Render validated issues XML elements to markdown format.
///
/// Produces a markdown checklist with each issue as an unchecked item.
/// If `no_issues_found` is present and no issues exist, renders the no-issues message.
fn render_issues_markdown(
    elements: &crate::files::llm_output_extraction::IssuesElements,
) -> String {
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

    let issues_text = elements
        .issues
        .iter()
        .filter_map(|issue| {
            let trimmed = issue.text.trim();
            if trimmed.is_empty() {
                None
            } else {
                let skills_mcp_text = render_skills_mcp_markdown(issue.skills_mcp.as_ref());
                Some(format!("- [ ] {}{}", trimmed, skills_mcp_text))
            }
        })
        .collect::<Vec<_>>()
        .join("\n");

    format!("# Issues\n\n{}", issues_text)
}

impl MainEffectHandler {
    pub(in crate::reducer::handler) fn write_issues_markdown(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
        use std::path::Path;

        let outcome = self
            .state
            .review_validated_outcome
            .as_ref()
            .filter(|outcome| outcome.pass == pass)
            .ok_or(ErrorEvent::ValidatedReviewOutcomeMissing { pass })?;

        // Try to get structured issue data from XML for skills-mcp.
        // Fall back to plain-string reconstruction if XML is unavailable.
        let elements = ctx
            .workspace
            .read(Path::new(xml_paths::ISSUES_XML))
            .ok()
            .and_then(|xml| crate::files::llm_output_extraction::validate_issues_xml(&xml).ok())
            .unwrap_or_else(|| crate::files::llm_output_extraction::IssuesElements {
                issues: outcome
                    .issues
                    .iter()
                    .map(|s| crate::files::llm_output_extraction::IssueEntry {
                        text: s.clone(),
                        skills_mcp: None,
                    })
                    .collect(),
                no_issues_found: outcome.no_issues_found.clone(),
            });

        let markdown = render_issues_markdown(&elements);
        ctx.workspace
            .write(Path::new(".agent/ISSUES.md"), &markdown)
            .map_err(|err| ErrorEvent::WorkspaceWriteFailed {
                path: ".agent/ISSUES.md".to_string(),
                kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
            })?;

        Ok(EffectResult::event(
            PipelineEvent::review_issues_markdown_written(pass),
        ))
    }

    pub(in crate::reducer::handler) fn extract_review_issue_snippets(
        &self,
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> Result<EffectResult> {
        use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
        use std::path::Path;

        let outcome = self
            .state
            .review_validated_outcome
            .as_ref()
            .filter(|outcome| outcome.pass == pass)
            .ok_or(ErrorEvent::ValidatedReviewOutcomeMissing { pass })?;

        let issues_xml = ctx.workspace.read(Path::new(xml_paths::ISSUES_XML));
        let issues_xml = match issues_xml {
            Ok(s) => s,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                ctx.logger
                    .warn("Missing .agent/tmp/issues.xml; using empty content for UI output");
                String::new()
            }
            Err(err) => {
                return Err(ErrorEvent::WorkspaceReadFailed {
                    path: xml_paths::ISSUES_XML.to_string(),
                    kind: WorkspaceIoErrorKind::from_io_error_kind(err.kind()),
                }
                .into());
            }
        };

        let snippets = extract_issue_snippets(&outcome.issues, ctx.workspace);
        Ok(EffectResult::with_ui(
            PipelineEvent::review_issue_snippets_extracted(pass),
            vec![UIEvent::XmlOutput {
                xml_type: XmlOutputType::ReviewIssues,
                content: issues_xml,
                context: Some(XmlOutputContext {
                    iteration: None,
                    pass: Some(pass),
                    snippets,
                }),
            }],
        ))
    }

    pub(in crate::reducer::handler) fn archive_review_issues_xml(
        ctx: &PhaseContext<'_>,
        pass: u32,
    ) -> EffectResult {
        use crate::files::llm_output_extraction::archive_xml_file_with_workspace;
        use crate::files::llm_output_extraction::file_based_extraction::paths as xml_paths;
        use std::path::Path;

        archive_xml_file_with_workspace(ctx.workspace, Path::new(xml_paths::ISSUES_XML));
        EffectResult::event(PipelineEvent::review_issues_xml_archived(pass))
    }

    pub(in crate::reducer::handler) const fn apply_review_outcome(
        _ctx: &mut PhaseContext<'_>,
        pass: u32,
        issues_found: bool,
        clean_no_issues: bool,
    ) -> EffectResult {
        if clean_no_issues {
            return EffectResult::event(PipelineEvent::review_pass_completed_clean(pass));
        }
        EffectResult::event(PipelineEvent::review_completed(pass, issues_found))
    }
}

#[cfg(test)]
mod tests {
    use super::{extract_issue_snippets, extract_snippet_lines, render_issues_markdown};
    use crate::files::llm_output_extraction::{IssueEntry, SkillsMcp};
    use crate::workspace::MemoryWorkspace;

    #[test]
    fn test_extract_issue_snippets_rejects_unsafe_paths() {
        let workspace = MemoryWorkspace::new_test()
            .with_file("src/main.rs", "fn main() {}\n")
            .with_file("../secret.txt", "top secret\n")
            .with_file("/etc/passwd.txt", "root:x:0:0:root:/root:/bin/bash\n")
            .with_file("C:/secret.txt", "windows secret\n");

        let issues = vec![
            "src/main.rs:1".to_string(),
            "../secret.txt:1".to_string(),
            "/etc/passwd.txt:1".to_string(),
            "C:/secret.txt:1".to_string(),
        ];

        let snippets = extract_issue_snippets(&issues, &workspace);

        assert_eq!(snippets.len(), 1, "expected only the safe snippet");
        assert_eq!(snippets[0].file, "src/main.rs");
        assert_eq!(snippets[0].line_start, 1);
        assert_eq!(snippets[0].line_end, 1);
        assert!(snippets[0].content.contains("1 | fn main() {}"));
    }

    #[test]
    fn test_extract_snippet_lines_rejects_reversed_ranges() {
        let content = "line1\nline2\n";
        assert!(extract_snippet_lines(content, 2, 1).is_none());
    }

    #[test]
    fn test_extract_snippet_lines_requires_one_based_start() {
        let content = "line1\n";
        assert!(extract_snippet_lines(content, 0, 1).is_none());
    }

    #[test]
    fn test_render_issues_markdown_with_skills_mcp() {
        let elements = crate::files::llm_output_extraction::IssuesElements {
            issues: vec![
                IssueEntry {
                    text: "src/main.rs:42 - Variable unused".to_string(),
                    skills_mcp: Some(SkillsMcp {
                        skills: vec![crate::files::llm_output_extraction::SkillEntry {
                            name: "test-driven-development".to_string(),
                            reason: Some("Start with failing test".to_string()),
                        }],
                        mcps: vec![crate::files::llm_output_extraction::McpEntry {
                            name: "context7".to_string(),
                            reason: Some("Use for library research".to_string()),
                        }],
                        raw_content: None,
                    }),
                },
                IssueEntry {
                    text: "src/lib.rs:10 - Missing error handling".to_string(),
                    skills_mcp: None,
                },
            ],
            no_issues_found: None,
        };

        let output = render_issues_markdown(&elements);

        assert!(
            output.contains("Variable unused"),
            "Should show first issue"
        );
        assert!(
            output.contains("test-driven-development"),
            "Should show skill name"
        );
        assert!(output.contains("context7"), "Should show mcp name");
        assert!(
            output.contains("Start with failing test"),
            "Should show skill reason"
        );
        assert!(
            output.contains("Missing error handling"),
            "Should show second issue"
        );
    }

    #[test]
    fn test_render_issues_markdown_skills_mcp_raw_content() {
        let elements = crate::files::llm_output_extraction::IssuesElements {
            issues: vec![IssueEntry {
                text: "Some issue".to_string(),
                skills_mcp: Some(SkillsMcp {
                    skills: vec![],
                    mcps: vec![],
                    raw_content: Some("some unstructured content".to_string()),
                }),
            }],
            no_issues_found: None,
        };

        let output = render_issues_markdown(&elements);

        assert!(
            output.contains("some unstructured content"),
            "Should show raw content"
        );
    }
}
