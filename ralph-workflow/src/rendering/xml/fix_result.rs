//! Fix result XML renderer.
//!
//! Renders fix result XML with semantic formatting:
//! - Box-drawing header with pass number
//! - Status with emoji indicator and friendly label
//! - Summary with proper multiline formatting

use super::helpers::{parse_unified_diff_files, render_diff_sections};
use crate::files::llm_output_extraction::validate_fix_result_xml;
use crate::reducer::ui_event::XmlOutputContext;

/// Render fix result XML with semantic formatting.
pub fn render(content: &str, output_context: Option<&XmlOutputContext>) -> String {
    let header = output_context
        .and_then(|ctx| ctx.pass)
        .map(|pass| format!("\n╔═══ Fix Pass {pass} ═══╗\n"))
        .unwrap_or_default();

    if let Ok(elements) = validate_fix_result_xml(content) {
        let (emoji, label): (&str, &str) = match elements.status.as_str() {
            "all_issues_addressed" => ("✅", "All Issues Addressed"),
            "issues_remain" => ("🔄", "Issues Remain"),
            "no_issues_found" => ("✨", "No Issues Found"),
            _ => ("❓", elements.status.as_str()),
        };

        let summary_part = elements.summary.as_ref().map_or(String::new(), |summary| {
            if summary.contains("diff --git ") {
                let sections = parse_unified_diff_files(summary);
                format!(
                    "\n📋 Summary:\n{}",
                    render_diff_sections("   Changes", &sections)
                )
            } else {
                let summary_lines: String =
                    summary.lines().map(|line| format!("   {line}\n")).collect();
                format!("\n📋 Summary:\n{summary_lines}")
            }
        });

        format!("{header}{emoji} Status: {label}{summary_part}")
    } else {
        format!("{header}⚠️  Unable to parse fix result XML\n\n{content}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_fix_result_all_addressed() {
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summary>Fixed all 3 reported issues</ralph-summary>
</ralph-fix-result>";

        let ctx = Some(XmlOutputContext {
            iteration: None,
            pass: Some(2),
            snippets: Vec::new(),
        });
        let output = render(xml, ctx.as_ref());

        assert!(output.contains("Fix Pass 2"), "Should show pass number");
        assert!(output.contains("✅"), "Should show success emoji");
        assert!(
            output.contains("All Issues Addressed"),
            "Should show friendly status label"
        );
        assert!(output.contains("Fixed all 3"), "Should show summary");
    }

    #[test]
    fn test_render_fix_result_renders_diff_like_view_when_summary_contains_diff() {
        let xml = r"<ralph-fix-result>
<ralph-status>all_issues_addressed</ralph-status>
<ralph-summary>Applied fix:
diff --git a/src/a.rs b/src/a.rs
deleted file mode 100644
--- a/src/a.rs
+++ /dev/null
@@ -1 +0,0 @@
-fn a() {}
</ralph-summary>
</ralph-fix-result>";

        let output = render(xml, None);

        assert!(
            output.contains("src/a.rs"),
            "Should include per-file header derived from diff"
        );
        assert!(
            output.contains("deleted") || output.contains("Deleted"),
            "Should include action context for deleted file"
        );
        assert!(
            output.contains("--- a/src/a.rs") && output.contains("+++ /dev/null"),
            "Should include diff markers"
        );
    }

    #[test]
    fn test_render_fix_result_issues_remain() {
        let xml = r"<ralph-fix-result>
<ralph-status>issues_remain</ralph-status>
</ralph-fix-result>";

        let output = render(xml, None);

        assert!(output.contains("🔄"), "Should show partial emoji");
        assert!(
            output.contains("Issues Remain"),
            "Should show friendly status label"
        );
    }

    #[test]
    fn test_render_fix_result_no_issues() {
        let xml = r"<ralph-fix-result>
<ralph-status>no_issues_found</ralph-status>
</ralph-fix-result>";

        let output = render(xml, None);

        assert!(output.contains("✨"), "Should show sparkle emoji");
    }
}
