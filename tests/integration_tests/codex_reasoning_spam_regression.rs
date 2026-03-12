//! Regression test for Codex reasoning spam bug.
//!
//! This test verifies that Codex reasoning streaming does not spam repeated
//! "[ccs/codex] Thinking:" lines in non-TTY output modes (logs).
//!
//! Bug reproduction: When Codex emits lengthy reasoning deltas (e.g., "diff too large"
//! scenarios), each delta was printed as a fresh line instead of updating in-place,
//! causing dozens of repeated "[ccs/codex] Thinking:" lines in logs.
//!
//! Fix: Align Codex reasoning with Claude's approach using `StreamingSession` state
//! tracking and `ThinkingDeltaRenderer` with proper terminal mode awareness.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::config::Verbosity;
use ralph_workflow::json_parser::codex::CodexParser;
use ralph_workflow::json_parser::printer::TestPrinter;
use ralph_workflow::json_parser::terminal::TerminalMode;
use ralph_workflow::logger::Colors;
use ralph_workflow::workspace::MemoryWorkspace;
use std::cell::RefCell;
use std::io::{BufReader, Cursor};
use std::rc::Rc;

#[test]
fn test_codex_reasoning_no_spam_in_non_tty_basic_mode() {
    with_default_timeout(|| {
        // Simulate non-TTY environment (Basic mode: colors but no cursor positioning)
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::Basic);

        // Use the captured reproduction log from the acceptance criteria.
        let log = include_str!("artifacts/example_log.log");
        let reader = BufReader::new(Cursor::new(log));
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // In Basic mode, per-delta output is suppressed.
        // The captured real log should not contain repeated "Thinking:" spam.
        let thinking_count = output.matches("[ccs/codex] Thinking:").count();
        assert!(
            thinking_count <= 1,
            "Expected <= 1 'Thinking:' line in Basic mode, found {thinking_count}. Output:\n{output}"
        );

        // We don't assert the exact reasoning text here; the key regression is that
        // the "Thinking:" prefix isn't repeated many times in non-TTY logs.
        // (Real Codex streams may vary.)
    });
}

#[test]
fn test_codex_reasoning_no_spam_in_non_tty_none_mode() {
    with_default_timeout(|| {
        // Simulate pure non-TTY environment (None mode: no ANSI sequences at all)
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::None);

        // Use the captured reproduction log from the acceptance criteria.
        let log = include_str!("artifacts/example_log.log");
        let reader = BufReader::new(Cursor::new(log));
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // In None mode, per-delta output is also suppressed.
        // The captured log should not contain repeated "Thinking:" spam.
        let thinking_count = output.matches("[ccs/codex] Thinking:").count();
        assert!(
            thinking_count <= 1,
            "Expected <= 1 'Thinking:' line in None mode, found {thinking_count}. Output:\n{output}"
        );
    });
}

#[test]
fn test_codex_reasoning_in_place_updates_in_full_mode() {
    with_default_timeout(|| {
        // Full TTY mode: should use in-place updates with cursor positioning
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::Full);

        let stream = r#"
{"type":"item.started","item":{"type":"reasoning","text":"First chunk"}}
{"type":"item.started","item":{"type":"reasoning","text":" second"}}
{"type":"item.started","item":{"type":"reasoning","text":" third"}}
{"type":"item.completed","item":{"type":"reasoning"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // NEW: Append-only pattern in Full mode
        // 1. First delta: "[ccs/codex] Thinking: First chunk" (NO newline, stays on line)
        // 2. Subsequent deltas: " second" (just the suffix, no prefix, no cursor movement)
        // 3. Subsequent deltas: " third"
        // 4. Completion: "\n" (single newline to finalize line)
        //
        // This pattern works correctly under wrapping and when ANSI is stripped.

        // Verify append-only pattern: prefix appears exactly once (no waterfall)
        let thinking_count = output.matches("Thinking:").count();
        assert_eq!(
            thinking_count, 1,
            "Expected 'Thinking:' to appear exactly once in Full mode (append-only pattern). Found {thinking_count} times. Output:\n{output}"
        );

        // Verify full content is present
        assert!(
            output.contains("First chunk"),
            "Expected content 'First chunk' to be present. Output:\n{output}"
        );
        assert!(
            output.contains("second"),
            "Expected content 'second' to be present. Output:\n{output}"
        );
        assert!(
            output.contains("third"),
            "Expected content 'third' to be present. Output:\n{output}"
        );

        // Verify NO cursor-up sequences (append-only doesn't use cursor movement during streaming)
        assert!(
            !output.contains("\x1b[1A"),
            "Append-only pattern should NOT use cursor-up sequences during streaming. Output:\n{output}"
        );
    });
}

#[test]
fn test_codex_reasoning_multiple_turns_no_cross_contamination() {
    with_default_timeout(|| {
        // Verify that reasoning state is properly reset between turns
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::Basic);

        let stream = r#"
{"type":"turn.started"}
{"type":"item.started","item":{"type":"reasoning","text":"Turn 1 reasoning"}}
{"type":"item.completed","item":{"type":"reasoning"}}
{"type":"turn.completed"}
{"type":"turn.started"}
{"type":"item.started","item":{"type":"reasoning","text":"Turn 2 reasoning"}}
{"type":"item.completed","item":{"type":"reasoning"}}
{"type":"turn.completed"}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Should see exactly 2 "Thinking:" lines (one per turn)
        let thinking_count = output.matches("[ccs/codex] Thinking:").count();
        assert_eq!(
            thinking_count, 2,
            "Expected 2 'Thinking:' lines (one per turn), found {thinking_count}. Output:\n{output}"
        );

        // Verify each turn's reasoning is shown separately
        assert!(
            output.contains("Turn 1 reasoning"),
            "Expected Turn 1 reasoning in output"
        );
        assert!(
            output.contains("Turn 2 reasoning"),
            "Expected Turn 2 reasoning in output"
        );
    });
}
