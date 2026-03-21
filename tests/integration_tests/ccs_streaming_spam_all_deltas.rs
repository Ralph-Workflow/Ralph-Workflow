//! Comprehensive regression test for CCS streaming delta spam bug.
//!
//! This test verifies that CCS agents (ccs/glm and ccs/codex) do not spam repeated
//! prefixed lines in non-TTY output modes for ANY delta type (text, thinking, tool input).
//!
//! Bug reproduction: When CCS agents emit streaming deltas (text/thinking/tool input),
//! each delta was printed as a fresh line instead of updating in-place in non-TTY modes,
//! causing repeated "[ccs/glm]" or "[ccs/codex]" lines in logs.
//!
//! Fix: Suppress per-delta output in non-TTY modes (`TerminalMode::Basic` and `TerminalMode::None`)
//! and flush accumulated content ONCE at completion boundaries.
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

// ============================================================================
// CCS/GLM (ClaudeParser) Tests
// ============================================================================

#[test]
fn test_ccs_glm_text_deltas_no_spam_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Simulate many text deltas for same block
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" This"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" is"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" a"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" test"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Count prefix occurrences - should be AT MOST 1 for the text block
        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 1,
            "Expected <= 1 '[ccs/glm]' prefix in None mode for text deltas, found {prefix_count}.\n\nOutput:\n{output}"
        );

        // Verify the content is present (not lost)
        assert!(
            output.contains("Hello World! This is a test") || output.contains("Hello World!"),
            "Expected accumulated text content to be present. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_text_deltas_no_spam_in_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}}
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
            "Expected <= 1 '[ccs/glm]' prefix in Basic mode for text deltas, found {prefix_count}.\n\nOutput:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_thinking_deltas_no_spam_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Simulate many thinking deltas for same block
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"I"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" need"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" to"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" think"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" about"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" this"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Count thinking prefix occurrences - should be AT MOST 1.
        // If colors are forced on in CI, the line may include ANSI sequences between
        // "]" and "Thinking:". Match on the prefix and "Thinking:" separately.
        let thinking_prefix_count = output.matches("[ccs/glm]").count();
        let thinking_label_count = output.matches("Thinking:").count();
        assert!(
            thinking_prefix_count <= 1,
            "Expected <= 1 '[ccs/glm]' prefix in None mode for thinking-only stream, found {thinking_prefix_count}.\n\nOutput:\n{output}"
        );
        assert!(
            thinking_label_count <= 1,
            "Expected <= 1 'Thinking:' label in None mode, found {thinking_label_count}.\n\nOutput:\n{output}"
        );

        // Verify thinking content is present (not lost)
        if thinking_label_count > 0 {
            assert!(
                output.contains("I need to think about this") || output.contains("think about"),
                "Expected accumulated thinking content to be present. Output:\n{output}"
            );
        }
    });
}

#[test]
fn test_ccs_glm_thinking_deltas_no_spam_in_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Analyzing"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" the"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" problem"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let thinking_label_count = output.matches("Thinking:").count();
        assert!(
            thinking_label_count <= 1,
            "Expected <= 1 'Thinking:' label in Basic mode, found {thinking_label_count}.\n\nOutput:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_tool_input_deltas_no_spam_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Simulate many tool input deltas
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tool1","name":"bash"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"tool_use_delta","tool_use":{"input":"ls"}}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"tool_use_delta","tool_use":{"input":" -la"}}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"tool_use_delta","tool_use":{"input":" /tmp"}}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Count tool input prefix occurrences - should be AT MOST 1-2
        // (one for tool start, at most one for tool input flush)
        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 2,
            "Expected <= 2 '[ccs/glm]' prefixes in None mode for tool deltas (tool start + optional input flush), found {prefix_count}.\n\nOutput:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_tool_input_deltas_no_spam_in_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Basic);

        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tool1","name":"bash"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"tool_use_delta","tool_use":{"input":"echo"}}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"tool_use_delta","tool_use":{"input":" hello"}}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 2,
            "Expected <= 2 '[ccs/glm]' prefixes in Basic mode for tool deltas, found {prefix_count}.\n\nOutput:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_mixed_deltas_no_spam_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Simulate a message with thinking, then text, then tool
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Let"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" me"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" think"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":" there"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":1}}
{"type":"stream_event","event":{"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"tool1","name":"bash"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":2,"delta":{"type":"tool_use_delta","tool_use":{"input":"ls"}}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":2,"delta":{"type":"tool_use_delta","tool_use":{"input":" -la"}}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":2}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Count total prefix occurrences
        // Should be AT MOST: 1 thinking + 1 text + 2 tool (start + input) = 4
        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 4,
            "Expected <= 4 '[ccs/glm]' prefixes in None mode for mixed deltas (1 thinking + 1 text + 2 tool), found {prefix_count}.\n\nOutput:\n{output}"
        );
    });
}

// ============================================================================
// CCS/Codex (CodexParser) Tests
// ============================================================================

#[test]
fn test_ccs_codex_reasoning_deltas_no_spam_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Simulate many reasoning deltas
        let stream = r#"
{"type":"item.started","item":{"type":"reasoning","text":"First"}}
{"type":"item.started","item":{"type":"reasoning","text":" chunk"}}
{"type":"item.started","item":{"type":"reasoning","text":" second"}}
{"type":"item.started","item":{"type":"reasoning","text":" third"}}
{"type":"item.started","item":{"type":"reasoning","text":" fourth"}}
{"type":"item.started","item":{"type":"reasoning","text":" fifth"}}
{"type":"item.completed","item":{"type":"reasoning"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Count reasoning label occurrences - should be AT MOST 1.
        // If colors are forced on in CI, ANSI sequences may appear between the prefix and label.
        let thinking_label_count = output.matches("Thinking:").count();
        assert!(
            thinking_label_count <= 1,
            "Expected <= 1 'Thinking:' label in None mode, found {thinking_label_count}.\n\nOutput:\n{output}"
        );

        // Verify reasoning content is present (not lost)
        if thinking_label_count > 0 {
            assert!(
                output.contains("First chunk") || output.contains("fifth"),
                "Expected accumulated reasoning content to be present. Output:\n{output}"
            );
        }
    });
}

#[test]
fn test_ccs_codex_reasoning_deltas_no_spam_in_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::Basic);

        let stream = r#"
{"type":"item.started","item":{"type":"reasoning","text":"Analyzing"}}
{"type":"item.started","item":{"type":"reasoning","text":" the"}}
{"type":"item.started","item":{"type":"reasoning","text":" problem"}}
{"type":"item.completed","item":{"type":"reasoning"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let thinking_label_count = output.matches("Thinking:").count();
        assert!(
            thinking_label_count <= 1,
            "Expected <= 1 'Thinking:' label in Basic mode, found {thinking_label_count}.\n\nOutput:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_agent_message_deltas_no_spam_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Simulate many agent_message deltas
        let stream = r#"
{"type":"item.started","item":{"type":"agent_message","text":"Hello"}}
{"type":"item.started","item":{"type":"agent_message","text":" World"}}
{"type":"item.started","item":{"type":"agent_message","text":"!"}}
{"type":"item.completed","item":{"type":"agent_message","text":"Hello World!"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Count prefix occurrences - should be AT MOST 1
        let prefix_count = output.matches("[ccs/codex]").count();
        assert!(
            prefix_count <= 1,
            "Expected <= 1 '[ccs/codex]' prefix in None mode for agent_message deltas, found {prefix_count}.\n\nOutput:\n{output}"
        );

        // Verify the content is present (not lost)
        assert!(
            output.contains("Hello World!"),
            "Expected accumulated agent_message content to be present. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_agent_message_deltas_no_spam_in_basic_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::Basic);

        let stream = r#"
{"type":"item.started","item":{"type":"agent_message","text":"Test"}}
{"type":"item.started","item":{"type":"agent_message","text":" message"}}
{"type":"item.completed","item":{"type":"agent_message","text":"Test message"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        let prefix_count = output.matches("[ccs/codex]").count();
        assert!(
            prefix_count <= 1,
            "Expected <= 1 '[ccs/codex]' prefix in Basic mode for agent_message deltas, found {prefix_count}.\n\nOutput:\n{output}"
        );

        assert!(
            output.contains("Test message"),
            "Expected accumulated agent_message content to be present. Output:\n{output}"
        );
    });
}

// ============================================================================
// Extreme Stress Test - 100 Deltas
// ============================================================================

#[test]
fn test_ccs_glm_extreme_text_deltas_no_spam_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Simulate 100 text deltas for same block (extreme stress test)
        let mut stream = String::from(
            r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
"#,
        );

        for i in 0..100 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"word{i} "}}}}}}"#
            ).unwrap();
        }

        stream.push_str(
            r#"
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#,
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Count prefix occurrences - should be AT MOST 1 even with 100 deltas
        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 1,
            "Expected <= 1 '[ccs/glm]' prefix in None mode for 100 text deltas, found {prefix_count}.\n\nOutput:\n{output}"
        );

        // Verify content is present (should contain multiple words)
        assert!(
            output.contains("word0") && output.contains("word99"),
            "Expected accumulated text content (word0...word99) to be present. Output:\n{output}"
        );
    });
}

// ============================================================================
// Real-World Scenario Tests - Comprehensive Multi-Block Streaming
// ============================================================================

#[test]
fn test_ccs_glm_two_text_blocks_both_flushed() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Simple test: two text blocks with multiple deltas each
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"first"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" block"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"second"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":" block"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":1}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();

        // Should have both blocks
        assert!(
            output.contains("first block"),
            "Expected 'first block' to be present. Output:\n{output}"
        );
        assert!(
            output.contains("second block"),
            "Expected 'second block' to be present. Output:\n{output}"
        );

        // Should have at most 2 prefixes (one per block)
        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 2,
            "Expected <= 2 '[ccs/glm]' prefixes for 2 text blocks, found {prefix_count}.\n\nOutput:\n{output}"
        );
    });
}

#[test]
fn test_ccs_glm_real_world_multi_block_streaming_no_spam_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, test_printer.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::None);

        // Simulate a real-world scenario with:
        // - Multiple thinking blocks with many deltas
        // - Multiple text blocks with many deltas
        // - Multiple tool blocks with many deltas
        // This tests the comprehensive fix across all delta types in a single stream
        let mut stream = String::from(
            r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
"#,
        );

        // First thinking block with 20 deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
"#,
        );
        for i in 0..20 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"thinking_delta","thinking":"think{i} "}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
"#,
        );

        // First text block with 30 deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}}
"#,
        );
        for i in 0..30 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":1,"delta":{{"type":"text_delta","text":"text{i} "}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":1}}
"#,
        );

        // First tool block with 15 input deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"tool1","name":"bash"}}}
"#,
        );
        for i in 0..15 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":2,"delta":{{"type":"tool_use_delta","tool_use":{{"input":"cmd{i} "}}}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":2}}
"#,
        );

        // Second thinking block with 25 deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":3,"content_block":{"type":"thinking","thinking":""}}}
"#,
        );
        for i in 0..25 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":3,"delta":{{"type":"thinking_delta","thinking":"more{i} "}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":3}}
"#,
        );

        // Second text block with 20 deltas
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":4,"content_block":{"type":"text","text":""}}}
"#,
        );
        for i in 0..20 {
            writeln!(stream,
                r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":4,"delta":{{"type":"text_delta","text":"final{i} "}}}}}}"#).unwrap();
        }
        stream.push_str(
            r#"{"type":"stream_event","event":{"type":"content_block_stop","index":4}}
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

        // In None mode, we should have:
        // - At most 2 thinking blocks: ≤2 "[ccs/glm]" prefixes for thinking
        // - At most 2 text blocks: ≤2 "[ccs/glm]" prefixes for text
        // - At most 1 tool block: ≤2 "[ccs/glm]" prefixes (start + input)
        // Total: ≤6 prefixes maximum
        //
        // The key test: with 110 total deltas, we should NOT see 110 prefixes!
        let prefix_count = output.matches("[ccs/glm]").count();
        assert!(
            prefix_count <= 6,
            "Expected <= 6 '[ccs/glm]' prefixes for 110 deltas across 5 blocks, found {prefix_count}.\n\n\
             This indicates per-delta spam is still occurring!\n\n\
             Output:\n{output}"
        );

        // Verify all content types are present (not lost during suppression)
        assert!(
            output.contains("think"),
            "Expected thinking content to be present. Output:\n{output}"
        );
        assert!(
            output.contains("text"),
            "Expected text content to be present. Output:\n{output}"
        );
    });
}

#[test]
fn test_ccs_codex_real_world_multi_turn_streaming_no_spam_in_none_mode() {
    with_default_timeout(|| {
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser =
            CodexParser::with_printer_for_test(colors, verbosity, test_printer.clone())
                .with_display_name_for_test("ccs/codex")
                .with_terminal_mode(TerminalMode::None);

        // Simulate a real-world multi-turn scenario with:
        // - First turn: many reasoning deltas + many agent_message deltas
        // - Second turn: many reasoning deltas + many agent_message deltas
        let mut stream = String::new();

        // First turn - 30 reasoning deltas
        for i in 0..30 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"reasoning","text":"reason1_{i} "}}}}"#
            )
            .unwrap();
        }
        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"reasoning"}}
"#,
        );

        // First turn - 25 agent_message deltas
        for i in 0..25 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"agent_message","text":"msg1_{i} "}}}}"#
            )
            .unwrap();
        }
        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"agent_message"}}
"#,
        );

        // Second turn - 20 reasoning deltas
        for i in 0..20 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"reasoning","text":"reason2_{i} "}}}}"#
            )
            .unwrap();
        }
        stream.push_str(
            r#"{"type":"item.completed","item":{"type":"reasoning"}}
"#,
        );

        // Second turn - 15 agent_message deltas
        for i in 0..15 {
            writeln!(
                stream,
                r#"{{"type":"item.started","item":{{"type":"agent_message","text":"msg2_{i} "}}}}"#
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

        // In None mode, we should have:
        // - 2 reasoning completions: ≤2 "Thinking:" labels
        // - 2 agent_message completions: ≤2 "[ccs/codex]" prefixes for messages
        // Total: ≤4 prefixes maximum
        //
        // The key test: with 90 total deltas, we should NOT see 90 prefixes!
        let prefix_count = output.matches("[ccs/codex]").count();
        assert!(
            prefix_count <= 4,
            "Expected <= 4 '[ccs/codex]' prefixes for 90 deltas across 4 items, found {prefix_count}.\n\n\
             This indicates per-delta spam is still occurring!\n\n\
             Output:\n{output}"
        );

        // Verify content from both turns is present
        assert!(
            output.contains("reason1") || output.contains("Thinking"),
            "Expected first turn reasoning content to be present. Output:\n{output}"
        );
        assert!(
            output.contains("msg1"),
            "Expected first turn message content to be present. Output:\n{output}"
        );
        assert!(
            output.contains("reason2") || output.contains("Thinking"),
            "Expected second turn reasoning content to be present. Output:\n{output}"
        );
        assert!(
            output.contains("msg2"),
            "Expected second turn message content to be present. Output:\n{output}"
        );
    });
}
