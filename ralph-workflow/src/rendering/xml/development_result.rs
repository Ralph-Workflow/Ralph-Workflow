//! Development result renderer.
//!
//! Renders development result JSON content with semantic formatting:
//! - Box-drawing header with iteration number
//! - Status with emoji indicator
//! - Summary description

use crate::reducer::ui_event::XmlOutputContext;

/// Render development result JSON content with semantic formatting.
pub(super) fn render(content: &str, output_context: Option<&XmlOutputContext>) -> String {
    let header = output_context
        .and_then(|ctx| ctx.iteration)
        .map(|iter| format!("\n╔═══ Development Iteration {iter} ═══╗\n"))
        .unwrap_or_default();

    if let Ok(json) = serde_json::from_str::<serde_json::Value>(content) {
        if let Some(status) = json.get("status").and_then(|s| s.as_str()) {
            let (emoji, label) = match status {
                "completed" => ("✅", "Completed"),
                "partial" => ("🔄", "Partial"),
                "issues_found" => ("⚠️", "Issues Found"),
                _ => ("❓", status),
            };
            let summary = json
                .get("summary")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .trim();
            return if summary.is_empty() {
                format!("{header}{emoji} Development {label}\n")
            } else {
                format!("{header}{emoji} Development {label}\n   {summary}\n")
            };
        }
    }

    format!("{header}{content}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_development_result_with_iteration() {
        let ctx = Some(XmlOutputContext {
            iteration: Some(2),
            pass: None,
            snippets: Vec::new(),
        });
        let output = render(r#"{"status":"completed","summary":"some content"}"#, ctx.as_ref());

        assert!(
            output.contains("Development Iteration 2"),
            "Should show iteration number"
        );
        assert!(output.contains("some content"), "Should include summary");
        assert!(output.contains("✅"), "Should show completion indicator");
    }

    #[test]
    fn test_render_development_result_no_context() {
        let output = render(r#"{"status":"partial","summary":"needs more work"}"#, None);

        assert!(output.contains("Partial"), "Should show status label");
        assert!(output.contains("needs more work"), "Should include summary");
    }

    #[test]
    fn test_render_development_result_no_summary() {
        let output = render(r#"{"status":"completed"}"#, None);

        assert!(output.contains("✅"), "Should show checkmark");
        assert!(output.contains("Completed"), "Should show label");
    }

    #[test]
    fn test_render_development_result_fallback_for_non_json() {
        let output = render("raw content here", None);

        assert_eq!(output, "raw content here");
    }
}
