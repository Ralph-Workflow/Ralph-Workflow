//! Review issues XML renderer.
//!
//! Renders review issues XML with semantic formatting:
//! - Box-drawing header with pass number
//! - Issue count or approval celebration
//! - Each issue as numbered item with file path extraction
//! - Visual separators between issues

use crate::files::llm_output_extraction::validate_issues_xml;
use crate::files::llm_output_extraction::{IssueEntry, SkillsMcp};
use crate::reducer::ui_event::{XmlCodeSnippet, XmlOutputContext};
use regex::Regex;
use std::collections::BTreeMap;

/// Render review issues XML with semantic formatting.
pub fn render(content: &str, output_context: Option<&XmlOutputContext>) -> String {
    let header = if let Some(ctx) = output_context {
        if let Some(pass) = ctx.pass {
            format!("\n╔═══ Review Pass {pass} ═══╗\n\n")
        } else {
            "\n╔═══ Review Results ═══╗\n\n".to_string()
        }
    } else {
        "\n╔═══ Review Results ═══╗\n\n".to_string()
    };

    if let Ok(elements) = validate_issues_xml(content) {
        if elements.issues.is_empty() {
            let body = if let Some(ref msg) = elements.no_issues_found {
                format!("🎉 ✅ Code Approved!\n\n   {msg}\n")
            } else {
                "🎉 ✅ No issues found! Code looks good.\n".to_string()
            };
            format!("{}{}", header, body)
        } else {
            let count_msg = format!(
                "🔍 Found {} issue(s) to address:\n\n",
                elements.issues.len()
            );
            let issues_output = render_issues_grouped_by_file(&elements.issues, output_context);
            format!("{}{}{}", header, count_msg, issues_output)
        }
    } else {
        format!("{}⚠️  Unable to parse issues XML\n\n{}", header, content)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ParsedIssue {
    file: Option<String>,
    line_start: Option<u32>,
    line_end: Option<u32>,
    severity: Option<String>,
    snippet: Option<String>,
    description: String,
    skills_mcp: Option<SkillsMcp>,
}

fn render_issues_grouped_by_file(
    issues: &[IssueEntry],
    context: Option<&XmlOutputContext>,
) -> String {
    let parsed: Vec<ParsedIssue> = issues.iter().map(parse_issue_entry).collect();

    let grouped: BTreeMap<String, Vec<ParsedIssue>> =
        parsed.iter().fold(BTreeMap::new(), |groups, issue| {
            let key = issue
                .file
                .clone()
                .unwrap_or_else(|| "(no file)".to_string());
            let existing: Vec<ParsedIssue> = groups.get(&key).cloned().unwrap_or_default();
            let updated: Vec<ParsedIssue> = existing
                .into_iter()
                .chain(std::iter::once(issue.clone()))
                .collect();
            groups
                .into_iter()
                .chain(std::iter::once((key, updated)))
                .collect()
        });

    let output = grouped
        .iter()
        .map(|(file, issues)| {
            let file_section = format!("📄 {file}");
            let issue_sections = issues
                .iter()
                .map(|issue| {
                    let header = build_issue_header(issue);
                    let desc = issue.description.trim();
                    let header_prefix = if header.is_empty() {
                        String::new()
                    } else {
                        format!("{header}: ")
                    };
                    let main_line = format!("   - {header_prefix}{desc}");

                    let snippet = issue
                        .snippet
                        .clone()
                        .or_else(|| snippet_from_context(issue, context));

                    let snippet_lines: Vec<String> = snippet
                        .iter()
                        .flat_map(|s| s.lines())
                        .map(|line| format!("      {line}"))
                        .collect();

                    let skills_mcp = render_skills_mcp_inline_to_string(issue.skills_mcp.as_ref());

                    let parts: String = std::iter::once(main_line)
                        .chain(snippet_lines)
                        .chain(skills_mcp)
                        .collect::<Vec<_>>()
                        .join("\n");

                    format!("{}", parts)
                })
                .collect::<Vec<_>>()
                .join("\n");

            format!("{}\n{}\n", file_section, issue_sections)
        })
        .collect::<Vec<_>>()
        .join("\n");

    output
}

fn build_issue_header(issue: &ParsedIssue) -> String {
    let severity_part = issue
        .severity
        .as_ref()
        .map(|sev| format!("[{sev}] "))
        .unwrap_or_default();

    let line_part = issue.line_start.map_or(String::new(), |start| {
        let end_part = issue
            .line_end
            .filter(|&end| end != start)
            .map_or(String::new(), |end| format!("-L{end}"));
        format!("L{start}{end_part}: ")
    });

    format!("{severity_part}{line_part}")
}

fn snippet_from_context(issue: &ParsedIssue, context: Option<&XmlOutputContext>) -> Option<String> {
    let ctx = context.as_ref()?;
    let file = issue.file.as_ref()?;
    let start = issue.line_start?;
    let end = issue.line_end.unwrap_or(start);

    ctx.snippets
        .iter()
        .find(|s| snippet_matches_issue(s, file, start, end))
        .map(|s| s.content.clone())
}

fn snippet_matches_issue(snippet: &XmlCodeSnippet, file: &str, start: u32, end: u32) -> bool {
    file_matches(&snippet.file, file)
        && ranges_overlap(snippet.line_start, snippet.line_end, start, end)
}

fn file_matches(snippet_file: &str, issue_file: &str) -> bool {
    let snippet_norm = normalize_path_for_match(snippet_file);
    let issue_norm = normalize_path_for_match(issue_file);
    if snippet_norm == issue_norm {
        return true;
    }

    // Be tolerant of differing prefixes (e.g. `./src/lib.rs` vs `src/lib.rs`),
    // and of callers emitting paths rooted at a sub-crate (`ralph-workflow/src/...`).
    let snippet_suffix = format!("/{issue_norm}");
    if snippet_norm.ends_with(&snippet_suffix) {
        return true;
    }

    let issue_suffix = format!("/{snippet_norm}");
    issue_norm.ends_with(&issue_suffix)
}

fn normalize_path_for_match(path: &str) -> String {
    path.replace('\\', "/").trim_start_matches("./").to_string()
}

const fn ranges_overlap(a_start: u32, a_end: u32, b_start: u32, b_end: u32) -> bool {
    a_start <= b_end && b_start <= a_end
}

#[expect(
    clippy::unwrap_used,
    reason = "hardcoded regex pattern is guaranteed to compile"
)]
fn severity_regex() -> Regex {
    Regex::new(r"(?i)^\[(critical|high|medium|low)\]\s*").unwrap()
}

#[expect(
    clippy::unwrap_used,
    reason = "hardcoded regex pattern is guaranteed to compile"
)]
fn location_regex() -> Regex {
    Regex::new(r"(?m)(?P<file>[-_./A-Za-z0-9]+\.[A-Za-z0-9]+):(?P<start>\d+)(?:[-–—](?P<end>\d+))?(?::(?P<col>\d+))?").unwrap()
}

#[expect(
    clippy::unwrap_used,
    reason = "hardcoded regex pattern is guaranteed to compile"
)]
fn gh_location_regex() -> Regex {
    Regex::new(r"(?m)(?P<file>[-_./A-Za-z0-9]+\.[A-Za-z0-9]+)#L(?P<start>\d+)(?:-L(?P<end>\d+))?")
        .unwrap()
}

#[expect(
    clippy::unwrap_used,
    reason = "hardcoded regex pattern is guaranteed to compile"
)]
fn snippet_regex() -> Regex {
    Regex::new(r"(?s)```(?:[A-Za-z0-9_-]+)?\s*(?P<code>.*?)\s*```").unwrap()
}

fn parse_issue_entry(issue: &IssueEntry) -> ParsedIssue {
    let parsed = parse_issue(&issue.text);
    ParsedIssue {
        file: parsed.file,
        line_start: parsed.line_start,
        line_end: parsed.line_end,
        severity: parsed.severity,
        snippet: parsed.snippet,
        description: parsed.description,
        skills_mcp: issue.skills_mcp.clone(),
    }
}

fn parse_issue(issue: &str) -> ParsedIssue {
    let trimmed = issue.trim();

    let severity = severity_regex()
        .captures(trimmed)
        .and_then(|cap| cap.get(1).map(|m| m.as_str().to_ascii_lowercase()))
        .map(|s| match s.as_str() {
            "critical" => "Critical".to_string(),
            "high" => "High".to_string(),
            "medium" => "Medium".to_string(),
            "low" => "Low".to_string(),
            _ => s,
        });

    let working_without_severity = if severity.is_some() {
        severity_regex().replace(trimmed, "").to_string()
    } else {
        trimmed.to_string()
    };

    let snippet = snippet_regex()
        .captures(&working_without_severity)
        .and_then(|cap| cap.name("code").map(|m| m.as_str().to_string()));

    let working = if snippet.is_some() {
        snippet_regex()
            .replace(&working_without_severity, "")
            .to_string()
    } else {
        working_without_severity
    };

    let (file, line_start, line_end) = location_regex()
        .captures(&working)
        .or_else(|| gh_location_regex().captures(&working))
        .map_or_else(
            || {
                (
                    extract_file_from_issue(&working).map(std::string::ToString::to_string),
                    None,
                    None,
                )
            },
            |cap| {
                let file = cap.name("file").map(|m| m.as_str().to_string());
                let start = cap
                    .name("start")
                    .and_then(|m| m.as_str().parse::<u32>().ok());
                let end = cap
                    .name("end")
                    .and_then(|m| m.as_str().parse::<u32>().ok())
                    .or(start);
                (file, start, end)
            },
        );

    let description = working
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect::<Vec<&str>>()
        .join(" ");

    ParsedIssue {
        file,
        line_start,
        line_end,
        severity,
        snippet,
        description,
        skills_mcp: None,
    }
}

fn render_skills_mcp_inline_to_string(skills_mcp: Option<&SkillsMcp>) -> Vec<String> {
    if let Some(sm) = skills_mcp {
        let has_structured = !sm.skills.is_empty() || !sm.mcps.is_empty();
        if has_structured || sm.raw_content.is_some() {
            let skill_lines: Vec<String> = sm
                .skills
                .iter()
                .map(|skill| {
                    skill
                        .reason
                        .as_ref()
                        .map(|r| format!("      - skill: {} \u{2014} {}", skill.name, r))
                        .unwrap_or_else(|| format!("      - skill: {}", skill.name))
                })
                .collect();

            let mcp_lines: Vec<String> = sm
                .mcps
                .iter()
                .map(|mcp| {
                    mcp.reason
                        .as_ref()
                        .map(|r| format!("      - mcp: {} \u{2014} {}", mcp.name, r))
                        .unwrap_or_else(|| format!("      - mcp: {}", mcp.name))
                })
                .collect();

            let raw_line = sm
                .raw_content
                .as_ref()
                .filter(|raw| {
                    let trimmed: &str = raw.trim();
                    !trimmed.is_empty() && !has_structured
                })
                .map(|raw| format!("      - {}", raw.trim()));

            std::iter::empty()
                .chain(skill_lines)
                .chain(mcp_lines)
                .chain(raw_line)
                .collect()
        } else {
            Vec::new()
        }
    } else {
        Vec::new()
    }
}

/// Try to extract file path from issue text using common patterns.
/// Returns None if no clear file path is found.
fn extract_file_from_issue(issue: &str) -> Option<&str> {
    // Common patterns: "in src/file.rs", "at src/file.rs:123", "File: src/file.rs"
    // This is best-effort heuristic parsing
    let patterns = ["in ", "at ", "File: ", "file "];

    patterns.iter().find_map(|pattern| {
        let idx = issue.find(*pattern)?;
        let start = idx.saturating_add(pattern.len());
        let rest = &issue[start..];
        // Find end of path (space, comma, colon for line number, or end of string)
        let end = rest
            .find(|c: char| c.is_whitespace() || c == ',')
            .unwrap_or(rest.len());
        // Handle colon followed by line number (e.g., src/file.rs:123)
        let path_with_line = &rest[..end];
        let path = path_with_line
            .find(':')
            .map_or(path_with_line, |colon_pos| &path_with_line[..colon_pos]);
        if path.contains('/') || path.contains('.') {
            Some(path)
        } else {
            None
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_issues_with_issues() {
        let xml = r"<ralph-issues>
<ralph-issue>Variable unused in src/main.rs</ralph-issue>
<ralph-issue>Missing error handling</ralph-issue>
</ralph-issues>";

        let ctx = Some(XmlOutputContext {
            iteration: None,
            pass: Some(1),
            snippets: Vec::new(),
        });
        let output = render(xml, ctx.as_ref());

        assert!(output.contains("Review Pass 1"), "Should show pass number");
        assert!(output.contains("2 issue"), "Should show issue count");
        assert!(output.contains("Variable unused"), "Should list issues");
        assert!(
            output.contains("📄 src/main.rs"),
            "Should group issues under extracted file"
        );
        assert!(
            output.contains("Missing error handling"),
            "Should include issues without file"
        );
    }

    #[test]
    fn test_render_issues_groups_by_file_and_renders_line_ranges_and_snippets() {
        let xml = r"<ralph-issues>
<ralph-issue>[High] src/main.rs:12-18 - Avoid unwrap in production code
```rust
let x = foo().unwrap();
```
</ralph-issue>
<ralph-issue>src/lib.rs:44:3 - Rename variable for clarity</ralph-issue>
<ralph-issue>General suggestion with no file</ralph-issue>
</ralph-issues>";

        let output = render(xml, None);

        assert!(
            output.contains("📄 src/main.rs") && output.contains("📄 src/lib.rs"),
            "Should render grouped file headers"
        );
        assert!(
            output.contains("L12") && output.contains("L18"),
            "Should include parsed line range in Lx-Ly form"
        );
        assert!(output.contains("[High]"), "Should include severity badge");
        assert!(
            output.contains("let x = foo().unwrap()"),
            "Should include extracted snippet"
        );
        assert!(
            output.contains("General suggestion"),
            "Should not drop issues without file"
        );
    }

    #[test]
    fn test_render_issues_uses_context_snippets_when_issue_has_location_but_no_fenced_code() {
        let xml = r"<ralph-issues>
<ralph-issue>./src/lib.rs:44-44 - Rename variable for clarity</ralph-issue>
</ralph-issues>";

        let ctx = Some(XmlOutputContext {
            iteration: None,
            pass: Some(1),
            snippets: vec![XmlCodeSnippet {
                file: "src/lib.rs".to_string(),
                line_start: 42,
                line_end: 46,
                content: "42 | let old_name = 1;\n43 | let x = old_name;\n44 | let clearer = old_name;\n45 | println!(\"{}\", clearer);".to_string(),
            }],
        });

        let output = render(xml, ctx.as_ref());

        assert!(
            output.contains("let clearer"),
            "Should render snippet from context even when file path differs by prefix"
        );
    }

    #[test]
    fn test_render_issues_no_issues() {
        let xml = r"<ralph-issues>
<ralph-no-issues-found>The code looks good, no issues detected</ralph-no-issues-found>
</ralph-issues>";

        let output = render(xml, None);

        assert!(output.contains("✅"), "Should show approval emoji");
        assert!(
            output.contains("no issues detected"),
            "Should show no-issues message"
        );
    }

    #[test]
    fn test_render_issues_malformed_fallback() {
        let bad_xml = "random text";
        let output = render(bad_xml, None);

        assert!(output.contains("⚠️"), "Should show warning");
    }

    #[test]
    fn test_extract_file_from_issue_pattern_in() {
        let issue = "Unused variable in src/main.rs";
        let file = extract_file_from_issue(issue);
        assert_eq!(file, Some("src/main.rs"));
    }

    #[test]
    fn test_extract_file_from_issue_pattern_at() {
        let issue = "Error at src/lib.rs:42 - missing semicolon";
        let file = extract_file_from_issue(issue);
        assert_eq!(file, Some("src/lib.rs"));
    }

    #[test]
    fn test_extract_file_from_issue_no_file() {
        let issue = "General code quality concern";
        let file = extract_file_from_issue(issue);
        assert!(file.is_none());
    }

    #[test]
    fn test_render_issues_celebration_on_approval() {
        let xml = r"<ralph-issues>
<ralph-no-issues-found>All code looks great!</ralph-no-issues-found>
</ralph-issues>";

        let output = render(xml, None);
        assert!(output.contains("🎉"), "Should celebrate approval");
        assert!(
            output.contains("Code Approved"),
            "Should show approval message"
        );
    }

    #[test]
    fn test_render_issues_shows_snippet_from_context_when_not_in_issue_text() {
        let xml = r"<ralph-issues>
<ralph-issue>[High] src/lib.rs:2 Missing semicolon</ralph-issue>
</ralph-issues>";

        let ctx = Some(XmlOutputContext {
            iteration: None,
            pass: Some(1),
            snippets: vec![XmlCodeSnippet {
                file: "src/lib.rs".to_string(),
                line_start: 1,
                line_end: 3,
                content: "fn example() {\n    let x = 1\n}\n".to_string(),
            }],
        });

        let output = render(xml, ctx.as_ref());

        assert!(
            output.contains("fn example()"),
            "Should render snippet content when provided via context: {output}"
        );
        assert!(
            output.contains("src/lib.rs"),
            "Should show file context: {output}"
        );
    }

    #[test]
    fn test_render_issues_with_per_issue_skills_mcp() {
        let xml = r#"<ralph-issues>
<ralph-issue>src/main.rs:42 - Variable unused
<skills-mcp>
<skill reason="Start with failing test">test-driven-development</skill>
<mcp reason="Use for library docs">context7</mcp>
</skills-mcp>
</ralph-issue>
<ralph-issue>src/lib.rs:10 - Missing error handling</ralph-issue>
</ralph-issues>"#;

        let output = render(xml, None);

        assert!(
            output.contains("Variable unused"),
            "Should show first issue"
        );
        assert!(
            output.contains("test-driven-development"),
            "Should show skill for first issue"
        );
        assert!(
            output.contains("context7"),
            "Should show mcp for first issue"
        );
        assert!(
            output.contains("Missing error handling"),
            "Should show second issue"
        );
        assert!(
            output.contains("Start with failing test"),
            "Should show skill reason"
        );
    }

    #[test]
    fn test_render_issues_skills_mcp_raw_content() {
        let xml = r"<ralph-issues>
<ralph-issue>src/main.rs:1 - Some issue
<skills-mcp>
some unstructured content
</skills-mcp>
</ralph-issue>
</ralph-issues>";

        let output = render(xml, None);

        assert!(
            output.contains("some unstructured content"),
            "Should show raw content"
        );
    }
}
