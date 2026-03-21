//! Comprehensive wrapping tests for CCS streaming.
//!
//! These tests verify that the append-only streaming pattern works correctly
//! across various wrapping scenarios: boundary conditions, unicode characters,
//! narrow terminals, and multiple deltas causing incremental wrapping.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::config::Verbosity;
use ralph_workflow::json_parser::claude::ClaudeParser;
use ralph_workflow::json_parser::codex::CodexParser;
use ralph_workflow::json_parser::printer::VirtualTerminal;
use ralph_workflow::json_parser::terminal::TerminalMode;
use ralph_workflow::logger::Colors;
use ralph_workflow::workspace::MemoryWorkspace;
use std::cell::RefCell;
use std::io::BufReader;
use std::rc::Rc;

// ============================================================================
// Claude (ClaudeParser) Wrapping Tests
// ============================================================================

#[test]
fn test_claude_wrapping_exactly_at_boundary() {
    with_default_timeout(|| {
        // Use a narrow terminal (40 cols) to control wrapping precisely
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(40, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, terminal.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        // Prefix is "[ccs/glm] " = 10 chars
        // Content needs to be exactly 30 chars to fill to column 40
        let content = "A".repeat(30); // Exactly fills width (10 + 30 = 40)
        let stream = format!(
            r#"
{{"type":"stream_event","event":{{"type":"message_start","message":{{"id":"msg1","content":[]}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_start","index":0,"content_block":{{"type":"text","text":""}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{content}"}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_stop","index":0}}}}
{{"type":"stream_event","event":{{"type":"message_stop"}}}}
"#
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let term = terminal.borrow();
        let visible_lines = term.count_visible_lines();

        // Content at exact boundary should occupy exactly 1 line (no wrap)
        assert_eq!(
            visible_lines, 1,
            "Content at exact boundary should not wrap. Found {visible_lines} lines"
        );

        // Enhanced assertions per screen model upgrade
        let visible = term.get_visible_output();
        assert_eq!(
            visible.matches(&content).count(),
            1,
            "Content should appear exactly once in visible output"
        );
        assert_eq!(
            term.count_visible_pattern("[ccs/glm]"),
            1,
            "Prefix should appear exactly once (no waterfall)"
        );
        let (row, _col) = term.cursor_position();
        assert!(
            row >= 1,
            "Cursor should have moved to new line after completion"
        );
    });
}

#[test]
fn test_claude_wrapping_one_char_over() {
    with_default_timeout(|| {
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(40, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, terminal.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        // Prefix is 10 chars, content is 31 chars = 41 total (wraps by 1 char)
        let content = "A".repeat(31);
        let stream = format!(
            r#"
{{"type":"stream_event","event":{{"type":"message_start","message":{{"id":"msg1","content":[]}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_start","index":0,"content_block":{{"type":"text","text":""}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{content}"}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_stop","index":0}}}}
{{"type":"stream_event","event":{{"type":"message_stop"}}}}
"#
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // When content wraps, it occupies multiple physical rows in the terminal.
        // This is expected and correct behavior. What matters is:
        // 1. Prefix appears exactly ONCE (no waterfall)
        // 2. Content is complete and correct (may be split across rows)
        let visible = term.get_visible_output();
        assert_eq!(
            term.count_visible_pattern("[ccs/glm]"),
            1,
            "Prefix should appear exactly once (append-only pattern prevents waterfall). \
             Content wrapping to multiple rows is expected."
        );
        // When content wraps, newlines are inserted between rows. Remove them to verify content.
        let visible_no_newlines = visible.replace('\n', "");
        assert!(
            visible_no_newlines.contains(&content),
            "Content should be complete (may be split across wrapped rows). Screen:\n{visible}"
        );
    });
}

#[test]
fn test_claude_wrapping_multi_line() {
    with_default_timeout(|| {
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(40, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, terminal.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        // Create content that spans 3+ terminal lines (40 cols)
        // Prefix = 10 chars, so we need 90+ chars of content for 3 lines
        let content = "This is a long message that will definitely wrap across multiple lines in the terminal. ".repeat(2);
        let stream = format!(
            r#"
{{"type":"stream_event","event":{{"type":"message_start","message":{{"id":"msg1","content":[]}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_start","index":0,"content_block":{{"type":"text","text":""}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{content}"}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_stop","index":0}}}}
{{"type":"stream_event","event":{{"type":"message_stop"}}}}
"#
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // Multi-line wrapping creates multiple physical terminal rows.
        // Verify append-only pattern works: prefix appears ONCE, content is complete
        let visible = term.get_visible_output();
        assert_eq!(
            term.count_visible_pattern("[ccs/glm]"),
            1,
            "Prefix should appear exactly once despite wrapping to multiple rows"
        );
        assert!(
            visible.contains("This is a long message"),
            "Content should be complete. Screen:\n{visible}"
        );
    });
}

#[test]
fn test_claude_wrapping_with_unicode() {
    with_default_timeout(|| {
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(40, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, terminal.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        // Mix emoji and regular text to test unicode handling
        let content = "Hello 👋 World 🌍 This is a test with emoji 🚀 and unicode 你好";
        let stream = format!(
            r#"
{{"type":"stream_event","event":{{"type":"message_start","message":{{"id":"msg1","content":[]}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_start","index":0,"content_block":{{"type":"text","text":""}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{content}"}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_stop","index":0}}}}
{{"type":"stream_event","event":{{"type":"message_stop"}}}}
"#
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // Unicode content may wrap differently due to character width, but
        // the prefix should still appear exactly once (no waterfall)
        let visible = term.get_visible_output();
        assert_eq!(
            term.count_visible_pattern("[ccs/glm]"),
            1,
            "Prefix should appear exactly once even with unicode characters"
        );
        assert!(
            visible.contains("Hello") && visible.contains("World"),
            "Unicode content should be complete. Screen:\n{visible}"
        );
    });
}

#[test]
fn test_claude_wrapping_multiple_deltas() {
    with_default_timeout(|| {
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(40, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, terminal.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        // Each delta adds more content, eventually exceeding terminal width
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" This is getting longer"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" and will soon wrap"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" across the terminal width"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // Multiple deltas with wrapping: content may span multiple physical rows.
        // Verify append-only pattern: prefix appears ONCE, content is complete.
        let visible = term.get_visible_output();
        assert_eq!(
            term.count_visible_pattern("[ccs/glm]"),
            1,
            "Prefix should appear exactly once despite multiple deltas and wrapping. \
             This indicates the append-only pattern is working correctly."
        );
        assert!(
            visible.contains("Hello World"),
            "Content should be complete. Screen:\n{visible}"
        );
        let (row, _col) = term.cursor_position();
        assert!(row >= 1, "Cursor should be on new line after completion");
    });
}

#[test]
fn test_claude_wrapping_very_narrow_terminal() {
    with_default_timeout(|| {
        // Extreme case: 20-column terminal
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(20, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, terminal.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        let content = "This message is much longer than the narrow terminal width";
        let stream = format!(
            r#"
{{"type":"stream_event","event":{{"type":"message_start","message":{{"id":"msg1","content":[]}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_start","index":0,"content_block":{{"type":"text","text":""}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{content}"}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_stop","index":0}}}}
{{"type":"stream_event","event":{{"type":"message_stop"}}}}
"#
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // Narrow terminal causes extreme wrapping (many physical rows).
        // Verify append-only pattern: prefix appears ONCE despite wrapping
        let visible = term.get_visible_output();
        assert_eq!(
            term.count_visible_pattern("[ccs/glm]"),
            1,
            "Prefix should appear exactly once even in very narrow terminal with extreme wrapping"
        );
        // Content wraps aggressively in narrow terminal, remove newlines to verify
        let visible_no_newlines = visible.replace('\n', "");
        assert!(
            visible_no_newlines.contains(content),
            "Content should be complete (split across many wrapped rows). Screen:\n{visible}"
        );
    });
}

// ============================================================================
// Codex (CodexParser) Wrapping Tests
// ============================================================================

#[test]
fn test_codex_wrapping_exactly_at_boundary() {
    with_default_timeout(|| {
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(40, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = CodexParser::with_printer_for_test(colors, verbosity, terminal.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::Full);

        // Prefix is "[ccs/codex] Thinking: " = 21 chars
        // Content needs to be 19 chars to fill to column 40
        let content = "A".repeat(19);
        let stream = format!(
            r#"
{{"type":"turn.started"}}
{{"type":"item.started","item":{{"type":"reasoning","text":"{content}"}}}}
{{"type":"item.completed","item":{{"type":"reasoning","text":"{content}"}}}}
{{"type":"turn.completed"}}
"#
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // Verify reasoning prefix appears exactly once (ignore turn lifecycle events)
        assert_eq!(
            term.count_visible_pattern("Thinking:"),
            1,
            "Thinking prefix should appear exactly once"
        );
    });
}

#[test]
fn test_codex_wrapping_multi_line() {
    with_default_timeout(|| {
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(40, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = CodexParser::with_printer_for_test(colors, verbosity, terminal.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::Full);

        let content = "This is extensive reasoning text that will definitely exceed the terminal width and cause wrapping across multiple lines in the narrow terminal window";
        let stream = format!(
            r#"
{{"type":"turn.started"}}
{{"type":"item.started","item":{{"type":"reasoning","text":"{content}"}}}}
{{"type":"item.completed","item":{{"type":"reasoning","text":"{content}"}}}}
{{"type":"turn.completed"}}
"#
        );

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // Codex multi-line content: verify reasoning prefix appears once (ignore turn lifecycle events)
        assert_eq!(
            term.count_visible_pattern("Thinking:"),
            1,
            "Thinking prefix should appear exactly once despite wrapping"
        );
    });
}

#[test]
fn test_codex_wrapping_multiple_deltas() {
    with_default_timeout(|| {
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(40, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = CodexParser::with_printer_for_test(colors, verbosity, terminal.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::Full);

        let stream = r#"
{"type":"turn.started"}
{"type":"item.started","item":{"type":"reasoning","text":"Starting reasoning"}}
{"type":"item.started","item":{"type":"reasoning","text":"Starting reasoning with more content"}}
{"type":"item.started","item":{"type":"reasoning","text":"Starting reasoning with more content that will eventually wrap"}}
{"type":"item.started","item":{"type":"reasoning","text":"Starting reasoning with more content that will eventually wrap across the terminal width"}}
{"type":"item.completed","item":{"type":"reasoning","text":"Starting reasoning with more content that will eventually wrap across the terminal width"}}
{"type":"turn.completed"}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let term = terminal.borrow();

        // Multiple Codex reasoning deltas: verify reasoning prefix appears once (ignore turn lifecycle events)
        assert_eq!(
            term.count_visible_pattern("Thinking:"),
            1,
            "Thinking prefix should appear exactly once across multiple deltas"
        );
    });
}
