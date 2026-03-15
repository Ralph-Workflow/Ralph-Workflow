//! Review issues XML renderer.
//!
//! Renders review issues XML with semantic formatting:
//! - Box-drawing header with pass number
//! - Issue count or approval celebration
//! - Each issue as numbered item with file path extraction
//! - Visual separators between issues

use crate::files::llm_output_extraction::validate_issues_xml;
use crate::reducer::ui_event::{XmlCodeSnippet, XmlOutputContext};
use regex::Regex;
use std::collections::BTreeMap;
use std::fmt::Write;
use std::sync::LazyLock;

/// Render review issues XML with semantic formatting.
pub fn render(content: &str, output_context: Option<&XmlOutputContext>) -> String {
    let mut output = String::new();

    // Header with pass context
    if let Some(ctx) = output_context {
        if let Some(pass) = ctx.pass {
            writeln!(output, "\n╔═══ Review Pass {pass} ═══╗\n").unwrap();
        } else {
            output.push_str("\n╔═══ Review Results ═══╗\n\n");
        }
    } else {
        output.push_str("\n╔═══ Review Results ═══╗\n\n");
    }

    if let Ok(elements) = validate_issues_xml(content) {
        if elements.issues.is_empty() {
            // Celebration for no issues
            if let Some(ref msg) = elements.no_issues_found {
                output.push_str("🎉 ✅ Code Approved!\n\n");
                writeln!(output, "   {msg}").unwrap();
            } else {
                output.push_str("🎉 ✅ No issues found! Code looks good.\n");
            }
        } else {
            writeln!(
                output,
                "🔍 Found {} issue(s) to address:\n",
                elements.issues.len()
            )
            .unwrap();
            let issue_texts: Vec<String> = elements.issue_texts();
            output.push_str(&render_issues_grouped_by_file(&issue_texts, output_context));
        }
    } else {
        output.push_str("⚠️  Unable to parse issues XML\n\n");
        output.push_str(content);
    }

    output
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ParsedIssue {
    file: Option<String>,
    line_start: Option<u32>,
    line_end: Option<u32>,
    severity: Option<String>,
    snippet: Option<String>,
    description: String,
}

fn render_issues_grouped_by_file(issues: &[String], context: Option<&XmlOutputContext>) -> String {
    let parsed: Vec<ParsedIssue> = issues.iter().map(|i| parse_issue(i)).collect();
    let mut grouped: BTreeMap<String, Vec<ParsedIssue>> = BTreeMap::new();

    for issue in parsed {
        let key = issue
            .file
            .clone()
            .unwrap_or_else(|| "(no file)".to_string());
        grouped.entry(key).or_default().push(issue);
    }

    let mut output = String::new();
    for (file, issues) in grouped {
        writeln!(output, "📄 {file}").unwrap();
        for issue in issues {
            let mut header = String::new();
            if let Some(sev) = &issue.severity {
                write!(header, "[{sev}] ").unwrap();
            }
            if let Some(start) = issue.line_start {
                write!(header, "L{start}").unwrap();
                if let Some(end) = issue.line_end {
                    if end != start {
                        write!(header, "-L{end}").unwrap();
                    }
                }
                header.push_str(": ");
            }

            let desc = issue.description.trim();
            if header.is_empty() {
                writeln!(output, "   - {desc}").unwrap();
            } else {
                writeln!(output, "   - {header}{desc}").unwrap();
            }

            let snippet = issue
                .snippet
                .clone()
                .or_else(|| snippet_from_context(&issue, context));
            if let Some(snippet) = snippet {
                for line in snippet.lines() {
                    writeln!(output, "      {line}").unwrap();
                }
            }
        }
        output.push('\n');
    }

    output
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

/// Regex for parsing severity levels from issue text.
static SEVERITY_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^\[(critical|high|medium|low)\]\s*")
        .expect("invalid severity regex pattern - this is a compile-time constant")
});

/// Regex for parsing file locations in standard format (file.ext:123-456).
static LOCATION_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)(?P<file>[-_./A-Za-z0-9]+\.[A-Za-z0-9]+):(?P<start>\d+)(?:[-–—](?P<end>\d+))?(?::(?P<col>\d+))?")
        .expect("invalid location regex pattern - this is a compile-time constant")
});

/// Regex for parsing GitHub-style locations (file.ext#L123-L456).
static GH_LOCATION_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)(?P<file>[-_./A-Za-z0-9]+\.[A-Za-z0-9]+)#L(?P<start>\d+)(?:-L(?P<end>\d+))?")
        .expect("invalid GitHub location regex pattern - this is a compile-time constant")
});

/// Regex for parsing code snippets in markdown fenced code blocks.
static SNIPPET_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?s)```(?:[A-Za-z0-9_-]+)?\s*(?P<code>.*?)\s*```")
        .expect("invalid snippet regex pattern - this is a compile-time constant")
});

fn parse_issue(issue: &str) -> ParsedIssue {
    let trimmed = issue.trim();

    let mut working = trimmed.to_string();

    let severity = SEVERITY_RE
        .captures(&working)
        .and_then(|cap| cap.get(1).map(|m| m.as_str().to_ascii_lowercase()))
        .map(|s| match s.as_str() {
            "critical" => "Critical".to_string(),
            "high" => "High".to_string(),
            "medium" => "Medium".to_string(),
            "low" => "Low".to_string(),
            _ => s,
        });
    if severity.is_some() {
        working = SEVERITY_RE.replace(&working, "").to_string();
    }

    let snippet = SNIPPET_RE
        .captures(&working)
        .and_then(|cap| cap.name("code").map(|m| m.as_str().to_string()));
    if snippet.is_some() {
        working = SNIPPET_RE.replace(&working, "").to_string();
    }

    let (file, line_start, line_end) = LOCATION_RE
        .captures(&working)
        .or_else(|| GH_LOCATION_RE.captures(&working))
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
    }
}

/// Try to extract file path from issue text using common patterns.
/// Returns None if no clear file path is found.
fn extract_file_from_issue(issue: &str) -> Option<&str> {
    // Common patterns: "in src/file.rs", "at src/file.rs:123", "File: src/file.rs"
    // This is best-effort heuristic parsing
    for pattern in ["in ", "at ", "File: ", "file "] {
        if let Some(idx) = issue.find(pattern) {
            let start = idx + pattern.len();
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
                return Some(path);
            }
        }
    }
    None
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
}
