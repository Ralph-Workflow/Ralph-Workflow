//! Shared helpers for XML renderers.
//!
//! This module contains utilities used by multiple XML renderer modules.

/// Action type for file changes.
use std::fmt::Write;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChangeAction {
    Create,
    Modify,
    Delete,
}

/// A section of a unified diff for a single file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiffFileSection {
    pub path: String,
    pub action: ChangeAction,
    pub diff: String,
}

/// Extract text content from an XML tag.
///
/// Simple extraction for well-formed tags. Returns None if tag not found.
#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked index arithmetic"
)]
pub fn extract_tag_content(content: &str, tag_name: &str) -> Option<String> {
    let start_tag = format!("<{tag_name}>");
    let end_tag = format!("</{tag_name}>");

    let start_pos = content.find(&start_tag)?;
    let content_start = start_pos + start_tag.len();
    let end_pos = content[content_start..].find(&end_tag)?;

    Some(content[content_start..content_start + end_pos].to_string())
}

/// Parse unified diff format into per-file sections.
pub fn parse_unified_diff_files(diff: &str) -> Vec<DiffFileSection> {
    let (sections, current) = diff.lines().fold(
        (Vec::new(), Vec::new()),
        |(mut sections, mut current), line| {
            if line.starts_with("diff --git ") {
                if !current.is_empty() {
                    sections.push(current);
                }
                current = vec![line];
            } else if !current.is_empty() {
                current.push(line);
            }
            (sections, current)
        },
    );

    // Push the final section if non-empty
    let sections = if current.is_empty() {
        sections
    } else {
        sections
            .into_iter()
            .chain(std::iter::once(current))
            .collect()
    };

    sections
        .into_iter()
        .filter_map(|lines| parse_diff_section(&lines))
        .collect()
}

/// Parse a single diff section into a `DiffFileSection`.
fn parse_diff_section(lines: &[&str]) -> Option<DiffFileSection> {
    let header = *lines.first()?;
    // Example: "diff --git a/src/main.rs b/src/main.rs"
    let mut parts = header.split_whitespace();
    let _ = parts.next()?; // diff
    let _ = parts.next()?; // --git
    let a_path = parts.next()?.trim();
    let b_path = parts.next()?.trim();

    let path = if b_path == "/dev/null" {
        a_path
    } else {
        b_path
    }
    .trim_start_matches("a/")
    .trim_start_matches("b/")
    .to_string();

    let action = lines
        .iter()
        .find_map(|line| {
            if line.starts_with("new file mode ") {
                Some(ChangeAction::Create)
            } else if line.starts_with("deleted file mode ") {
                Some(ChangeAction::Delete)
            } else {
                None
            }
        })
        .unwrap_or(ChangeAction::Modify);

    Some(DiffFileSection {
        path,
        action,
        diff: lines.join("\n"),
    })
}

/// Render diff sections with a title.
pub fn render_diff_sections(title: &str, sections: &[DiffFileSection]) -> String {
    if sections.is_empty() {
        return String::new();
    }

    let file_list = sections
        .iter()
        .map(|s| s.path.as_str())
        .collect::<Vec<&str>>()
        .join(", ");

    let sections_output = sections.iter().fold(String::new(), |mut acc, section| {
        let action_str = match section.action {
            ChangeAction::Create => "created",
            ChangeAction::Modify => "modified",
            ChangeAction::Delete => "deleted",
        };
        let diff_lines = section
            .diff
            .lines()
            .map(|line| format!("      {line}"))
            .collect::<Vec<_>>()
            .join("\n");
        let _ = writeln!(acc, "\n   📄 {}", section.path);
        let _ = writeln!(acc, "      Action: {}", action_str);
        let _ = writeln!(acc, "{}", diff_lines);
        acc
    });

    format!(
        "\n{title}:\n   Modified {} file(s): {}{sections_output}",
        sections.len(),
        file_list
    )
}

/// Parse a simple file list into file paths with actions.
pub fn parse_files_changed_list(files: &str) -> Vec<(String, ChangeAction)> {
    files
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(|l| l.trim_start_matches("- ").trim())
        .map(|l| {
            let lowered = l.to_ascii_lowercase();
            let action = if lowered.contains("(created)") || lowered.contains("(new)") {
                ChangeAction::Create
            } else if lowered.contains("(deleted)") || lowered.contains("(removed)") {
                ChangeAction::Delete
            } else {
                ChangeAction::Modify
            };
            let path = l.split_once(" (").map_or(l, |(p, _)| p).trim().to_string();
            (path, action)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_tag_content_found() {
        let xml = "<ralph-subject>Hello World</ralph-subject>";
        let result = extract_tag_content(xml, "ralph-subject");
        assert_eq!(result, Some("Hello World".to_string()));
    }

    #[test]
    fn test_extract_tag_content_not_found() {
        let xml = "<other>content</other>";
        let result = extract_tag_content(xml, "ralph-subject");
        assert!(result.is_none());
    }

    #[test]
    fn test_extract_tag_content_nested() {
        let xml = "<outer><ralph-subject>Nested</ralph-subject></outer>";
        let result = extract_tag_content(xml, "ralph-subject");
        assert_eq!(result, Some("Nested".to_string()));
    }

    #[test]
    fn test_parse_unified_diff_files_single() {
        let diff = r#"diff --git a/src/main.rs b/src/main.rs
index 1111111..2222222 100644
--- a/src/main.rs
+++ b/src/main.rs
@@ -1 +1 @@
-fn main() {}
+fn main() { println!("hello"); }"#;

        let sections = parse_unified_diff_files(diff);
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].path, "src/main.rs");
        assert_eq!(sections[0].action, ChangeAction::Modify);
    }

    #[test]
    fn test_parse_unified_diff_files_new_file() {
        let diff = r"diff --git a/src/new.rs b/src/new.rs
new file mode 100644
--- /dev/null
+++ b/src/new.rs
@@ -0,0 +1 @@
+fn new() {}";

        let sections = parse_unified_diff_files(diff);
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].path, "src/new.rs");
        assert_eq!(sections[0].action, ChangeAction::Create);
    }

    #[test]
    fn test_parse_unified_diff_files_deleted() {
        let diff = r"diff --git a/src/old.rs b/src/old.rs
deleted file mode 100644
--- a/src/old.rs
+++ /dev/null
@@ -1 +0,0 @@
-fn old() {}";

        let sections = parse_unified_diff_files(diff);
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].path, "src/old.rs");
        assert_eq!(sections[0].action, ChangeAction::Delete);
    }

    #[test]
    fn test_parse_files_changed_list_basic() {
        let files = r"src/main.rs
src/lib.rs (created)
src/old.rs (deleted)";

        let result = parse_files_changed_list(files);
        assert_eq!(result.len(), 3);
        assert_eq!(result[0], ("src/main.rs".to_string(), ChangeAction::Modify));
        assert_eq!(result[1], ("src/lib.rs".to_string(), ChangeAction::Create));
        assert_eq!(result[2], ("src/old.rs".to_string(), ChangeAction::Delete));
    }
}
