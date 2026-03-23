//! Ultra-comprehensive CCS delta spam reproduction test.
//!
//! This test serves as a final verification that CCS agents do NOT spam repeated
//! prefixed lines for ANY delta type in non-TTY modes, covering edge cases that
//! might not be tested elsewhere.
//!
//! Bug hypothesis: Despite existing fixes, there might be edge cases where per-delta
//! spam still occurs (e.g., rapid successive deltas, cross-block contamination,
//! multi-turn edge cases).
//!
//! This test is designed to FAIL if the bug exists and PASS if properly fixed.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::{count_prefixed_lines, extract_spam_excerpt};
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

const ASCII_LOWERCASE: &[u8; 26] = b"abcdefghijklmnopqrstuvwxyz";

// ============================================================================
// CCS/GLM (ClaudeParser) Edge Case Tests
// ============================================================================

#[test]
fn test_ccs_glm_ultra_extreme_text_deltas_1000_chunks_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Generate 1000 text deltas for a single block (ultra-extreme stress test)
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
"#,
        );

        for i in 0..1000 {
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
        let prefix_count = count_prefixed_lines(&output, "[ccs/glm]");

        assert!(
            prefix_count <= 1,
            "SPAM DETECTED! Expected <= 1 '[ccs/glm]' prefix with 1000 text deltas in None mode, found {}.\n\n\
             This indicates per-delta spam is occurring!\n\n\
             First 20 lines with prefix:\n{}",
            prefix_count,
            extract_spam_excerpt(&output, "[ccs/glm]", 20)
        );

        // Verify content is present (not lost)
        assert!(
            output.contains("w0") && output.contains("w999"),
            "Expected accumulated content to contain first and last words. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_rapid_successive_thinking_deltas_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Test rapid successive thinking deltas with minimal content
        // This might expose issues with delta accumulation timing
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
"#,
        );

        // 200 very small deltas (single characters)
        for i in 0usize..200 {
            write!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"thinking_delta","thinking":"{}"}}}}}}"#,
                char::from(ASCII_LOWERCASE[i % ASCII_LOWERCASE.len()])
            ).unwrap();
            stream.push('\n');
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
        let thinking_count = output.matches("Thinking:").count();

        assert!(
            thinking_count <= 1,
            "SPAM DETECTED! Expected <= 1 'Thinking:' label with 200 rapid deltas in None mode, found {}.\n\n\
             First 20 lines:\n{}",
            thinking_count,
            extract_spam_excerpt(&output, "Thinking:", 20)
        );
    });
}

#[test]
fn test_ccs_glm_interleaved_blocks_with_many_deltas_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Test: multiple blocks, each with many deltas, to ensure no cross-contamination
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
"#,
        );

        // Thinking block 0 with 50 deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
"#,
        );
        for i in 0..50 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"thinking_delta","thinking":"t0_{i} "}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
"#,
        );

        // Text block 1 with 75 deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}}
"#,
        );
        for i in 0..75 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":1,"delta":{{"type":"text_delta","text":"txt1_{i} "}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":1}}
"#,
        );

        // Text block 2 with 60 deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":2,"content_block":{"type":"text","text":""}}}
"#,
        );
        for i in 0..60 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":2,"delta":{{"type":"text_delta","text":"txt2_{i} "}}}}}}"#).unwrap();
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
        let prefix_count = count_prefixed_lines(&output, "[ccs/glm]");

        // Should have AT MOST 3 prefixes (one per block: 1 thinking + 2 text)
        // With 185 total deltas, if spam exists we'd see many more
        assert!(
            prefix_count <= 3,
            "SPAM DETECTED! Expected <= 3 '[ccs/glm]' prefixes for 3 blocks with 185 deltas, found {}.\n\n\
             First 20 lines:\n{}",
            prefix_count,
            extract_spam_excerpt(&output, "[ccs/glm]", 20)
        );

        // Verify all blocks are present
        assert!(output.contains("t0_"), "Expected thinking block 0 content");
        assert!(output.contains("txt1_"), "Expected text block 1 content");
        assert!(output.contains("txt2_"), "Expected text block 2 content");
    });
}

// ============================================================================
// CCS/Codex Edge Case Tests
// ============================================================================

#[test]
fn test_ccs_codex_ultra_extreme_reasoning_deltas_1000_chunks_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Generate 1000 reasoning deltas
        let mut stream = String::new();
        for i in 0..1000 {
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
        let thinking_count = output.matches("Thinking:").count();

        assert!(
            thinking_count <= 1,
            "SPAM DETECTED! Expected <= 1 'Thinking:' label with 1000 reasoning deltas in None mode, found {}.\n\n\
             First 20 lines:\n{}",
            thinking_count,
            extract_spam_excerpt(&output, "Thinking:", 20)
        );

        // Verify content is present
        assert!(
            output.contains("r0") && output.contains("r999"),
            "Expected accumulated reasoning content. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_rapid_agent_message_deltas_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Test rapid agent_message deltas with single characters
        let mut stream = String::new();
        for i in 0usize..200 {
            write!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"agent_message","text":"{}"}}}}"#,
                char::from(ASCII_LOWERCASE[i % ASCII_LOWERCASE.len()])
            )
            .unwrap();
            stream.push('\n');
        }
        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"agent_message"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        let prefix_count = count_prefixed_lines(&output, "[ccs/codex]");

        assert!(
            prefix_count <= 1,
            "SPAM DETECTED! Expected <= 1 '[ccs/codex]' prefix with 200 rapid agent_message deltas in None mode, found {}.\n\n\
             First 20 lines:\n{}",
            prefix_count,
            extract_spam_excerpt(&output, "[ccs/codex]", 20)
        );
    });
}

#[test]
fn test_ccs_codex_multi_item_interleaved_deltas_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Test multiple reasoning items with many deltas each
        let mut stream = String::new();

        // Item 1: 80 reasoning deltas
        for i in 0..80 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"reasoning","text":"item1_r{i} "}}}}"#
            )
            .unwrap();
        }
        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"reasoning"}}
"#,
        );

        // Item 2: 70 agent_message deltas
        for i in 0..70 {
            writeln!(stream,
                r#"{{"type":"item.started","item":{{"type":"agent_message","text":"item2_msg{i} "}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"agent_message"}}
"#,
        );

        // Item 3: 90 reasoning deltas
        for i in 0..90 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"reasoning","text":"item3_r{i} "}}}}"#
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
        let prefix_count = count_prefixed_lines(&output, "[ccs/codex]");

        // Should have AT MOST 3 prefixes (one per item completion: 2 reasoning + 1 agent_message)
        // With 240 total deltas, if spam exists we'd see many more
        assert!(
            prefix_count <= 3,
            "SPAM DETECTED! Expected <= 3 '[ccs/codex]' prefixes for 3 items with 240 deltas, found {}.\n\n\
             First 20 lines:\n{}",
            prefix_count,
            extract_spam_excerpt(&output, "[ccs/codex]", 20)
        );

        // Verify all items are present
        assert!(
            output.contains("item1_r"),
            "Expected item 1 reasoning content"
        );
        assert!(
            output.contains("item2_msg"),
            "Expected item 2 message content"
        );
        assert!(
            output.contains("item3_r"),
            "Expected item 3 reasoning content"
        );
    });
}

// ============================================================================
// Cross-Mode Verification Tests
// ============================================================================

#[test]
fn test_ccs_glm_same_stream_different_modes_consistency() {
    with_default_timeout(|| {
        // Generate the same stream
        let stream = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        // Test None mode
        let test_printer_none = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser_none =
            ClaudeParser::with_printer(colors, verbosity, test_printer_none.clone())
                .with_display_name("ccs/glm")
                .with_terminal_mode(TerminalMode::None);

        let reader_none = BufReader::new(stream.as_bytes());
        let workspace_none = MemoryWorkspace::new_test();
        parser_none
            .parse_stream(reader_none, &workspace_none)
            .unwrap();

        let output_none = test_printer_none.borrow().get_output();
        let prefix_count_none = count_prefixed_lines(&output_none, "[ccs/glm]");

        // Test Basic mode
        let test_printer_basic = Rc::new(RefCell::new(TestPrinter::new()));
        let mut parser_basic =
            ClaudeParser::with_printer(colors, verbosity, test_printer_basic.clone())
                .with_display_name("ccs/glm")
                .with_terminal_mode(TerminalMode::Basic);

        let reader_basic = BufReader::new(stream.as_bytes());
        let workspace_basic = MemoryWorkspace::new_test();
        parser_basic
            .parse_stream(reader_basic, &workspace_basic)
            .unwrap();

        let output_basic = test_printer_basic.borrow().get_output();
        let prefix_count_basic = count_prefixed_lines(&output_basic, "[ccs/glm]");

        // Both modes should produce the same number of prefix lines (AT MOST 1)
        assert!(
            prefix_count_none <= 1,
            "None mode: Expected <= 1 prefix, found {prefix_count_none}"
        );
        assert!(
            prefix_count_basic <= 1,
            "Basic mode: Expected <= 1 prefix, found {prefix_count_basic}"
        );

        // Both should contain the content
        assert!(output_none.contains("Hello World!"));
        assert!(output_basic.contains("Hello World!"));
    });
}
