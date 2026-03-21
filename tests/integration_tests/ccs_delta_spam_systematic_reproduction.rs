//! Systematic CCS delta spam reproduction test (comprehensive verification).
//!
//! This test implements the systematic debugging protocol required by the bug fix plan:
//! - Reproduces spam for ALL delta types (text, thinking, tool input)
//! - Tests BOTH ccs/glm (`ClaudeParser`) and ccs/codex (`CodexParser`)
//! - Covers BOTH `TerminalMode::None` and `TerminalMode::Basic`
//! - Uses hard assertions with failure excerpts
//!
//! Purpose: Serve as comprehensive regression coverage that validates the three-layer
//! spam prevention architecture works correctly across ALL scenarios.
//!
//! Architecture Validation:
//! - Layer 1: Renderer suppression in non-TTY modes
//! - Layer 2: `StreamingSession` accumulation across deltas
//! - Layer 3: Parser flush at completion boundaries
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

// ============================================================================
// CCS/GLM (ClaudeParser) - Text Delta Tests
// ============================================================================

#[test]
fn test_ccs_glm_text_delta_spam_reproduction_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Generate 100 text deltas for a single block
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
"#,
        );

        for i in 0..100 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"word{i} "}}}}}}"#
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
            "SPAM DETECTED! Expected <= 1 '[ccs/glm]' prefix with 100 text deltas, found {}.\n\n\
             This indicates per-delta spam is occurring.\n\n\
             First 20 lines with prefix:\n{}",
            prefix_count,
            extract_spam_excerpt(&output, "[ccs/glm]", 20)
        );

        // Verify content is present
        assert!(
            output.contains("word0") && output.contains("word99"),
            "Expected accumulated content. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_text_delta_spam_reproduction_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        // Generate 100 text deltas for a single block
        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
"#,
        );

        for i in 0..100 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"word{i} "}}}}}}"#).unwrap();
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
            "SPAM DETECTED! Expected <= 1 '[ccs/glm]' prefix with 100 text deltas in Basic mode, found {}.\n\n\
             First 20 lines with prefix:\n{}",
            prefix_count,
            extract_spam_excerpt(&output, "[ccs/glm]", 20)
        );

        assert!(
            output.contains("word0") && output.contains("word99"),
            "Expected accumulated content. Output:\n{output}"
        );
    });
}

// ============================================================================
// CCS/GLM (ClaudeParser) - Thinking Delta Tests
// ============================================================================

#[test]
fn test_ccs_glm_thinking_delta_spam_reproduction_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
"#,
        );

        // 100 thinking deltas
        for i in 0..100 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"thinking_delta","thinking":"thought{i} "}}}}}}"#).unwrap();
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
            "SPAM DETECTED! Expected <= 1 'Thinking:' label with 100 thinking deltas, found {}.\n\n\
             First 20 lines:\n{}",
            thinking_count,
            extract_spam_excerpt(&output, "Thinking:", 20)
        );

        assert!(
            output.contains("thought0") && output.contains("thought99"),
            "Expected accumulated thinking content. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_thinking_delta_spam_reproduction_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
"#,
        );

        for i in 0..100 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"thinking_delta","thinking":"thought{i} "}}}}}}"#).unwrap();
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
            "SPAM DETECTED! Expected <= 1 'Thinking:' label with 100 thinking deltas in Basic mode, found {}.\n\n\
             First 20 lines:\n{}",
            thinking_count,
            extract_spam_excerpt(&output, "Thinking:", 20)
        );

        assert!(
            output.contains("thought0") && output.contains("thought99"),
            "Expected accumulated thinking content. Output:\n{output}"
        );
    });
}

// ============================================================================
// CCS/GLM (ClaudeParser) - Tool Input Delta Tests
// ============================================================================

#[test]
fn test_ccs_glm_tool_input_delta_spam_reproduction_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Verbose; // Need verbose to see tool input

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tool1","name":"read_file"}}}
"#,
        );

        // 50 tool input deltas (simulating partial JSON chunks)
        for i in 0..50 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"tool_use_delta","tool_use":{{"input":"chunk{i} "}}}}}}}}"#).unwrap();
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
        let tool_input_count = output.matches("Tool input:").count();

        assert!(
            tool_input_count <= 1,
            "SPAM DETECTED! Expected <= 1 'Tool input:' label with 50 tool input deltas, found {}.\n\n\
             First 20 lines:\n{}",
            tool_input_count,
            extract_spam_excerpt(&output, "Tool input:", 20)
        );

        assert!(
            output.contains("chunk0") && output.contains("chunk49"),
            "Expected accumulated tool input content. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_tool_input_delta_spam_reproduction_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Verbose;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tool1","name":"read_file"}}}
"#,
        );

        for i in 0..50 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"tool_use_delta","tool_use":{{"input":"chunk{i} "}}}}}}}}"#).unwrap();
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
        let tool_input_count = output.matches("Tool input:").count();

        assert!(
            tool_input_count <= 1,
            "SPAM DETECTED! Expected <= 1 'Tool input:' label with 50 tool input deltas in Basic mode, found {}.\n\n\
             First 20 lines:\n{}",
            tool_input_count,
            extract_spam_excerpt(&output, "Tool input:", 20)
        );

        assert!(
            output.contains("chunk0") && output.contains("chunk49"),
            "Expected accumulated tool input content. Output:\n{output}"
        );
    });
}

// ============================================================================
// CCS/GLM (ClaudeParser) - Multi-Block Test
// ============================================================================

#[test]
fn test_ccs_glm_multi_block_spam_reproduction_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        let mut stream = String::from(
            r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
"#,
        );

        // Block 0: 50 text deltas
        for i in 0..50 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"b0w{i} "}}}}}}"#).unwrap();
        }

        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}}
"#,
        );

        // Block 1: 50 text deltas
        for i in 0..50 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":1,"delta":{{"type":"text_delta","text":"b1w{i} "}}}}}}"#).unwrap();
        }

        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":1}}
{"type":"stream_event","event":{"type":"content_block_start","index":2,"content_block":{"type":"text","text":""}}}
"#,
        );

        // Block 2: 50 text deltas
        for i in 0..50 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":2,"delta":{{"type":"text_delta","text":"b2w{i} "}}}}}}"#).unwrap();
        }

        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":2}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        let prefix_count = count_prefixed_lines(&output, "[ccs/glm]");

        assert!(
            prefix_count <= 3,
            "SPAM DETECTED! Expected <= 3 '[ccs/glm]' prefixes (one per block) with 3 blocks × 50 deltas = 150 deltas, found {}.\n\n\
             First 20 lines with prefix:\n{}",
            prefix_count,
            extract_spam_excerpt(&output, "[ccs/glm]", 20)
        );

        // Verify all blocks are present
        assert!(
            output.contains("b0w0") && output.contains("b0w49"),
            "Expected block 0 content. Output:\n{output}"
        );
        assert!(
            output.contains("b1w0") && output.contains("b1w49"),
            "Expected block 1 content. Output:\n{output}"
        );
        assert!(
            output.contains("b2w0") && output.contains("b2w49"),
            "Expected block 2 content. Output:\n{output}"
        );
    });
}

// ============================================================================
// CCS/GLM (ClaudeParser) - Mode Consistency Test
// ============================================================================

#[test]
fn test_ccs_glm_mode_consistency_same_stream_none_vs_basic() {
    with_default_timeout(|| {
        // Test the same stream in both None and Basic modes
        // Both should produce the same prefix count

        let stream_template = |mode: TerminalMode| {
            let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
            let colors = Colors::new();
            let verbosity = Verbosity::Normal;

            let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
                .with_display_name("ccs/glm")
                .with_terminal_mode(mode);

            let mut stream = String::from(
                r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
"#,
            );

            for i in 0..100 {
                writeln!(stream,
                    r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"word{i} "}}}}}}"#).unwrap();
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
            count_prefixed_lines(&output, "[ccs/glm]")
        };

        let none_count = stream_template(TerminalMode::None);
        let basic_count = stream_template(TerminalMode::Basic);

        assert_eq!(
            none_count, basic_count,
            "Prefix count mismatch! None mode produced {none_count} prefixes, Basic mode produced {basic_count}.\n\
             Both non-TTY modes should suppress per-delta output identically."
        );

        assert!(
            none_count <= 1,
            "Both modes produced spam: {none_count} prefixes found"
        );
    });
}

// ============================================================================
// CCS/CODEX (CodexParser) - Agent Message Delta Tests
// ============================================================================

#[test]
fn test_ccs_codex_agent_message_delta_spam_reproduction_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Codex uses {"type":"item.started",...} format, not stream_event
        let mut stream = String::new();

        // 100 agent message deltas
        for i in 0..100 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"agent_message","text":"word{i} "}}}}"#
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
        let prefix_count = count_prefixed_lines(&output, "[ccs/codex]");

        assert!(
            prefix_count <= 1,
            "SPAM DETECTED! Expected <= 1 '[ccs/codex]' prefix with 100 agent message deltas, found {}.\n\n\
             First 20 lines with prefix:\n{}",
            prefix_count,
            extract_spam_excerpt(&output, "[ccs/codex]", 20)
        );

        assert!(
            output.contains("word0") && output.contains("word99"),
            "Expected accumulated content. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_agent_message_delta_spam_reproduction_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::Basic);

        let mut stream = String::new();

        for i in 0..100 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"agent_message","text":"word{i} "}}}}"#
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
        let prefix_count = count_prefixed_lines(&output, "[ccs/codex]");

        assert!(
            prefix_count <= 1,
            "SPAM DETECTED! Expected <= 1 '[ccs/codex]' prefix with 100 agent message deltas in Basic mode, found {}.\n\n\
             First 20 lines with prefix:\n{}",
            prefix_count,
            extract_spam_excerpt(&output, "[ccs/codex]", 20)
        );

        assert!(
            output.contains("word0") && output.contains("word99"),
            "Expected accumulated content. Output:\n{output}"
        );
    });
}

// ============================================================================
// CCS/CODEX (CodexParser) - Reasoning Delta Tests
// ============================================================================

#[test]
fn test_ccs_codex_reasoning_delta_spam_reproduction_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        let mut stream = String::new();

        // 100 reasoning deltas
        for i in 0..100 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"reasoning","text":"thought{i} "}}}}"#
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
            "SPAM DETECTED! Expected <= 1 'Thinking:' label with 100 reasoning deltas, found {}.\n\n\
             First 20 lines:\n{}",
            thinking_count,
            extract_spam_excerpt(&output, "Thinking:", 20)
        );

        assert!(
            output.contains("thought0") && output.contains("thought99"),
            "Expected accumulated reasoning content. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_reasoning_delta_spam_reproduction_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::Basic);

        let mut stream = String::new();

        for i in 0..100 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"reasoning","text":"thought{i} "}}}}"#
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
            "SPAM DETECTED! Expected <= 1 'Thinking:' label with 100 reasoning deltas in Basic mode, found {}.\n\n\
             First 20 lines:\n{}",
            thinking_count,
            extract_spam_excerpt(&output, "Thinking:", 20)
        );

        assert!(
            output.contains("thought0") && output.contains("thought99"),
            "Expected accumulated reasoning content. Output:\n{output}"
        );
    });
}
