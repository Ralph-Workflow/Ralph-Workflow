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

use crate::test_timeout::with_timeout;
use ralph_workflow::config::Verbosity;
use ralph_workflow::json_parser::printer::TestPrinter;
use ralph_workflow::json_parser::terminal::TerminalMode;
use ralph_workflow::json_parser::ClaudeParser;
use ralph_workflow::logger::Colors;
use ralph_workflow::workspace::MemoryWorkspace;
use std::cell::RefCell;
use std::io::{BufReader, Cursor};
use std::rc::Rc;
use std::time::Duration;

const REAL_LOG_TIMEOUT: Duration = Duration::from_secs(20);

fn real_world_log_fixture() -> &'static str {
    let log = include_str!("artifacts/example_log.log");
    assert!(
        log.contains("\"type\":\"content_block_start\""),
        "real-world fixture must contain content_block_start events"
    );
    assert!(
        log.contains("\"type\":\"content_block_delta\""),
        "real-world fixture must contain content_block_delta events"
    );
    assert!(
        log.contains("\"type\":\"thinking_delta\""),
        "real-world fixture must contain thinking deltas for the spam regression"
    );
    assert!(
        log.contains("\"type\":\"input_json_delta\""),
        "real-world fixture must contain tool-input deltas for the spam regression"
    );
    log
}

fn parse_real_world_log(mode: TerminalMode) -> String {
    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let colors = Colors::new();
    let verbosity = Verbosity::Normal;

    let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
        .with_display_name("ccs/claude")
        .with_terminal_mode(mode);

    let reader = BufReader::new(Cursor::new(real_world_log_fixture()));
    let workspace = MemoryWorkspace::new_test();
    parser.parse_stream(reader, &workspace).unwrap();

    let output = test_printer.borrow().get_output();
    assert!(
        output.contains("[ccs/claude]"),
        "real-world CCS fixture must produce visible Claude parser output"
    );
    output
}

#[test]
fn test_real_world_log_no_spam_any_delta_type_none_mode() {
    with_timeout(
        || {
            let output = parse_real_world_log(TerminalMode::None);

            // Analyze output for spam patterns across ALL delta types
            let lines: Vec<&str> = output.lines().collect();
            let total_lines = lines.len();

            // Count prefix occurrences by type
            let thinking_count = output.matches("[ccs/claude] Thinking:").count();
            let tool_use_count = output.matches("[ccs/claude] Using ").count();
            let total_prefix_count = output.matches("[ccs/claude]").count();

            // Assert: With thousands of deltas (9515 thinking + 818 text + 2263 tool input),
            // we should NOT see thousands of output lines. A well-behaved accumulator
            // should flush content at message boundaries, resulting in far fewer lines.
            //
            // The real log contains multiple messages/turns, so we allow a reasonable
            // number of prefix lines (one per message/block), but not one per delta.
            assert!(
            thinking_count <= 100,
            "Expected <= 100 'Thinking:' lines in None mode for real log (9515 thinking deltas), \
             found {}. This indicates per-delta spam!\n\n\
             Total output lines: {}\n\
             Total [ccs/claude] prefixes: {}\n\n\
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
        },
        REAL_LOG_TIMEOUT,
    );
}

#[test]
fn test_real_world_log_no_spam_any_delta_type_basic_mode() {
    with_timeout(
        || {
            let output = parse_real_world_log(TerminalMode::Basic);

            let thinking_count = output.matches("[ccs/claude] Thinking:").count();
            let tool_use_count = output.matches("[ccs/claude] Using ").count();

            // Same assertions as None mode: Basic mode should also suppress per-delta output
            assert!(
                thinking_count <= 100,
                "Expected <= 100 'Thinking:' lines in Basic mode for real log, found {}.\n\n\
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
        },
        REAL_LOG_TIMEOUT,
    );
}

#[test]
fn test_real_world_log_text_deltas_accumulated() {
    with_timeout(
        || {
            // This test specifically validates that text deltas (not thinking deltas)
            // are properly accumulated and not spammed per-delta.
            let output = parse_real_world_log(TerminalMode::None);

            // The log contains 818 text_delta events. These should NOT produce 818 output lines.
            // Count lines that look like text content (not thinking, not tool use)
            let lines: Vec<&str> = output.lines().collect();
            let text_lines: Vec<&str> = lines
                .iter()
                .filter(|l| {
                    (l.contains("[ccs/claude]") || l.contains("[ccs/codex]"))
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
        },
        REAL_LOG_TIMEOUT,
    );
}
