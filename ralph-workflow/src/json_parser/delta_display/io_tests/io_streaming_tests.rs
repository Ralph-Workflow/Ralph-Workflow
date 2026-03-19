//! Tests for streaming sequences and prefix display.

use super::*;

#[test]
fn test_full_streaming_sequence_no_extra_blank_lines() {
    let colors = test_colors();

    // Simulate a full streaming sequence with append-only pattern
    let first = TextDeltaRenderer::render_first_delta("Hello", "agent", colors, TerminalMode::Full);
    let second = TextDeltaRenderer::render_subsequent_delta(
        "Hello World",
        "agent",
        colors,
        TerminalMode::Full,
    );
    let complete = TextDeltaRenderer::render_completion(TerminalMode::Full);

    // First delta: no newline (append-only pattern)
    assert!(!first.contains('\n'));
    assert_eq!(first.matches('\n').count(), 0);

    // Subsequent delta: DEPRECATED - returns empty string in Full mode
    // Parsers compute and emit suffix directly
    assert_eq!(second, "");
    assert_eq!(second.matches('\n').count(), 0);

    // Completion: exactly one newline, no cursor positioning
    assert_eq!(complete, "\n");
    assert_eq!(complete.matches('\n').count(), 1);
}

#[test]
fn test_non_tty_streaming_sequence_simple_output() {
    let colors = test_colors();

    // Simulate a full streaming sequence in None mode
    let first = TextDeltaRenderer::render_first_delta("Hello", "agent", colors, TerminalMode::None);
    let second = TextDeltaRenderer::render_subsequent_delta(
        "Hello World",
        "agent",
        colors,
        TerminalMode::None,
    );
    let complete = TextDeltaRenderer::render_completion(TerminalMode::None);

    // Per-delta output is suppressed in None mode to prevent spam
    assert_eq!(first, "");
    assert_eq!(second, "");

    // Completion: no-op (newline is emitted by the parser's non-TTY flush paths)
    assert_eq!(complete, "");
}

#[test]
fn test_prefix_displayed_on_all_deltas() {
    let colors = test_colors();
    let prefix = "my-agent";

    // First delta shows prefix
    let first = TextDeltaRenderer::render_first_delta("A", prefix, colors, TerminalMode::Full);
    assert!(first.contains(&format!("[{prefix}]")));

    // Subsequent delta: DEPRECATED - render_subsequent_delta returns empty in Full mode
    // With append-only pattern, parsers compute suffix and emit directly (no prefix on subsequent)
    let subsequent =
        TextDeltaRenderer::render_subsequent_delta("AB", prefix, colors, TerminalMode::Full);
    assert_eq!(subsequent, "");
}

// Tests for append-only suffix helper

#[test]
fn test_compute_append_only_suffix_extends_last_rendered() {
    let last = "Hello";
    let current = "Hello World";
    let suffix = compute_append_only_suffix(last, current);
    assert_eq!(suffix, " World");
}

#[test]
fn test_compute_append_only_suffix_snapshot_delta() {
    let last = "Hello World";
    let current = "Hello World!";
    let suffix = compute_append_only_suffix(last, current);
    assert_eq!(suffix, "!");
}

#[test]
fn test_compute_append_only_suffix_discontinuity_does_not_duplicate_full_snapshot() {
    // If the provider sends a replacement (not pure append), the safe behavior is to
    // avoid appending the entire "current" snapshot onto already-rendered content.
    //
    // Example:
    // - rendered so far: "Hello World"
    // - new snapshot:    "Hi"
    // A naive "emit current" strategy would produce "Hello WorldHi" (corrupted).
    //
    // We conservatively emit nothing on discontinuities; the parser can choose to
    // finalize the line and re-render in a new line if desired.
    let last = "Hello World";
    let current = "Hi";
    let suffix = compute_append_only_suffix(last, current);
    assert_eq!(suffix, "");
}

#[test]
fn test_compute_append_only_suffix_first_delta_returns_all() {
    let last = "";
    let current = "Hello";
    let suffix = compute_append_only_suffix(last, current);
    assert_eq!(suffix, "Hello");
}

// Tests for sanitize_for_display helper function
