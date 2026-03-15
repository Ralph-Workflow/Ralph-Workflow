//! Development result XML renderer.
//!
//! Renders development result XML with semantic formatting:
//! - Header with box-drawing characters
//! - Status with emoji indicator and label
//! - Summary description with proper indentation
//! - Files changed with action type indicators
//! - Next steps if present

use super::helpers::{
    parse_files_changed_list, parse_unified_diff_files, render_diff_sections, ChangeAction,
};
use crate::files::llm_output_extraction::validate_development_result_xml;
use crate::reducer::ui_event::XmlOutputContext;
use std::fmt::Write;

/// Render development result XML with semantic formatting.
pub fn render(content: &str, output_context: Option<&XmlOutputContext>) -> String {
    let mut output = String::new();

    // Header with optional iteration context
    if let Some(ctx) = output_context {
        if let Some(iter) = ctx.iteration {
            writeln!(output, "\n╔═══ Development Iteration {iter} ═══╗\n").unwrap();
        }
    }

    if let Ok(elements) = validate_development_result_xml(content) {
        // Status with emoji and label
        let (status_emoji, status_label) = match elements.status.as_str() {
            "completed" => ("✅", "Completed"),
            "partial" => ("🔄", "In Progress"),
            "failed" => ("❌", "Failed"),
            _ => ("❓", "Unknown"),
        };
        writeln!(output, "{status_emoji} Status: {status_label}\n").unwrap();

        // Summary with proper formatting for multiline
        output.push_str("📋 Summary:\n");
        for line in elements.summary.lines() {
            writeln!(output, "   {line}").unwrap();
        }

        // Skills & MCP recommendations (if present)
        if let Some(ref sm) = elements.skills_mcp {
            let has_structured = !sm.skills.is_empty() || !sm.mcps.is_empty();
            if has_structured || sm.raw_content.is_some() {
                output.push_str("\n🛠️  Skills & MCP Recommendations:\n");
                for skill in &sm.skills {
                    if let Some(ref reason) = skill.reason {
                        writeln!(output, "   - skill: {} \u{2014} {}", skill.name, reason).unwrap();
                    } else {
                        writeln!(output, "   - skill: {}", skill.name).unwrap();
                    }
                }
                for mcp in &sm.mcps {
                    if let Some(ref reason) = mcp.reason {
                        writeln!(output, "   - mcp: {} \u{2014} {}", mcp.name, reason).unwrap();
                    } else {
                        writeln!(output, "   - mcp: {}", mcp.name).unwrap();
                    }
                }
                if let Some(ref raw) = sm.raw_content {
                    let trimmed: &str = raw.trim();
                    if !trimmed.is_empty() && !has_structured {
                        writeln!(output, "   {trimmed}").unwrap();
                    }
                }
            }
        }

        // Files changed: prefer diff-like rendering when unified diff is present.
        if let Some(ref files) = elements.files_changed {
            output.push_str(&render_files_changed_as_diff_like_view(files));
        }

        // Next steps with proper formatting
        if let Some(ref next) = elements.next_steps {
            output.push_str("\n➡️  Next Steps:\n");
            for line in next.lines() {
                writeln!(output, "   {line}").unwrap();
            }
        }
    } else {
        output.push_str("⚠️  Unable to parse development result XML\n\n");
        output.push_str(content);
    }

    output
}

fn render_files_changed_as_diff_like_view(files_changed: &str) -> String {
    let trimmed = files_changed.trim();
    if trimmed.is_empty() {
        return String::new();
    }

    if trimmed.contains("diff --git ") {
        let sections = parse_unified_diff_files(trimmed);
        return render_diff_sections("📁 Files Changed", &sections);
    }

    let items = parse_files_changed_list(trimmed);
    if items.is_empty() {
        return String::new();
    }

    let file_list: Vec<&str> = items.iter().map(|(p, _)| p.as_str()).collect();
    let mut output = String::new();
    output.push_str("\n📁 Files Changed:\n");
    writeln!(
        output,
        "   Modified {} file(s): {}",
        file_list.len(),
        file_list.join(", ")
    )
    .unwrap();

    for (path, action) in items {
        writeln!(output, "\n   📄 {path}").unwrap();
        writeln!(
            output,
            "      Action: {}",
            match action {
                ChangeAction::Create => "created",
                ChangeAction::Modify => "modified",
                ChangeAction::Delete => "deleted",
            }
        )
        .unwrap();
        output.push_str("      (no diff provided)\n");
    }

    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_development_result_completed() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Implemented feature X</ralph-summary>
<ralph-files-changed>src/main.rs
src/lib.rs</ralph-files-changed>
</ralph-development-result>";

        let output = render(xml, None);

        assert!(output.contains("✅"), "Should have completed emoji");
        assert!(
            output.contains("Completed"),
            "Should show friendly status label"
        );
        assert!(
            output.contains("Implemented feature X"),
            "Should show summary"
        );
        assert!(output.contains("src/main.rs"), "Should list files");
    }

    #[test]
    fn test_render_development_result_renders_diff_like_view_per_file_when_diff_present() {
        let xml = r#"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Updated two files</ralph-summary>
<ralph-files-changed>diff --git a/src/main.rs b/src/main.rs
index 1111111..2222222 100644
--- a/src/main.rs
+++ b/src/main.rs
@@ -1,2 +1,2 @@
-fn main() { println!("old"); }
+fn main() { println!("new"); }
diff --git a/src/lib.rs b/src/lib.rs
new file mode 100644
--- /dev/null
+++ b/src/lib.rs
@@ -0,0 +1,1 @@
+pub fn hello() {}
</ralph-files-changed>
</ralph-development-result>"#;

        let output = render(xml, None);

        assert!(
            output.contains("Modified 2 file") || output.contains("2 file"),
            "Should include file count summary"
        );
        assert!(
            output.contains("src/main.rs") && output.contains("src/lib.rs"),
            "Should include per-file headers"
        );
        assert!(
            output.contains("--- a/src/main.rs") && output.contains("+++ b/src/main.rs"),
            "Should include diff markers"
        );
        assert!(
            output.contains("+pub fn hello") || output.contains("pub fn hello"),
            "Should include diff content"
        );
    }

    #[test]
    fn test_render_development_result_partial() {
        let xml = r"<ralph-development-result>
<ralph-status>partial</ralph-status>
<ralph-summary>Started work on feature</ralph-summary>
<ralph-next-steps>Continue with implementation</ralph-next-steps>
</ralph-development-result>";

        let output = render(xml, None);

        assert!(output.contains("🔄"), "Should have partial emoji");
        assert!(
            output.contains("Continue with implementation"),
            "Should show next steps"
        );
    }

    #[test]
    fn test_render_development_result_with_iteration() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Done</ralph-summary>
</ralph-development-result>";

        let ctx = Some(XmlOutputContext {
            iteration: Some(2),
            pass: None,
            snippets: Vec::new(),
        });
        let output = render(xml, ctx.as_ref());

        assert!(
            output.contains("Development Iteration 2"),
            "Should show iteration number"
        );
    }

    #[test]
    fn test_render_development_result_malformed_fallback() {
        let bad_xml = "not valid xml at all";
        let output = render(bad_xml, None);

        assert!(output.contains("⚠️"), "Should show warning");
        assert!(
            output.contains("not valid xml"),
            "Should include raw content"
        );
    }

    #[test]
    fn test_development_result_multiline_summary() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>First line of summary
Second line of summary
Third line of summary</ralph-summary>
</ralph-development-result>";

        let output = render(xml, None);
        assert!(
            output.contains("First line"),
            "Should show first line of summary"
        );
        assert!(
            output.contains("Second line"),
            "Should show second line of summary"
        );
        assert!(
            output.contains("Third line"),
            "Should show third line of summary"
        );
    }

    #[test]
    fn test_development_result_file_action_icons() {
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Changes made</ralph-summary>
<ralph-files-changed>src/new_file.rs (created)
src/existing.rs
src/old.rs (deleted)</ralph-files-changed>
</ralph-development-result>";

        let output = render(xml, None);
        assert!(
            output.contains("src/new_file.rs") && output.contains("Action: created"),
            "Should show created action for new file"
        );
        assert!(
            output.contains("src/old.rs") && output.contains("Action: deleted"),
            "Should show deleted action for removed file"
        );
        assert!(
            output.contains("src/existing.rs") && output.contains("Action: modified"),
            "Should show modified action for existing file"
        );
    }
}
