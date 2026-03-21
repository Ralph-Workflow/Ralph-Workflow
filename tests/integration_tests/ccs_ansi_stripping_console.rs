//! ANSI-stripping console tests for CCS streaming.
//!
//! These tests simulate CI/log environments that strip or ignore ANSI escape sequences.
//! The append-only pattern should work correctly even when ANSI is stripped, producing
//! clean single-line output without repeated prefixes.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::config::Verbosity;
use ralph_workflow::json_parser::claude::ClaudeParser;
use ralph_workflow::json_parser::printer::VirtualTerminal;
use ralph_workflow::json_parser::terminal::TerminalMode;
use ralph_workflow::logger::Colors;
use ralph_workflow::workspace::MemoryWorkspace;
use std::cell::RefCell;
use std::io::{BufReader, Write};
use std::rc::Rc;

#[test]
fn test_append_only_works_when_ansi_stripped() {
    with_default_timeout(|| {
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, terminal.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        // Stream multiple deltas
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello World"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // Get output with ANSI stripped (simulates CI log)
        let ansi_stripped = term.get_visible_output_ansi_stripped();

        // Assert: prefix appears exactly once
        assert_eq!(
            ansi_stripped.matches("[ccs/glm]").count(),
            1,
            "Prefix should appear once even when ANSI is stripped"
        );

        // Assert: no excessive newlines (only 1 line for the content)
        let lines: Vec<&str> = ansi_stripped.lines().collect();
        let non_empty_lines: Vec<&str> = lines
            .iter()
            .filter(|l| !l.trim().is_empty())
            .copied()
            .collect();
        assert_eq!(
            non_empty_lines.len(),
            1,
            "Should have exactly 1 line of content when ANSI stripped, got: {non_empty_lines:?}"
        );
    });
}

#[test]
fn test_legacy_pattern_fails_when_ansi_stripped() {
    // This test documents the OLD broken behavior for comparison
    // It shows what happens when newline + cursor-up is used and ANSI is stripped
    with_default_timeout(|| {
        let mut term = VirtualTerminal::new();

        // Simulate legacy pattern: prefix + content + \n\x1b[1A (newline + cursor up)
        write!(term, "[ccs/glm] Hello\n\x1b[1A").unwrap();
        write!(term, "\x1b[2K\r[ccs/glm] Hello World\n\x1b[1A").unwrap();
        writeln!(term).unwrap(); // Completion

        // Get output with ANSI stripped
        let ansi_stripped = term.get_visible_output_ansi_stripped();

        // Assert: With ANSI stripped, the \n becomes literal newlines
        let lines: Vec<&str> = ansi_stripped.lines().collect();
        let prefix_lines: Vec<&str> = lines
            .iter()
            .filter(|l| l.contains("[ccs/glm]"))
            .copied()
            .collect();

        // Under this helper's model, ANSI is stripped but `\r` still overwrites within a line.
        // The legacy pattern relies on `\n` + cursor-up to rewrite; if cursor-up is ignored/stripped,
        // the literal newlines remain and cause repeated visible prefix lines.
        assert!(
            prefix_lines.len() > 1,
            "Legacy pattern should create multiple lines when ANSI is stripped and cursor-up is ignored. Lines: {prefix_lines:?}"
        );
    });
}

#[test]
fn test_thinking_append_only_works_when_ansi_stripped() {
    with_default_timeout(|| {
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, terminal.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        // Stream thinking deltas
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"I am thinking"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"I am thinking about the problem"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // Get output with ANSI stripped
        let ansi_stripped = term.get_visible_output_ansi_stripped();

        // Assert: "Thinking:" label appears exactly once
        assert_eq!(
            ansi_stripped.matches("Thinking:").count(),
            1,
            "Thinking label should appear once even when ANSI is stripped"
        );

        // Assert: prefix appears exactly once
        assert_eq!(
            ansi_stripped.matches("[ccs/glm]").count(),
            1,
            "Prefix should appear once even when ANSI is stripped"
        );

        // Assert: no excessive newlines
        let lines: Vec<&str> = ansi_stripped.lines().collect();
        let non_empty_lines: Vec<&str> = lines
            .iter()
            .filter(|l| !l.trim().is_empty())
            .copied()
            .collect();
        assert_eq!(
            non_empty_lines.len(),
            1,
            "Should have exactly 1 line of thinking content when ANSI stripped, got: {non_empty_lines:?}"
        );
    });
}

#[test]
fn test_multiple_content_blocks_ansi_stripped() {
    with_default_timeout(|| {
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new()));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, terminal.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        // Stream with thinking + text blocks
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Let me think"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"Here is my response"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":1}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // Get output with ANSI stripped
        let ansi_stripped = term.get_visible_output_ansi_stripped();

        // Assert: prefix appears exactly twice (once per block)
        assert_eq!(
            ansi_stripped.matches("[ccs/glm]").count(),
            2,
            "Prefix should appear exactly twice (one per content block)"
        );

        // Assert: thinking label appears once
        assert_eq!(
            ansi_stripped.matches("Thinking:").count(),
            1,
            "Thinking label should appear once"
        );

        // Assert: exactly 2 lines (thinking + text)
        let lines: Vec<&str> = ansi_stripped.lines().collect();
        let non_empty_lines: Vec<&str> = lines
            .iter()
            .filter(|l| !l.trim().is_empty())
            .copied()
            .collect();
        assert_eq!(
            non_empty_lines.len(),
            2,
            "Should have exactly 2 lines (thinking + text) when ANSI stripped, got: {non_empty_lines:?}"
        );
    });
}
