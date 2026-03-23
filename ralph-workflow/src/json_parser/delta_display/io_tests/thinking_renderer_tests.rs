//! Tests for `ThinkingDeltaRenderer` implementation.

use super::*;

#[test]
fn test_thinking_delta_renderer_first_delta_full_mode() {
    let colors = test_colors();
    let out = ThinkingDeltaRenderer::render_first_delta(
        "git status",
        "ccs/codex",
        colors,
        TerminalMode::Full,
    );
    assert!(out.contains("[ccs/codex]"));
    assert!(out.contains("Thinking:"));
    assert!(out.contains("git status"));
    // Append-only pattern: no newline, no cursor positioning
    assert!(!out.contains('\n'));
    assert!(!out.contains("\x1b[1A"));
}

#[test]
fn test_thinking_delta_renderer_subsequent_delta_full_mode() {
    let colors = test_colors();
    let out = ThinkingDeltaRenderer::render_subsequent_delta(
        "git status --porcelain",
        "ccs/codex",
        colors,
        TerminalMode::Full,
    );
    // DEPRECATED: render_subsequent_delta returns empty string in Full mode
    // Parsers MUST compute suffix themselves and emit directly (append-only pattern)
    assert_eq!(out, "");
}

#[test]
fn test_thinking_delta_renderer_basic_mode_no_cursor_sequences() {
    let colors = test_colors();
    let out = ThinkingDeltaRenderer::render_first_delta(
        "git status",
        "ccs/codex",
        colors,
        TerminalMode::Basic,
    );
    // Per-delta thinking output is suppressed in Basic mode to prevent spam.
    // Content will be flushed once at completion boundaries.
    assert_eq!(out, "");
}

#[test]
fn test_thinking_delta_renderer_first_delta_none_mode() {
    let colors = test_colors();
    let out = ThinkingDeltaRenderer::render_first_delta(
        "git status",
        "ccs/codex",
        colors,
        TerminalMode::None,
    );
    // Per-delta thinking output is suppressed in None mode to prevent spam.
    // Content will be flushed once at completion boundaries.
    assert_eq!(out, "");
}

#[test]
fn test_thinking_delta_renderer_subsequent_delta_none_mode() {
    let colors = test_colors();
    let out = ThinkingDeltaRenderer::render_subsequent_delta(
        "git status --porcelain",
        "ccs/codex",
        colors,
        TerminalMode::None,
    );
    // Per-delta thinking output is suppressed in None mode to prevent spam.
    // Content will be flushed once at completion boundaries.
    assert_eq!(out, "");
}

#[test]
fn test_thinking_delta_renderer_subsequent_delta_basic_mode() {
    let colors = test_colors();
    let out = ThinkingDeltaRenderer::render_subsequent_delta(
        "git status --porcelain",
        "ccs/codex",
        colors,
        TerminalMode::Basic,
    );
    // Per-delta thinking output is suppressed in Basic mode to prevent spam.
    // Content will be flushed once at completion boundaries.
    assert_eq!(out, "");
}
