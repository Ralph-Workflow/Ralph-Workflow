//! Commit message XML renderer.
//!
//! Renders commit message XML with structured visual output:
//! - Box-drawing header and closing border
//! - Subject line prominently displayed
//! - All body variants: simple body (labeled "Body"), summary/details/footer (labeled sections)
//! - Skip path with reason
//! - Staged files list when `ralph-files` is present

use super::helpers::extract_tag_content;
use std::fmt::Write;

/// Box width for the commit message display (characters).
const BOX_WIDTH: usize = 47;

/// Section separator character.
const SEPARATOR_CHAR: char = '─';

/// Extract all occurrences of a tag from XML content.
#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked index arithmetic"
)]
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

/// Write a section label and divider line.
fn write_section_header(output: &mut String, label: &str) {
    let separator = SEPARATOR_CHAR.to_string().repeat(BOX_WIDTH - 2);
    writeln!(output, "\n  {label}").unwrap();
    writeln!(output, "  {separator}").unwrap();
}

/// Write the closing border.
fn write_closing_border(output: &mut String) {
    let inner = "═".repeat(BOX_WIDTH - 2);
    writeln!(output, "╚{inner}╝").unwrap();
}

/// Render commit message XML with structured visual output.
pub fn render(content: &str) -> String {
    let mut output = String::new();

    // Opening border — pad header to fill BOX_WIDTH
    // "╔═══ Commit Message ══...══╗"
    let header_label = " Commit Message ";
    let total_inner = BOX_WIDTH - 2; // width between ╔ and ╗
    let label_len = header_label.len();
    let prefix_fill = 3usize; // "═══" before label
    let suffix_fill = total_inner.saturating_sub(prefix_fill + label_len);
    let prefix = "═".repeat(prefix_fill);
    let suffix = "═".repeat(suffix_fill);
    writeln!(output, "\n╔{prefix}{header_label}{suffix}╗").unwrap();
    output.push('\n');

    // Skip path
    let skip_reason = trim_opt(extract_tag_content(content, "ralph-skip"));
    if let Some(reason) = skip_reason {
        writeln!(output, "  ⏭  Skip: {reason}").unwrap();
        output.push('\n');
        write_closing_border(&mut output);
        return output;
    }

    let subject = trim_opt(extract_tag_content(content, "ralph-subject"));

    // Fallback: no parseable content at all
    if subject.is_none() {
        output.push_str("  ⚠️  Unable to parse commit message XML\n\n");
        output.push_str(content);
        output.push('\n');
        write_closing_border(&mut output);
        return output;
    }

    // Subject
    if let Some(ref s) = subject {
        writeln!(output, "  {s}").unwrap();
    }

    // Body — simple variant
    let body = trim_opt(extract_tag_content(content, "ralph-body"));
    if let Some(ref b) = body {
        write_section_header(&mut output, "Body");
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
        write_section_header(&mut output, "Summary");
        for line in s.lines() {
            writeln!(output, "  {}", line.trim_end()).unwrap();
        }
    }

    if let Some(ref d) = details {
        write_section_header(&mut output, "Details");
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
        write_section_header(&mut output, "Footer");
        for line in f.lines() {
            writeln!(output, "  {}", line.trim_end()).unwrap();
        }
    }

    // Staged files section
    let files = extract_all_tag_content(content, "ralph-file");
    if !files.is_empty() {
        write_section_header(&mut output, &format!("Staged Files ({})", files.len()));
        for file in &files {
            writeln!(output, "  · {file}").unwrap();
        }
    }

    output.push('\n');
    write_closing_border(&mut output);
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
    fn test_render_commit_with_staged_files() {
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
            output.contains("Staged Files (2)"),
            "Should show staged file count with new label"
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

    #[test]
    fn test_render_commit_has_closing_border() {
        let xml = r"<ralph-commit>
<ralph-subject>docs: clarify API authentication flow</ralph-subject>
</ralph-commit>";

        let output = render(xml);

        assert!(output.contains('╚'), "Should have closing border ╚");
        assert!(output.contains('╝'), "Should have closing border ╝");
    }

    #[test]
    fn test_render_commit_skip_path_has_closing_border() {
        let xml = r"<ralph-commit>
<ralph-skip>No changes to commit</ralph-skip>
</ralph-commit>";

        let output = render(xml);

        assert!(
            output.contains('╚'),
            "Skip path should have closing border ╚"
        );
        assert!(
            output.contains('╝'),
            "Skip path should have closing border ╝"
        );
    }

    #[test]
    fn test_render_commit_body_has_section_label() {
        let xml = r"<ralph-commit>
<ralph-subject>fix: resolve null pointer in user lookup</ralph-subject>
<ralph-body>Adds nil check before dereferencing user pointer.</ralph-body>
</ralph-commit>";

        let output = render(xml);

        assert!(
            output.contains("Body"),
            "Simple body should show 'Body' label"
        );
        assert!(
            output.contains(SEPARATOR_CHAR),
            "Should have section divider"
        );
    }

    #[test]
    fn test_render_commit_detailed_body_has_section_labels() {
        let xml = r"<ralph-commit>
<ralph-subject>feat(auth): add OAuth2 login</ralph-subject>
<ralph-body-summary>Adds OAuth2 login flow with Google and GitHub.</ralph-body-summary>
<ralph-body-details>· Added token exchange endpoint
· Configured OAuth callback URL</ralph-body-details>
<ralph-body-footer>Fixes #42</ralph-body-footer>
</ralph-commit>";

        let output = render(xml);

        assert!(output.contains("Summary"), "Should show 'Summary' label");
        assert!(output.contains("Details"), "Should show 'Details' label");
        assert!(output.contains("Footer"), "Should show 'Footer' label");
        assert!(
            output.contains(SEPARATOR_CHAR),
            "Should have section dividers"
        );
    }

    #[test]
    fn test_render_commit_files_section_label() {
        let xml = r"<ralph-commit>
<ralph-subject>fix(auth): prevent token expiry race</ralph-subject>
<ralph-files>
  <ralph-file>src/auth/token.rs</ralph-file>
  <ralph-file>tests/auth/token_test.rs</ralph-file>
</ralph-files>
</ralph-commit>";

        let output = render(xml);

        assert!(
            output.contains("Staged Files (2)"),
            "Should show 'Staged Files (2)' label, not 'Selected files'"
        );
        assert!(
            !output.contains("Selected files"),
            "Should not use old 'Selected files' label"
        );
        assert!(
            output.contains(SEPARATOR_CHAR),
            "Should have section divider"
        );
    }

    #[test]
    fn test_render_commit_divider_lines_present_when_body_exists() {
        let xml = r"<ralph-commit>
<ralph-subject>fix: something</ralph-subject>
<ralph-body>Some body text here.</ralph-body>
</ralph-commit>";

        let output = render(xml);

        assert!(
            output.contains(SEPARATOR_CHAR),
            "Body section should have divider line"
        );
    }

    #[test]
    fn test_render_commit_subject_only_no_divider() {
        let xml = r"<ralph-commit>
<ralph-subject>docs: clarify API authentication flow</ralph-subject>
</ralph-commit>";

        let output = render(xml);

        assert!(
            !output.contains(SEPARATOR_CHAR),
            "Subject-only output must NOT have section dividers"
        );
    }

    #[test]
    fn test_render_commit_fallback_has_closing_border() {
        let xml = r"<ralph-commit>
<ralph-subject>   </ralph-subject>
</ralph-commit>";

        let output = render(xml);

        assert!(
            output.contains('╚'),
            "Fallback path should have closing border"
        );
        assert!(
            output.contains('╝'),
            "Fallback path should have closing border"
        );
    }
}
