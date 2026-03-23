//! Tests for `TextDeltaRenderer` implementation.
//!
//! This module tests the `TextDeltaRenderer`'s `render_first_delta`,
//! `render_subsequent_delta`, and `render_completion` methods across
//! different terminal modes (Full, None, Basic).

use super::*;

#[test]
fn test_text_delta_renderer_first_delta_full_mode() {
    let output = TextDeltaRenderer::render_first_delta(
        "Hello",
        "ccs-glm",
        test_colors(),
        TerminalMode::Full,
    );
    assert!(output.contains("[ccs-glm]"));
    assert!(output.contains("Hello"));
    // Append-only pattern: NO newline, NO cursor positioning
    assert!(!output.contains('\n'));
    assert!(!output.contains("\x1b[1A"));
    assert!(!output.contains("\x1b[1B"));
}

#[test]
fn test_text_delta_renderer_first_delta_none_mode() {
    let output = TextDeltaRenderer::render_first_delta(
        "Hello",
        "ccs-glm",
        test_colors(),
        TerminalMode::None,
    );
    // Per-delta output is suppressed in None mode to prevent spam.
    // Content will be flushed once at completion boundaries.
    assert_eq!(output, "");
}

#[test]
fn test_text_delta_renderer_first_delta_basic_mode() {
    let output = TextDeltaRenderer::render_first_delta(
        "Hello",
        "ccs-glm",
        test_colors(),
        TerminalMode::Basic,
    );
    // Per-delta output is suppressed in Basic mode to prevent spam.
    // Content will be flushed once at completion boundaries.
    assert_eq!(output, "");
}

#[test]
fn test_text_delta_renderer_subsequent_delta_full_mode() {
    let output = TextDeltaRenderer::render_subsequent_delta(
        "Hello World",
        "ccs-glm",
        test_colors(),
        TerminalMode::Full,
    );
    // DEPRECATED: render_subsequent_delta returns empty string in Full mode
    // Parsers MUST compute suffix themselves and emit directly (append-only pattern)
    // This test verifies the deprecated method doesn't produce output
    assert_eq!(output, "");
}

#[test]
fn test_text_delta_renderer_subsequent_delta_none_mode() {
    let output = TextDeltaRenderer::render_subsequent_delta(
        "Hello World",
        "ccs-glm",
        test_colors(),
        TerminalMode::None,
    );
    // Per-delta output is suppressed in None mode to prevent spam.
    // Content will be flushed once at completion boundaries.
    assert_eq!(output, "");
}

#[test]
fn test_text_delta_renderer_deprecated_subsequent_delta() {
    let output = TextDeltaRenderer::render_subsequent_delta(
        "Hello World",
        "ccs-glm",
        test_colors(),
        TerminalMode::Full,
    );
    // DEPRECATED: This method is no longer used by parsers in Full mode
    // Returns empty string to make incorrect usage visible
    assert_eq!(output, "");
    // No control sequences should be present
    assert!(!output.contains('\r'));
    assert!(!output.contains("\x1b[2K"));
    assert!(!output.contains("\x1b[0K"));
}

#[test]
fn test_text_delta_renderer_completion_full_mode() {
    let output = TextDeltaRenderer::render_completion(TerminalMode::Full);
    // Append-only pattern: just newline, no cursor positioning
    assert!(output.contains('\n'));
    assert!(!output.contains("\x1b[1B"));
    assert!(!output.contains("\x1b[1A"));
    assert_eq!(output, "\n");
}

#[test]
fn test_text_delta_renderer_completion_none_mode() {
    let output = TextDeltaRenderer::render_completion(TerminalMode::None);
    // No completion sequence in None mode.
    // In non-TTY modes, streaming output is suppressed and the parser flushes
    // newline-terminated content at completion boundaries.
    assert!(!output.contains("\x1b[1B"));
    assert_eq!(output, "");
}

#[test]
fn test_text_delta_renderer_completion_basic_mode() {
    let output = TextDeltaRenderer::render_completion(TerminalMode::Basic);
    // No completion sequence in Basic mode.
    // In non-TTY modes, streaming output is suppressed and the parser flushes
    // newline-terminated content at completion boundaries.
    assert!(!output.contains("\x1b[1B"));
    assert_eq!(output, "");
}

#[test]
fn test_text_delta_renderer_sanitizes_newlines() {
    let output = TextDeltaRenderer::render_first_delta(
        "Hello\nWorld",
        "ccs-glm",
        test_colors(),
        TerminalMode::Full,
    );
    // Newlines should be replaced with spaces
    assert!(!output.contains("Hello\nWorld"));
    assert!(output.contains("Hello World"));
}

#[test]
fn test_text_delta_renderer_in_place_update_sequence() {
    let colors = test_colors();

    // First delta - append-only pattern: prefix + content, no newline
    let out1 =
        TextDeltaRenderer::render_first_delta("Hello", "ccs-glm", colors, TerminalMode::Full);
    assert!(out1.contains("[ccs-glm]"));
    assert!(out1.contains("Hello"));
    assert!(!out1.contains('\n'));
    assert!(!out1.contains("\x1b[1A"));

    // Subsequent delta - DEPRECATED: render_subsequent_delta returns empty in Full mode
    // Parsers compute suffix themselves: "Hello" -> "Hello World" emits " World" only
    let out2 = TextDeltaRenderer::render_subsequent_delta(
        "Hello World",
        "ccs-glm",
        colors,
        TerminalMode::Full,
    );
    assert_eq!(out2, "");

    // Completion - just newline
    let out3 = TextDeltaRenderer::render_completion(TerminalMode::Full);
    assert_eq!(out3, "\n");
}
