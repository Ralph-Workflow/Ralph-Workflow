//! Extreme real-world CCS streaming regression test.
//!
//! This test simulates real production scenarios with hundreds of deltas to ensure
//! the spam fix works at scale. Real-world CCS sessions can emit 500+ deltas for
//! long reasoning or tool input streams.
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

/// Generate a stream with N deltas for a single content block.
fn generate_text_delta_stream(n: usize, _agent_type: &str) -> String {
    let mut stream = String::new();

    // Message start
    stream.push_str(r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}"#);
    stream.push('\n');

    // Content block start
    stream.push_str(r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}"#);
    stream.push('\n');

    // Many text deltas
    for i in 0..n {
        write!(stream,
            r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"word{i} "}}}}}}"#
        ).unwrap();
        stream.push('\n');
    }

    // Content block stop
    stream.push_str(r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}"#);
    stream.push('\n');

    // Message stop
    stream.push_str(r#"{"type":"stream_event","event":{"type":"message_stop"}}"#);
    stream.push('\n');

    stream
}

/// Generate a thinking delta stream with N deltas.
fn generate_thinking_delta_stream(n: usize, thinking_type: &str) -> String {
    let mut stream = String::new();

    // Message start
    stream.push_str(r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}"#);
    stream.push('\n');

    // Thinking block start
    write!(stream,
        r#"{{"type":"stream_event","event":{{"type":"content_block_start","index":0,"content_block":{{"type":"{thinking_type}","{thinking_type}":""}}}}}}"#
    ).unwrap();
    stream.push('\n');

    // Many thinking deltas
    for i in 0..n {
        write!(stream,
            r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"{thinking_type}_delta","{thinking_type}":"thought{i} "}}}}}}"#
        ).unwrap();
        stream.push('\n');
    }

    // Content block stop
    stream.push_str(r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}"#);
    stream.push('\n');

    // Message stop
    stream.push_str(r#"{"type":"stream_event","event":{"type":"message_stop"}}"#);
    stream.push('\n');

    stream
}

/// Generate a Codex reasoning stream with N deltas.
fn generate_codex_reasoning_stream(n: usize) -> String {
    let mut stream = String::new();

    // Session start
    stream.push_str(r#"{"type":"event","event":"session.started"}"#);
    stream.push('\n');

    // Item started
    stream.push_str(
        r#"{"type":"event","event":"item.started","item_type":"reasoning","item_id":"r1"}"#,
    );
    stream.push('\n');

    // Many deltas
    for i in 0..n {
        write!(stream,
            r#"{{"type":"event","event":"item.content.delta","item_id":"r1","item_type":"reasoning","delta":"reason{i} "}}"#
        ).unwrap();
        stream.push('\n');
    }

    // Item completed
    stream.push_str(
        r#"{"type":"event","event":"item.completed","item_type":"reasoning","item_id":"r1"}"#,
    );
    stream.push('\n');

    // Session done
    stream.push_str(r#"{"type":"event","event":"session.done"}"#);
    stream.push('\n');

    stream
}

#[test]
fn test_ccs_glm_extreme_text_deltas_500_chunks_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        let stream = generate_text_delta_stream(500, "glm");
        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // With 500 deltas, if per-delta output is NOT suppressed, we'd see 500+ prefix lines.
        // With proper suppression, we should see AT MOST 1 prefix line (at completion).
        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 1,
            "Expected <= 1 '[ccs/glm]' prefix with 500 text deltas in None mode, found {}.\n\n\
             This indicates per-delta spam is still occurring!\n\n\
             Output excerpt (first 50 lines):\n{}",
            prefix_count,
            output.lines().take(50).collect::<Vec<_>>().join("\n")
        );

        // Verify content is accumulated and present
        assert!(
            output.contains("word0") && output.contains("word499"),
            "Expected accumulated content to contain first and last words. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_extreme_text_deltas_500_chunks_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        let stream = generate_text_delta_stream(500, "glm");
        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 1,
            "Expected <= 1 '[ccs/glm]' prefix with 500 text deltas in Basic mode, found {}.\n\n\
             Output excerpt (first 50 lines):\n{}",
            prefix_count,
            output.lines().take(50).collect::<Vec<_>>().join("\n")
        );
    });
}

#[test]
fn test_ccs_glm_extreme_thinking_deltas_500_chunks_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        let stream = generate_thinking_delta_stream(500, "thinking");
        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Should see AT MOST 1 "Thinking:" line (at completion)
        let thinking_count = output.matches("Thinking:").count();
        assert!(
            thinking_count <= 1,
            "Expected <= 1 'Thinking:' line with 500 thinking deltas in None mode, found {}.\n\n\
             Output excerpt (first 50 lines):\n{}",
            thinking_count,
            output.lines().take(50).collect::<Vec<_>>().join("\n")
        );

        // Verify thinking content is present
        assert!(
            output.contains("thought0") && output.contains("thought499"),
            "Expected accumulated thinking content. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_extreme_thinking_deltas_500_chunks_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        let stream = generate_thinking_delta_stream(500, "thinking");
        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let thinking_count = output.matches("Thinking:").count();
        assert!(
            thinking_count <= 1,
            "Expected <= 1 'Thinking:' line with 500 thinking deltas in Basic mode, found {}.\n\n\
             Output excerpt (first 50 lines):\n{}",
            thinking_count,
            output.lines().take(50).collect::<Vec<_>>().join("\n")
        );
    });
}

#[test]
fn test_ccs_codex_extreme_reasoning_deltas_500_chunks_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        let stream = generate_codex_reasoning_stream(500);
        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Should see AT MOST 1 "Thinking:" line (at reasoning completion)
        let thinking_count = output.matches("Thinking:").count();
        assert!(
            thinking_count <= 1,
            "Expected <= 1 'Thinking:' line with 500 reasoning deltas in None mode, found {}.\n\n\
             Output excerpt (first 50 lines):\n{}",
            thinking_count,
            output.lines().take(50).collect::<Vec<_>>().join("\n")
        );

        // Verify reasoning content is present
        assert!(
            output.contains("reason0") && output.contains("reason499"),
            "Expected accumulated reasoning content. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_extreme_reasoning_deltas_500_chunks_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::Basic);

        let stream = generate_codex_reasoning_stream(500);
        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let thinking_count = output.matches("Thinking:").count();
        assert!(
            thinking_count <= 1,
            "Expected <= 1 'Thinking:' line with 500 reasoning deltas in Basic mode, found {}.\n\n\
             Output excerpt (first 50 lines):\n{}",
            thinking_count,
            output.lines().take(50).collect::<Vec<_>>().join("\n")
        );
    });
}

#[test]
fn test_ccs_glm_multi_turn_extreme_streaming() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Simulate 3 turns, each with 200 deltas
        let mut stream = String::new();
        for turn in 0..3 {
            // Message start
            write!(stream,
                r#"{{"type":"stream_event","event":{{"type":"message_start","message":{{"id":"msg{turn}","content":[]}}}}}}"#
            ).unwrap();
            stream.push('\n');

            // Content block with 200 deltas
            stream.push_str(r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}"#);
            stream.push('\n');

            for i in 0..200 {
                write!(stream,
                    r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"turn{turn}word{i} "}}}}}}"#
                ).unwrap();
                stream.push('\n');
            }

            stream.push_str(
                r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}"#,
            );
            stream.push('\n');
            stream.push_str(r#"{"type":"stream_event","event":{"type":"message_stop"}}"#);
            stream.push('\n');
        }

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // With 3 turns and 200 deltas each (600 total deltas), we should see AT MOST 3 prefix lines
        // (one per turn completion), NOT 600 lines.
        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 3,
            "Expected <= 3 '[ccs/glm]' prefixes for 3-turn session with 600 total deltas, found {}.\n\n\
             Output excerpt (first 100 lines):\n{}",
            prefix_count,
            output.lines().take(100).collect::<Vec<_>>().join("\n")
        );

        // Verify all turns are present
        assert!(
            output.contains("turn0word0") && output.contains("turn2word199"),
            "Expected all turn content to be present. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_multi_item_extreme_streaming() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Simulate 3 reasoning items, each with 200 deltas
        let mut stream = String::new();
        stream.push_str(r#"{"type":"event","event":"session.started"}"#);
        stream.push('\n');

        for item in 0..3 {
            write!(stream,
                r#"{{"type":"event","event":"item.started","item_type":"reasoning","item_id":"r{item}"}}"#
            ).unwrap();
            stream.push('\n');

            for i in 0..200 {
                write!(stream,
                    r#"{{"type":"event","event":"item.content.delta","item_id":"r{item}","item_type":"reasoning","delta":"item{item}reason{i} "}}"#
                ).unwrap();
                stream.push('\n');
            }

            write!(stream,
                r#"{{"type":"event","event":"item.completed","item_type":"reasoning","item_id":"r{item}"}}"#
            ).unwrap();
            stream.push('\n');
        }

        stream.push_str(r#"{"type":"event","event":"session.done"}"#);
        stream.push('\n');

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // With 3 items and 200 deltas each (600 total), we should see AT MOST 3 "Thinking:" lines
        let thinking_count = output.matches("Thinking:").count();
        assert!(
            thinking_count <= 3,
            "Expected <= 3 'Thinking:' lines for 3-item session with 600 total deltas, found {}.\n\n\
             Output excerpt (first 100 lines):\n{}",
            thinking_count,
            output.lines().take(100).collect::<Vec<_>>().join("\n")
        );

        // Verify all items are present
        assert!(
            output.contains("item0reason0") && output.contains("item2reason199"),
            "Expected all item content to be present. Output:\n{output}"
        );
    });
}
