//! Nuclear test specifically for Basic mode to ensure no spam there either.
//!
//! The bug report mentions both Basic and None modes, so we test Basic mode
//! separately with the same nuclear strictness.
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
use std::fmt::Write;
use std::io::BufReader;
use std::rc::Rc;

#[test]
fn test_ccs_glm_basic_mode_500_text_deltas_must_produce_one_line() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        // Build stream with 500 text deltas
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
"#,
        );

        for i in 0..500 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"w{i} "}}}}}}"#
            ).unwrap();
        }

        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // NUCLEAR ASSERTION: With 500 deltas in Basic mode, we should have AT MOST 2 total lines
        let total_lines = output.lines().count();
        assert!(
            total_lines <= 2,
            "BASIC MODE NUCLEAR TEST FAILED: Expected ≤ 2 total lines for 500 text deltas, found {total_lines}.\n\n\
             This proves per-delta spam is happening in Basic mode!\n\n\
             Output:\n{output}"
        );

        // Verify no consecutive duplicates
        let lines: Vec<&str> = output.lines().collect();
        for (i, pair) in lines.windows(2).enumerate() {
            assert!(
                pair[0].is_empty() || pair[0] != pair[1],
                "Found consecutive duplicate at line {}: '{}'\n\nOutput:\n{}",
                i + 1,
                pair[0],
                output
            );
        }

        // Verify content is present
        assert!(
            output.contains("w0") && output.contains("w499"),
            "Expected content (w0...w499) to be present. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_basic_mode_500_reasoning_deltas_must_produce_one_line() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::Basic);

        // Build stream with 500 reasoning deltas
        let mut stream = String::new();

        for i in 0..500 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"reasoning","text":"r{i} "}}}}"#
            )
            .unwrap();
        }

        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"reasoning"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // NUCLEAR ASSERTION: With 500 reasoning deltas in Basic mode, we should have AT MOST 2 total lines
        let total_lines = output.lines().count();
        assert!(
            total_lines <= 2,
            "BASIC MODE NUCLEAR TEST FAILED: Expected ≤ 2 total lines for 500 reasoning deltas, found {total_lines}.\n\n\
             This proves per-delta spam is happening in Basic mode!\n\n\
             Output:\n{output}"
        );

        // Verify no consecutive duplicates
        let lines: Vec<&str> = output.lines().collect();
        for (i, pair) in lines.windows(2).enumerate() {
            assert!(
                pair[0].is_empty() || pair[0] != pair[1],
                "Found consecutive duplicate at line {}: '{}'\n\nOutput:\n{}",
                i + 1,
                pair[0],
                output
            );
        }
    });
}
