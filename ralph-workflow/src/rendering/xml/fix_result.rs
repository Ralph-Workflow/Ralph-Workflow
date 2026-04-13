//! Fix result renderer.
//!
//! Renders fix result JSON content with semantic formatting:
//! - Box-drawing header with pass number
//! - Status with emoji indicator and friendly label
//! - Summary with proper formatting

use crate::reducer::ui_event::XmlOutputContext;

/// Render fix result JSON content with semantic formatting.
pub(super) fn render(content: &str, output_context: Option<&XmlOutputContext>) -> String {
    let header = output_context
        .and_then(|ctx| ctx.pass)
        .map(|pass| format!("\n╔═══ Fix Pass {pass} ═══╗\n"))
        .unwrap_or_default();

    if let Ok(json) = serde_json::from_str::<serde_json::Value>(content) {
        if let Some(status) = json.get("status").and_then(|s| s.as_str()) {
            let (emoji, label) = match status {
                "all_issues_addressed" | "completed" => ("✅", "All Issues Addressed"),
                "issues_remain" | "partial" => ("🔄", "Issues Remain"),
                "no_issues_found" => ("✨", "No Issues Found"),
                _ => ("❓", status),
            };
            let summary = json
                .get("summary")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .trim();
            return if summary.is_empty() {
                format!("{header}{emoji} {label}\n")
            } else {
                format!("{header}{emoji} {label}\n   {summary}\n")
            };
        }
    }

    format!("{header}{content}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_fix_result_with_pass_header() {
        let ctx = Some(XmlOutputContext {
            iteration: None,
            pass: Some(2),
            snippets: Vec::new(),
        });
        let output = render(r#"{"status":"all_issues_addressed","summary":"done"}"#, ctx.as_ref());

        assert!(output.contains("Fix Pass 2"), "Should show pass number");
        assert!(output.contains("done"), "Should include summary");
        assert!(output.contains("✅"), "Should show completion indicator");
    }

    #[test]
    fn test_render_fix_result_issues_remain() {
        let output = render(r#"{"status":"issues_remain","summary":"needs more work"}"#, None);

        assert!(output.contains("🔄"), "Should show in-progress indicator");
        assert!(output.contains("Issues Remain"), "Should show label");
        assert!(output.contains("needs more work"), "Should include summary");
    }

    #[test]
    fn test_render_fix_result_no_issues_found() {
        let output = render(r#"{"status":"no_issues_found"}"#, None);

        assert!(output.contains("✨"), "Should show sparkle indicator");
        assert!(output.contains("No Issues Found"), "Should show label");
    }

    #[test]
    fn test_render_fix_result_no_context() {
        let output = render(r#"{"status":"all_issues_addressed"}"#, None);

        assert!(output.contains("✅"), "Should show checkmark");
    }

    #[test]
    fn test_render_fix_result_fallback_for_non_json() {
        let output = render("raw content here", None);

        assert_eq!(output, "raw content here");
    }
}
