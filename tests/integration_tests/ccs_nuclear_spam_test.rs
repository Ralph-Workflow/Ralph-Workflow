//! Nuclear option regression test for CCS streaming spam.
//!
//! This test is EXTREMELY strict and designed to catch ANY per-delta spam that
//! might slip through existing tests. It uses:
//!
//! - 500+ deltas per content block (extreme stress)
//! - HARD assertion: total lines MUST be ≤ 10 (not ≤ prefix count)
//! - Consecutive duplicate detection
//! - Both ccs/glm and ccs/codex in same test
//! - All delta types: text, thinking, tool input
//!
//! If this test fails, it means per-delta spam is DEFINITELY happening.
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
fn test_ccs_glm_nuclear_500_text_deltas_must_produce_one_line() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

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

        // NUCLEAR ASSERTION: With 500 deltas, we should have AT MOST 2 total lines
        // (1 content line + maybe 1 empty line)
        let total_lines = output.lines().count();
        assert!(
            total_lines <= 2,
            "NUCLEAR TEST FAILED: Expected ≤ 2 total lines for 500 text deltas, found {total_lines}.\n\n\
             This proves per-delta spam is happening!\n\n\
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
fn test_ccs_glm_nuclear_500_thinking_deltas_must_produce_one_line() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Build stream with 500 thinking deltas
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
"#,
        );

        for i in 0..500 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"thinking_delta","thinking":"t{i} "}}}}}}"#).unwrap();
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

        // NUCLEAR ASSERTION: With 500 thinking deltas, we should have AT MOST 2 total lines
        let total_lines = output.lines().count();
        assert!(
            total_lines <= 2,
            "NUCLEAR TEST FAILED: Expected ≤ 2 total lines for 500 thinking deltas, found {total_lines}.\n\n\
             This proves per-delta spam is happening!\n\n\
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

#[test]
fn test_ccs_glm_nuclear_500_tool_input_deltas_must_produce_two_lines() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Build stream with 500 tool input deltas
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tool1","name":"bash"}}}
"#,
        );

        for i in 0..500 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"tool_use_delta","tool_use":{{"input":"cmd{i} "}}}}}}}}"#).unwrap();
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

        // NUCLEAR ASSERTION: With 500 tool input deltas, we should have AT MOST 3 total lines
        // (1 tool start + 1 tool input + maybe 1 empty line)
        let total_lines = output.lines().count();
        assert!(
            total_lines <= 3,
            "NUCLEAR TEST FAILED: Expected ≤ 3 total lines for 500 tool input deltas, found {total_lines}.\n\n\
             This proves per-delta spam is happening!\n\n\
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

#[test]
fn test_ccs_codex_nuclear_500_reasoning_deltas_must_produce_one_line() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

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

        // NUCLEAR ASSERTION: With 500 reasoning deltas, we should have AT MOST 2 total lines
        let total_lines = output.lines().count();
        assert!(
            total_lines <= 2,
            "NUCLEAR TEST FAILED: Expected ≤ 2 total lines for 500 reasoning deltas, found {total_lines}.\n\n\
             This proves per-delta spam is happening!\n\n\
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

#[test]
fn test_ccs_codex_nuclear_500_agent_message_deltas_must_produce_one_line() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Build stream with 500 agent_message deltas
        let mut stream = String::new();

        for i in 0..500 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"agent_message","text":"m{i} "}}}}"#
            )
            .unwrap();
        }

        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"agent_message"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // NUCLEAR ASSERTION: With 500 agent_message deltas, we should have AT MOST 2 total lines
        let total_lines = output.lines().count();
        assert!(
            total_lines <= 2,
            "NUCLEAR TEST FAILED: Expected ≤ 2 total lines for 500 agent_message deltas, found {total_lines}.\n\n\
             This proves per-delta spam is happening!\n\n\
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

#[test]
fn test_ccs_glm_nuclear_mixed_1500_deltas_must_produce_few_lines() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Build stream with 500 thinking + 500 text + 500 tool input = 1500 total deltas
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
"#,
        );

        // 500 thinking deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
"#,
        );
        for i in 0..500 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"thinking_delta","thinking":"t{i} "}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
"#,
        );

        // 500 text deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}}
"#,
        );
        for i in 0..500 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":1,"delta":{{"type":"text_delta","text":"w{i} "}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":1}}
"#,
        );

        // 500 tool input deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"tool1","name":"bash"}}}
"#,
        );
        for i in 0..500 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":2,"delta":{{"type":"tool_use_delta","tool_use":{{"input":"c{i} "}}}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":2}}
"#,
        );

        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"message_stop"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // NUCLEAR ASSERTION: With 1500 deltas across 3 blocks, we should have AT MOST 5 total lines
        // (1 thinking + 1 text + 1 tool start + 1 tool input + maybe 1 empty)
        let total_lines = output.lines().count();
        assert!(
            total_lines <= 5,
            "NUCLEAR TEST FAILED: Expected ≤ 5 total lines for 1500 deltas (500 thinking + 500 text + 500 tool), found {}.\n\n\
             This proves per-delta spam is happening!\n\n\
             Output (first 50 lines):\n{}",
            total_lines,
            output.lines().take(50).collect::<Vec<_>>().join("\n")
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

        // Verify all content types are present
        assert!(
            output.contains("t0") && output.contains("t499"),
            "Expected thinking content (t0...t499) to be present. Output:\n{output}"
        );
        assert!(
            output.contains("w0") && output.contains("w499"),
            "Expected text content (w0...w499) to be present. Output:\n{output}"
        );
    });
}
