//! Commit message XML renderer.
//!
//! Renders commit message XML with structured visual output:
//! - Box-drawing header
//! - Subject line prominently displayed
//! - All body variants: simple body, summary/details/footer
//! - Skip path with reason
//! - Selected files list when `ralph-files` is present

use super::helpers::extract_tag_content;
use std::fmt::Write;

/// Extract all occurrences of a tag from XML content.
fn extract_all_tag_content(content: &str, tag_name: &str) -> Vec<String> {
    let start_tag = format!("<{tag_name}>");
    let end_tag = format!("</{tag_name}>");
    let mut results = Vec::new();
    let mut search_from = 0;

    while let Some(start_pos) = content[search_from..].find(&start_tag) {
        let abs_start = search_from + start_pos + start_tag.len();
        if let Some(end_pos) = content[abs_start..].find(&end_tag) {
            let value = content[abs_start..abs_start + end_pos].trim().to_string();
            if !value.is_empty() {
                results.push(value);
            }
            search_from = abs_start + end_pos + end_tag.len();
        } else {
            break;
        }
    }

    results
}

fn trim_opt(s: Option<String>) -> Option<String> {
    s.map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

/// Render commit message XML with structured visual output.
pub fn render(content: &str) -> String {
    let mut output = String::new();

    output.push_str("\n╔═══ Commit Message ═══╗\n\n");

    // Skip path
    let skip_reason = trim_opt(extract_tag_content(content, "ralph-skip"));
    if let Some(reason) = skip_reason {
        writeln!(output, "⏭  Skip: {reason}").unwrap();
        return output;
    }

    let subject = trim_opt(extract_tag_content(content, "ralph-subject"));

    // Fallback: no parseable content at all
    if subject.is_none() {
        output.push_str("⚠️  Unable to parse commit message XML\n\n");
        output.push_str(content);
        return output;
    }

    // Subject
    if let Some(ref s) = subject {
        writeln!(output, "  {s}").unwrap();
    }

    // Body — simple variant
    let body = trim_opt(extract_tag_content(content, "ralph-body"));
    if let Some(ref b) = body {
        output.push('\n');
        for line in b.lines() {
            let trimmed = line.trim_end();
            if trimmed.is_empty() {
                output.push('\n');
            } else {
                writeln!(output, "  {trimmed}").unwrap();
            }
        }
    }

    // Body — detailed variant (summary / details / footer)
    let summary = trim_opt(extract_tag_content(content, "ralph-body-summary"));
    let details = trim_opt(extract_tag_content(content, "ralph-body-details"));
    let footer = trim_opt(extract_tag_content(content, "ralph-body-footer"));

    if let Some(ref s) = summary {
        output.push('\n');
        for line in s.lines() {
            writeln!(output, "  {}", line.trim_end()).unwrap();
        }
    }

    if let Some(ref d) = details {
        output.push('\n');
        for line in d.lines() {
            let trimmed = line.trim_end();
            if trimmed.is_empty() {
                output.push('\n');
            } else {
                writeln!(output, "  {trimmed}").unwrap();
            }
        }
    }

    if let Some(ref f) = footer {
        output.push('\n');
        for line in f.lines() {
            writeln!(output, "  {}", line.trim_end()).unwrap();
        }
    }

    // Selected files section
    let files = extract_all_tag_content(content, "ralph-file");
    if !files.is_empty() {
        output.push('\n');
        writeln!(output, "  Selected files ({}):", files.len()).unwrap();
        for file in &files {
            writeln!(output, "    · {file}").unwrap();
        }
    }

    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_commit_with_subject_and_body() {
        let xml = r"<ralph-commit>
<ralph-subject>feat: add new authentication system</ralph-subject>
<ralph-body>This commit introduces a new JWT-based authentication system.

- Added auth middleware
- Created user session management
- Updated API endpoints</ralph-body>
</ralph-commit>";

        let output = render(xml);

        assert!(
            output.contains("Commit Message"),
            "Should have commit header"
        );
        assert!(
            output.contains("feat: add new authentication"),
            "Should show subject"
        );
        assert!(
            output.contains("JWT-based authentication"),
            "Should show body"
        );
        assert!(
            output.contains("Added auth middleware"),
            "Should show body details"
        );
    }

    #[test]
    fn test_render_commit_subject_only() {
        let xml = r"<ralph-commit>
<ralph-subject>fix: resolve null pointer exception</ralph-subject>
</ralph-commit>";

        let output = render(xml);

        assert!(
            output.contains("fix: resolve null pointer"),
            "Should show subject"
        );
    }

    #[test]
    fn test_render_commit_falls_back_to_raw_with_warning_when_subject_is_blank() {
        let xml = r"<ralph-commit>
<ralph-subject>   </ralph-subject>
</ralph-commit>";

        let output = render(xml);

        assert!(output.contains("⚠️"), "Should warn on parse failure");
        assert!(
            output.contains("<ralph-commit>"),
            "Should include raw XML fallback"
        );
        assert!(
            !output.contains("📝 \n"),
            "Should not render an empty subject line"
        );
    }

    #[test]
    fn test_all_renderers_have_header_boxes() {
        // Verify commit message has box-drawing characters
        let commit_output = render("<ralph-commit>invalid</ralph-commit>");
        assert!(commit_output.contains("═"), "Commit should have box header");
    }

    #[test]
    fn test_render_commit_skip_path() {
        let xml = r"<ralph-commit>
<ralph-skip>No changes to commit</ralph-skip>
</ralph-commit>";

        let output = render(xml);

        assert!(
            output.contains("No changes to commit"),
            "Should show skip reason"
        );
        assert!(output.contains("⏭"), "Should show skip indicator");
        assert!(!output.contains("⚠️"), "Should not show warning on skip");
    }

    #[test]
    fn test_render_commit_with_detailed_body_tags() {
        let xml = r"<ralph-commit>
<ralph-subject>feat(auth): add OAuth2 login</ralph-subject>
<ralph-body-summary>Adds OAuth2 login flow.</ralph-body-summary>
<ralph-body-details>- Added token exchange
- Configured callback URL</ralph-body-details>
<ralph-body-footer>Fixes #42</ralph-body-footer>
</ralph-commit>";

        let output = render(xml);

        assert!(
            output.contains("feat(auth): add OAuth2 login"),
            "Should show subject"
        );
        assert!(
            output.contains("Adds OAuth2 login flow."),
            "Should show summary"
        );
        assert!(
            output.contains("Added token exchange"),
            "Should show details"
        );
        assert!(output.contains("Fixes #42"), "Should show footer");
    }

    #[test]
    fn test_render_commit_with_selected_files() {
        let xml = r"<ralph-commit>
<ralph-subject>fix(auth): prevent token expiry race</ralph-subject>
<ralph-files>
  <ralph-file>src/auth/token.rs</ralph-file>
  <ralph-file>tests/auth/token_test.rs</ralph-file>
</ralph-files>
</ralph-commit>";

        let output = render(xml);

        assert!(
            output.contains("fix(auth): prevent token expiry race"),
            "Should show subject"
        );
        assert!(
            output.contains("Selected files (2)"),
            "Should show file count"
        );
        assert!(
            output.contains("src/auth/token.rs"),
            "Should list first file"
        );
        assert!(
            output.contains("tests/auth/token_test.rs"),
            "Should list second file"
        );
    }
}
