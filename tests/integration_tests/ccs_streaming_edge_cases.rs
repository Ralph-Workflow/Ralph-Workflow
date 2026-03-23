//! Edge case tests for CCS streaming delta handling.
//!
//! These tests validate edge cases that might expose remaining spam issues:
//! - Empty deltas and whitespace-only deltas
//! - Rapid block transitions between different content types
//! - Protocol violations and malformed streams
//! - Zero-length content and boundary conditions
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
fn test_empty_and_whitespace_deltas_no_spam() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let mut parser =
            ClaudeParser::with_printer(Colors::new(), Verbosity::Normal, test_printer.clone())
                .with_display_name("ccs/glm")
                .with_terminal_mode(TerminalMode::None);

        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"   "}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"\n"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"actual"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" content"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        let prefix_count = output.matches("[ccs/glm]").count();

        assert!(
            prefix_count <= 1,
            "Expected <= 1 prefix with empty/whitespace deltas, found {prefix_count}. Output:\n{output}"
        );
        assert!(
            output.contains("actual content"),
            "Expected content to be present"
        );
    });
}

#[test]
fn test_rapid_block_transitions_no_cross_contamination() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let mut parser =
            ClaudeParser::with_printer(Colors::new(), Verbosity::Normal, test_printer.clone())
                .with_display_name("ccs/glm")
                .with_terminal_mode(TerminalMode::None);

        // Rapidly switch between thinking -> text -> thinking -> text
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"think1"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"text1"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":1}}
{"type":"stream_event","event":{"type":"content_block_start","index":2,"content_block":{"type":"thinking","thinking":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":2,"delta":{"type":"thinking_delta","thinking":"think2"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":2}}
{"type":"stream_event","event":{"type":"content_block_start","index":3,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":3,"delta":{"type":"text_delta","text":"text2"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":3}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Should have at most 4 prefixes (2 thinking + 2 text)
        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 4,
            "Expected <= 4 prefixes, found {prefix_count}. Output:\n{output}"
        );

        // Verify all content present
        assert!(output.contains("think1") || output.contains("Thinking"));
        assert!(output.contains("text1"));
        assert!(output.contains("think2") || output.contains("Thinking"));
        assert!(output.contains("text2"));
    });
}

#[test]
fn test_single_character_deltas_no_spam() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let mut parser =
            ClaudeParser::with_printer(Colors::new(), Verbosity::Normal, test_printer.clone())
                .with_display_name("ccs/glm")
                .with_terminal_mode(TerminalMode::None);

        // Stream each character as a separate delta
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
"#,
        );

        let text = "Hello World!";
        for ch in text.chars() {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{ch}"}}}}}}"#
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
        let prefix_count = output.matches("[ccs/glm]").count();

        // With 12 single-character deltas, should still only have 1 prefix
        assert!(
            prefix_count <= 1,
            "Expected <= 1 prefix with 12 single-char deltas, found {prefix_count}. Output:\n{output}"
        );
        assert!(output.contains("Hello World!"));
    });
}

#[test]
fn test_tool_input_chunked_deltas_no_spam() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let mut parser =
            ClaudeParser::with_printer(Colors::new(), Verbosity::Normal, test_printer.clone())
                .with_display_name("ccs/glm")
                .with_terminal_mode(TerminalMode::None);

        // Tool input arriving in many small chunks
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tool1","name":"bash","input":""}}}
"#,
        );

        // Send tool input in 50 small chunks
        let tool_input = r#"{"command":"ls -la /path/to/directory","timeout":5000}"#;
        let chunk_size = 2;
        for chunk in tool_input.as_bytes().chunks(chunk_size) {
            let chunk_str = std::str::from_utf8(chunk).unwrap();
            // Escape for JSON
            let escaped = chunk_str.replace('\\', "\\\\").replace('"', "\\\"");
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"input_json_delta","partial_json":"{escaped}"}}}}}}"#).unwrap();
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
        let prefix_count = output.matches("[ccs/glm]").count();

        // Should have at most 1 prefix for tool input flush
        assert!(
            prefix_count <= 1,
            "Expected <= 1 prefix with chunked tool input, found {prefix_count}. Output:\n{output}"
        );
    });
}

#[test]
fn test_codex_empty_reasoning_items_no_spam() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let mut parser = CodexParser::with_printer_for_test(
            Colors::new(),
            Verbosity::Normal,
            test_printer.clone(),
        )
        .with_display_name_for_test("ccs/codex")
        .with_terminal_mode(TerminalMode::None);

        let stream = r#"
{"type":"item.started","item":{"type":"reasoning","text":""}}
{"type":"item.started","item":{"type":"reasoning","text":"   "}}
{"type":"item.started","item":{"type":"reasoning","text":"\n"}}
{"type":"item.started","item":{"type":"reasoning","text":"actual reasoning"}}
{"type":"item.completed","item":{"type":"reasoning"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        let thinking_count = output.matches("Thinking:").count();

        assert!(
            thinking_count <= 1,
            "Expected <= 1 'Thinking:' with empty reasoning items, found {thinking_count}. Output:\n{output}"
        );
        assert!(output.contains("actual reasoning"));
    });
}

#[test]
fn test_codex_rapid_agent_message_transitions_no_spam() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let mut parser = CodexParser::with_printer_for_test(
            Colors::new(),
            Verbosity::Normal,
            test_printer.clone(),
        )
        .with_display_name_for_test("ccs/codex")
        .with_terminal_mode(TerminalMode::None);

        // Alternate between reasoning and agent_message rapidly
        let stream = r#"
{"type":"item.started","item":{"type":"reasoning","text":"r1"}}
{"type":"item.completed","item":{"type":"reasoning"}}
{"type":"item.started","item":{"type":"agent_message","text":"m1"}}
{"type":"item.completed","item":{"type":"agent_message"}}
{"type":"item.started","item":{"type":"reasoning","text":"r2"}}
{"type":"item.completed","item":{"type":"reasoning"}}
{"type":"item.started","item":{"type":"agent_message","text":"m2"}}
{"type":"item.completed","item":{"type":"agent_message"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        let prefix_count = output.matches("[ccs/codex]").count();

        // Should have at most 4 prefixes (2 reasoning + 2 agent_message)
        assert!(
            prefix_count <= 4,
            "Expected <= 4 prefixes with rapid transitions, found {prefix_count}. Output:\n{output}"
        );

        // Verify all content present
        assert!(output.contains("r1") || output.contains("Thinking"));
        assert!(output.contains("m1"));
        assert!(output.contains("r2") || output.contains("Thinking"));
        assert!(output.contains("m2"));
    });
}

#[test]
fn test_multi_turn_session_boundary_isolation() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let mut parser =
            ClaudeParser::with_printer(Colors::new(), Verbosity::Normal, test_printer.clone())
                .with_display_name("ccs/glm")
                .with_terminal_mode(TerminalMode::None);

        // Multiple complete messages in sequence
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"message1"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg2","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"message2"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg3","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"message3"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        let prefix_count = output.matches("[ccs/glm]").count();

        // Should have at most 3 prefixes (one per message)
        assert!(
            prefix_count <= 3,
            "Expected <= 3 prefixes for 3 messages, found {prefix_count}. Output:\n{output}"
        );

        // Verify all messages present and isolated
        assert!(output.contains("message1"));
        assert!(output.contains("message2"));
        assert!(output.contains("message3"));

        // Verify messages don't contaminate each other
        let lines: Vec<&str> = output.lines().collect();
        for line in &lines {
            if line.contains("message1") {
                assert!(!line.contains("message2") && !line.contains("message3"));
            }
            if line.contains("message2") {
                assert!(!line.contains("message1") && !line.contains("message3"));
            }
            if line.contains("message3") {
                assert!(!line.contains("message1") && !line.contains("message2"));
            }
        }
    });
}
