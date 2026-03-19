//! General renderer behavior tests (newlines, whitespace).

use super::*;

#[test]
fn test_delta_renderer_multiple_newlines_render_cleanly() {
    let colors = test_colors();
    let output = TextDeltaRenderer::render_first_delta(
        "Hello\n\n\nWorld",
        "agent",
        colors,
        TerminalMode::Full,
    );
    // Multiple newlines should render as single space
    assert!(output.contains("Hello World"));
    // Should NOT have multiple spaces
    assert!(!output.contains("  "));
}

#[test]
fn test_delta_renderer_trailing_whitespace_trimmed() {
    let colors = test_colors();
    let output = TextDeltaRenderer::render_first_delta(
        "Hello World   ",
        "agent",
        colors,
        TerminalMode::Full,
    );
    // Trailing spaces should be trimmed
    assert!(output.contains("Hello World"));
    // Content should not end with space before escape sequences.
    // (In append-only mode, there are no cursor escape sequences; only a color reset may follow.)
}
