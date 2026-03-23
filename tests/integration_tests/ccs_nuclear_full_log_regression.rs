//! Nuclear regression test for CCS delta spam using production-scale fixtures.
//!
//! The Codex coverage here uses a focused multi-delta reasoning fixture with a
//! single logical reasoning session. That keeps the fixture small while still
//! asserting the real bug boundary: non-TTY modes must emit one `Thinking:`
//! line per completed reasoning session, not one line per streamed chunk.
//!
//! The Claude/GLM coverage continues to use the larger captured production log.
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

fn codex_full_log_fixture() -> &'static str {
    let log = include_str!("artifacts/codex_reasoning_spam.log");
    assert!(
        log.contains("\"type\":\"item.started\""),
        "Codex full-log regression fixture must contain item.started events"
    );
    assert!(
        log.contains("\"type\":\"reasoning\""),
        "Codex full-log regression fixture must contain reasoning items"
    );
    assert!(
        !log.contains("thinking_delta"),
        "Codex full-log regression fixture must not silently use Claude-style thinking deltas"
    );
    assert!(
        log.matches(r#""type":"reasoning","text":"#).count() > 1,
        "Codex full-log regression fixture must contain multiple reasoning chunks"
    );
    assert_eq!(
        count_codex_reasoning_sessions(log),
        1,
        "Codex full-log regression fixture must encode exactly one completed reasoning session"
    );
    log
}

fn count_codex_reasoning_sessions(log: &str) -> usize {
    log.lines()
        .filter(|line| line.contains(r#""type":"item.completed","item":{"type":"reasoning""#))
        .count()
}

#[test]
fn test_ccs_codex_full_example_log_no_spam_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        let log = codex_full_log_fixture();

        let reader = BufReader::new(log.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let reasoning_sessions = count_codex_reasoning_sessions(log);
        let thinking_labels = output.matches("Thinking:").count();

        // The regression boundary is logical reasoning sessions, not raw streamed chunks.
        // A broken parser that emits one prefix per chunk must fail this assertion.
        assert!(
            thinking_labels <= reasoning_sessions,
            "SPAM DETECTED! With {} completed reasoning session(s) in log, expected <= {} 'Thinking:' labels, found {}.\n\n\
             First 100 output lines:\n{}",
            reasoning_sessions,
            reasoning_sessions,
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

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
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

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
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

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::Basic);

        let log = codex_full_log_fixture();

        let reader = BufReader::new(log.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let reasoning_sessions = count_codex_reasoning_sessions(log);
        let thinking_labels = output.matches("Thinking:").count();

        assert!(
            thinking_labels <= reasoning_sessions,
            "SPAM DETECTED! With {} completed reasoning session(s), expected <= {} 'Thinking:' labels in Basic mode, found {}.\n\n\
             Output:\n{}",
            reasoning_sessions,
            reasoning_sessions,
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

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
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
