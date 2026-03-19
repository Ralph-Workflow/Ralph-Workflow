//! Tests for delta display formatting methods.
//!
//! This module tests the `DeltaDisplayFormatter`'s `format_thinking` and
//! `format_tool_input` methods across different terminal modes.

use super::*;

#[test]
fn test_format_thinking_content() {
    let formatter = DeltaDisplayFormatter::new();
    let output = formatter.format_thinking(
        "Thinking about this",
        "Claude",
        test_colors(),
        TerminalMode::Full,
    );
    assert!(output.contains("Thinking"));
    assert!(output.contains("Thinking about this"));
}

#[test]
fn test_format_thinking_none_mode_plain_text() {
    let formatter = DeltaDisplayFormatter::new();
    let output = formatter.format_thinking(
        "Thinking about this",
        "Claude",
        Colors { enabled: true },
        TerminalMode::None,
    );
    assert_eq!(output, "[Claude] Thinking: Thinking about this\n");
}

#[test]
fn test_format_tool_input() {
    let formatter = DeltaDisplayFormatter::new();
    let output = formatter.format_tool_input(
        "command=ls -la",
        "Claude",
        test_colors(),
        TerminalMode::Full,
    );
    assert!(output.contains("command=ls -la"));
    assert!(output.contains("└─"));
}

#[test]
fn test_format_tool_input_none_mode_suppressed() {
    let formatter = DeltaDisplayFormatter::new();
    let output = formatter.format_tool_input(
        "command=ls -la",
        "Claude",
        test_colors(),
        TerminalMode::None,
    );
    // Per-delta tool input is suppressed in None mode to prevent spam.
    // Tool input will be flushed once at completion boundaries.
    assert_eq!(output, "");
}

#[test]
fn test_format_tool_input_basic_mode_suppressed() {
    let formatter = DeltaDisplayFormatter::new();
    let output = formatter.format_tool_input(
        "command=ls -la",
        "Claude",
        test_colors(),
        TerminalMode::Basic,
    );
    // Per-delta tool input is suppressed in Basic mode to prevent spam.
    // Tool input will be flushed once at completion boundaries.
    assert_eq!(output, "");
}
