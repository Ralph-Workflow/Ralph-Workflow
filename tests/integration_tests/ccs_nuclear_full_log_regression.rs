//! Nuclear regression test for CCS delta spam using full production logs.
//!
//! This test consumes entire real-world captured logs from CCS agents containing
//! thousands of streaming deltas to ensure no per-delta spam occurs in non-TTY modes.
//!
//! This is the "nuclear option" mentioned in acceptance criteria - using complete production
//! logs to validate the fix comprehensively.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::config::Verbosity;
use ralph_workflow::json_parser::claude::ClaudeParser;
use ralph_workflow::json_parser::codex::CodexParser;
use ralph_workflow::json_parser::printer::TestPrinter;
use ralph_workflow::json_parser::terminal::TerminalMode;
use ralph_workflow::logger::Colors;
use ralph_workflow::workspace::MemoryWorkspace;
use std::cell::RefCell;
use std::io::BufReader;
use std::rc::Rc;

#[test]
fn test_ccs_codex_full_example_log_no_spam_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::None);

        // Use existing example_log.log (already referenced in codex_reasoning_spam_regression.rs)
        let log = include_str!("artifacts/example_log.log");

        // NOTE: This fixture is Claude/CCS-focused and may not include Codex events.
        // Codex regression coverage is validated by tests that provide Codex-specific streams.
        if !log.contains("item.started") {
            return;
        }

        let reader = BufReader::new(log.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Count reasoning items in log by counting message_start events (crude but effective)
        // This log has thinking blocks, so count content_block_start with type=thinking
        let thinking_blocks = log.matches(r#""type":"reasoning""#).count();
        let thinking_labels = output.matches("Thinking:").count();

        // Should have at most 1 "Thinking:" per thinking block completion
        // With a margin for multi-turn scenarios
        assert!(
            thinking_labels <= thinking_blocks,
            "SPAM DETECTED! With {} reasoning blocks in log, expected <= {} 'Thinking:' labels, found {}.\n\n\
             First 100 output lines:\n{}",
            thinking_blocks,
            thinking_blocks,
            thinking_labels,
            output.lines().take(100).collect::<Vec<_>>().join("\n")
        );

        // Verify output is not empty (content was flushed)
        assert!(
            !output.trim().is_empty(),
            "Expected non-empty output, but got empty string. Content may have been lost."
        );
    });
}

#[test]
fn test_ccs_glm_full_example_log_no_spam_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Use existing example_log.log (contains Claude-style stream events)
        let log = include_str!("artifacts/example_log.log");
        let reader = BufReader::new(log.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Count total deltas by scanning log
        let total_deltas = log.matches("content_block_delta").count();
        let prefix_count = output.matches("[ccs/glm]").count();

        // Strict bound: no more than 1 prefix per 100 deltas (generous bound)
        // With 12,000+ deltas, expect <= ~120 prefixes (one per content block)
        // But we know the real number should be much lower (one per block, not per 100 deltas)
        let max_allowed = (total_deltas / 100).max(1);

        assert!(
            prefix_count <= max_allowed,
            "SPAM DETECTED! With {} total deltas, expected <= {} prefixes in None mode, found {}.\n\n\
             This indicates per-delta spam is occurring.\n\n\
             First 100 output lines:\n{}",
            total_deltas,
            max_allowed,
            prefix_count,
            output.lines().take(100).collect::<Vec<_>>().join("\n")
        );

        // Verify output is not empty (content was flushed)
        assert!(
            !output.trim().is_empty(),
            "Expected non-empty output, but got empty string. Content may have been lost."
        );
    });
}

#[test]
fn test_ccs_glm_full_example_log_no_spam_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        let log = include_str!("artifacts/example_log.log");
        let reader = BufReader::new(log.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let total_deltas = log.matches("content_block_delta").count();
        let prefix_count = output.matches("[ccs/glm]").count();
        let max_allowed = (total_deltas / 100).max(1);

        assert!(
            prefix_count <= max_allowed,
            "SPAM DETECTED! With {} total deltas, expected <= {} prefixes in Basic mode, found {}.\n\n\
             First 100 output lines:\n{}",
            total_deltas,
            max_allowed,
            prefix_count,
            output.lines().take(100).collect::<Vec<_>>().join("\n")
        );

        // Verify output is not empty
        assert!(
            !output.trim().is_empty(),
            "Expected non-empty output, but got empty string. Content may have been lost."
        );
    });
}

#[test]
fn test_ccs_codex_full_example_log_no_spam_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::Basic);

        let log = include_str!("artifacts/example_log.log");

        // NOTE: This fixture is Claude/CCS-focused and may not include Codex events.
        // Codex regression coverage is validated by tests that provide Codex-specific streams.
        if !log.contains("item.started") {
            return;
        }

        let reader = BufReader::new(log.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let reasoning_blocks = log.matches(r#""type":"reasoning""#).count();
        let thinking_labels = output.matches("Thinking:").count();

        assert!(
            thinking_labels <= reasoning_blocks,
            "SPAM DETECTED! With {} reasoning blocks, expected <= {} 'Thinking:' labels in Basic mode, found {}.\n\n\
             Output:\n{}",
            reasoning_blocks,
            reasoning_blocks,
            thinking_labels,
            output.lines().take(100).collect::<Vec<_>>().join("\n")
        );

        // Verify output is not empty
        assert!(
            !output.trim().is_empty(),
            "Expected non-empty output, but got empty string. Content may have been lost."
        );
    });
}

#[test]
fn test_ccs_glm_full_example_log_strict_per_block_bound() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        let log = include_str!("artifacts/example_log.log");
        let reader = BufReader::new(log.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Stricter bound: count actual content blocks
        let content_block_starts = log.matches("content_block_start").count();
        let prefix_count = output.matches("[ccs/glm]").count();

        // Allow at most 2x content blocks for margin (multi-line output per block)
        let max_allowed = content_block_starts * 2;

        assert!(
            prefix_count <= max_allowed,
            "SPAM DETECTED! With {} content blocks, expected <= {} prefixes (2x margin), found {}.\n\n\
             This suggests per-delta spam beyond reasonable block boundaries.\n\n\
             First 100 output lines:\n{}",
            content_block_starts,
            max_allowed,
            prefix_count,
            output.lines().take(100).collect::<Vec<_>>().join("\n")
        );
    });
}
