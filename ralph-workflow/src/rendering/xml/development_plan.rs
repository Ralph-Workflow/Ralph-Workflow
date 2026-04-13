//! Development plan renderer.
//!
//! Renders development plan content with a standard header.

/// Render development plan content with a standard header.
pub(super) fn render(content: &str) -> String {
    let header = "\n╔════════════════════════════════════╗\n\
║      Implementation Plan           ║\n\
╚════════════════════════════════════╝\n\n";

    format!("{header}{content}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_plan_includes_header() {
        let output = render("some plan content");

        assert!(
            output.contains("Implementation Plan"),
            "Should have plan header"
        );
        assert!(
            output.contains("some plan content"),
            "Should include content"
        );
    }

    #[test]
    fn test_render_plan_raw_content_passthrough() {
        let content = "Step 1: Do something\nStep 2: Do something else";
        let output = render(content);

        assert!(output.contains("Step 1"), "Should include raw content");
        assert!(output.contains("Step 2"), "Should include raw content");
    }
}
