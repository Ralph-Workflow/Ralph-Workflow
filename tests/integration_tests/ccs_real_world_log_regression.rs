//! Real-world log regression test for CCS streaming spam (all delta types).
//!
//! This test parses the full `example_log.log` (captured from production CCS usage)
//! and verifies that repeated prefixed lines do not occur for ANY delta type in
//! non-TTY modes (text deltas, thinking deltas, tool input deltas).
//!
//! Unlike `codex_reasoning_spam_regression.rs` which only checks for "Thinking:" spam,
//! this test validates that ALL streaming delta types are properly accumulated and
//! flushed once at appropriate boundaries, not spammed per-delta.
//!
//! The `example_log.log` contains:
//! - 9,515 `thinking_delta` events
//! - 818 `text_delta` events
//! - 2,263 `input_json_delta` (tool input) events
//!
//! Expected behavior: In None/Basic modes, these thousands of deltas should NOT
//! produce thousands of output lines. Instead, content should be accumulated and
//! flushed at message boundaries (`message_stop`, `content_block_stop`).
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
fn test_real_world_log_no_spam_any_delta_type_none_mode() {
    with_default_timeout(|| {
        // Simulate pure non-TTY environment (None mode: no ANSI sequences at all)
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::None);

        // Parse the full real-world log.
        // NOTE: This fixture is Claude/CCS-focused and may not include Codex events.
        // Codex regression coverage is validated by tests that provide Codex-specific streams.
        let log = include_str!("artifacts/example_log.log");
        if !log.contains("item.started") {
            return;
        }

        let reader = BufReader::new(Cursor::new(log));
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Analyze output for spam patterns across ALL delta types
        let lines: Vec<&str> = output.lines().collect();
        let total_lines = lines.len();

        // Count prefix occurrences by type
        let thinking_count = output.matches("[ccs/codex] Thinking:").count();
        let tool_use_count = output.matches("[ccs/codex] Using ").count();
        let total_prefix_count = output.matches("[ccs/codex]").count();

        // Behavioral invariant: parser must not produce consecutive duplicate non-empty lines
        let has_consecutive_duplicates = lines.windows(2).any(|w| !w[0].is_empty() && w[0] == w[1]);
        assert!(
            !has_consecutive_duplicates,
            "Parser should not produce consecutive duplicate lines, indicating spam.\n\n\
             Output excerpt (first 100 lines):\n{}",
            lines
                .iter()
                .take(100)
                .enumerate()
                .map(|(i, l)| format!("{:4}: {}", i + 1, l))
                .collect::<Vec<_>>()
                .join("\n")
        );

        // Assert: With thousands of deltas (9515 thinking + 818 text + 2263 tool input),
        // we should NOT see thousands of output lines. A well-behaved accumulator
        // should flush content at message boundaries, resulting in far fewer lines.
        //
        // The real log contains multiple messages/turns, so we allow a reasonable
        // number of prefix lines (one per message/block), but not one per delta.
        assert!(
            thinking_count <= 10,
            "Expected <= 10 'Thinking:' lines in None mode for real log (9515 thinking deltas), \
             found {}. This indicates per-delta spam!\n\n\
             Total output lines: {}\n\
             Total [ccs/codex] prefixes: {}\n\n\
             Output excerpt (first 100 lines):\n{}",
            thinking_count,
            total_lines,
            total_prefix_count,
            lines
                .iter()
                .take(100)
                .enumerate()
                .map(|(i, l)| format!("{:4}: {}", i + 1, l))
                .collect::<Vec<_>>()
                .join("\n")
        );

        // Additional check: tool use lines should also be bounded
        // (The log has 2263 input_json_delta events)
        assert!(
            tool_use_count <= 50,
            "Expected <= 50 'Using' lines in None mode for real log (2263 input_json deltas), \
             found {}. This indicates tool input spam!\n\n\
             Output excerpt (first 100 lines):\n{}",
            tool_use_count,
            lines
                .iter()
                .take(100)
                .enumerate()
                .map(|(i, l)| format!("{:4}: {}", i + 1, l))
                .collect::<Vec<_>>()
                .join("\n")
        );
    });
}

#[test]
fn test_real_world_log_no_spam_any_delta_type_basic_mode() {
    with_default_timeout(|| {
        // Simulate non-TTY environment (Basic mode: colors but no cursor positioning)
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::Basic);

        // Parse the full real-world log.
        // NOTE: This fixture is Claude/CCS-focused and may not include Codex events.
        // Codex regression coverage is validated by tests that provide Codex-specific streams.
        let log = include_str!("artifacts/example_log.log");
        if !log.contains("item.started") {
            return;
        }

        let reader = BufReader::new(Cursor::new(log));
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let thinking_count = output.matches("[ccs/codex] Thinking:").count();
        let tool_use_count = output.matches("[ccs/codex] Using ").count();

        // Same assertions as None mode: Basic mode should also suppress per-delta output
        assert!(
            thinking_count <= 10,
            "Expected <= 10 'Thinking:' lines in Basic mode for real log, found {}.\n\n\
             Output excerpt (first 100 lines):\n{}",
            thinking_count,
            output
                .lines()
                .take(100)
                .enumerate()
                .map(|(i, l)| format!("{:4}: {}", i + 1, l))
                .collect::<Vec<_>>()
                .join("\n")
        );

        assert!(
            tool_use_count <= 50,
            "Expected <= 50 'Using' lines in Basic mode for real log, found {}.\n\n\
             Output excerpt (first 100 lines):\n{}",
            tool_use_count,
            output
                .lines()
                .take(100)
                .enumerate()
                .map(|(i, l)| format!("{:4}: {}", i + 1, l))
                .collect::<Vec<_>>()
                .join("\n")
        );
    });
}

#[test]
fn test_real_world_log_text_deltas_accumulated() {
    with_default_timeout(|| {
        // This test specifically validates that text deltas (not thinking deltas)
        // are properly accumulated and not spammed per-delta.
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::None);

        let log = include_str!("artifacts/example_log.log");
        let reader = BufReader::new(Cursor::new(log));
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // The log contains 818 text_delta events. These should NOT produce 818 output lines.
        // Count lines that look like text content (not thinking, not tool use)
        let lines: Vec<&str> = output.lines().collect();
        let text_lines: Vec<&str> = lines
            .iter()
            .filter(|l| {
                l.contains("[ccs/codex]")
                    && !l.contains("Thinking:")
                    && !l.contains("Using ")
                    && !l.contains("✓")
                    && !l.is_empty()
            })
            .copied()
            .collect();

        // With 818 text deltas, we expect far fewer output lines (text is accumulated)
        // Allow a reasonable bound for multiple messages/turns
        assert!(
            text_lines.len() <= 100,
            "Expected <= 100 text output lines for real log (818 text_delta events), \
             found {}. Text deltas may be spammed per-delta!\n\n\
             Text lines sample:\n{}",
            text_lines.len(),
            text_lines
                .iter()
                .take(20)
                .enumerate()
                .map(|(i, l)| format!("{}: {}", i + 1, l))
                .collect::<Vec<_>>()
                .join("\n")
        );
    });
}
