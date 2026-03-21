//! Test that streaming with wrapping does NOT cause multi-line waterfall.
//!
//! Bug: Current Full mode uses `\n\x1b[1A` pattern which fails when content wraps:
//! - Content exceeds terminal width
//! - Terminal wraps to multiple rows
//! - `\x1b[2K` only clears current row, not wrapped rows above
//! - Result: multiple visible lines instead of in-place update
//!
//! Expected: ChatGPT-style streaming with append-only pattern produces only 1 visible line.
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
use std::io::{BufReader, Write};
use std::rc::Rc;

#[test]
fn test_wrapping_no_waterfall_claude() {
    with_default_timeout(|| {
        // Use a narrow terminal (40 cols) to force wrapping
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(40, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = ClaudeParser::with_printer(colors, verbosity, terminal.clone())
            .with_display_name("ccs/glm")
            .with_terminal_mode(TerminalMode::Full);

        // Stream text that will definitely wrap (80+ chars, terminal is 40 cols)
        let stream = r#"
{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg1","content":[]}}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"This is a very long message that will definitely wrap across multiple lines in a narrow terminal"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" and even more text to ensure wrapping"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" final delta"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_stop"}}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream(reader, &workspace).unwrap();

        let term = terminal.borrow();
        let screen_content = term.get_visible_output();

        // In ChatGPT-style append-only streaming, we expect:
        // 1. The prefix appears exactly ONCE (not repeated for each delta)
        // 2. The full content is present
        // 3. Content may wrap to multiple rows (this is expected with narrow terminal)

        let prefix_count = screen_content.matches("[ccs/glm]").count();
        assert_eq!(
            prefix_count, 1,
            "Expected prefix to appear exactly once, found {prefix_count} times. \
             This indicates waterfall bug where each delta repeats the prefix.\n\
             Screen content:\n{screen_content}"
        );

        // Verify the full content is present
        assert!(
            screen_content.contains("This is a very long message"),
            "Content should be visible. Screen:\n{screen_content}"
        );
        assert!(
            screen_content.contains("final delta"),
            "Final delta content should be visible. Screen:\n{screen_content}"
        );

        // Verify the content is actually present (not lost)
        let screen_content = term.get_visible_output();
        assert!(
            screen_content.contains("This is a very long message"),
            "Content should be visible. Screen:\n{screen_content}"
        );
    });
}

#[test]
fn test_wrapping_no_waterfall_codex() {
    with_default_timeout(|| {
        // Similar test for Codex parser
        let terminal = Rc::new(RefCell::new(VirtualTerminal::new_with_geometry(40, 24)));
        let colors = Colors::new();
        let verbosity = Verbosity::Normal;

        let mut parser = CodexParser::with_printer_for_test(colors, verbosity, terminal.clone())
            .with_display_name_for_test("ccs/codex")
            .with_terminal_mode(TerminalMode::Full);

        // Stream reasoning text with multiple deltas that will wrap
        // Uses Codex item.started format with incremental text updates
        let stream = r#"
{"type":"turn.started"}
{"type":"item.started","item":{"type":"reasoning","text":"This is extensive reasoning text that will"}}
{"type":"item.started","item":{"type":"reasoning","text":"This is extensive reasoning text that will definitely exceed the terminal width and cause wrapping across multiple lines in the narrow terminal window"}}
{"type":"item.started","item":{"type":"reasoning","text":"This is extensive reasoning text that will definitely exceed the terminal width and cause wrapping across multiple lines in the narrow terminal window even more reasoning to ensure wrapping"}}
{"type":"item.completed","item":{"type":"reasoning","text":"This is extensive reasoning text that will definitely exceed the terminal width and cause wrapping across multiple lines in the narrow terminal window even more reasoning to ensure wrapping"}}
{"type":"turn.completed"}
"#;

        let reader = BufReader::new(stream.as_bytes());
        let workspace = MemoryWorkspace::new_test();
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let term = terminal.borrow();
        let visible_output = term.get_visible_output();

        // In ChatGPT-style append-only streaming, we expect:
        // 1. The "Thinking:" prefix appears exactly ONCE (not repeated for each delta)
        // 2. The full content is present
        // 3. Content may wrap to multiple rows (this is expected with narrow terminal)

        let thinking_prefix_count = visible_output.matches("Thinking:").count();
        assert_eq!(
            thinking_prefix_count, 1,
            "Expected 'Thinking:' prefix to appear exactly once, found {thinking_prefix_count} times. \
             This indicates waterfall bug where each delta repeats the prefix.\n\
             Screen content:\n{visible_output}"
        );

        // Verify the full content is present (check for key phrases that won't be split by wrapping)
        assert!(
            visible_output.contains("This is extensive"),
            "Content should be visible. Screen:\n{visible_output}"
        );
        assert!(
            visible_output.contains("reasoning text"),
            "Content should be visible. Screen:\n{visible_output}"
        );
        assert!(
            visible_output.contains("even more"),
            "Final delta content should be visible. Screen:\n{visible_output}"
        );
    });
}

#[test]
fn test_cursor_up_pattern_fails_with_wrapping() {
    with_default_timeout(|| {
        // This test demonstrates the ROOT CAUSE: cursor-up pattern cannot erase wrapped content.
        // When content wraps to N rows, "\x1b[1A\x1b[2K" (cursor up 1, clear line) only clears
        // the last row, leaving N-1 rows visible (orphaned wrapped content).

        // Create narrow terminal (40 cols) to force wrapping
        let mut term = VirtualTerminal::new_with_geometry(40, 24);

        // Write content that wraps to 3 rows
        // Prefix "[ccs/glm] " = 10 chars
        // Content = 100 A's will wrap: row1=30 chars, row2=40 chars, row3=30 chars + newline
        let prefix = "[ccs/glm] ";
        let content = "A".repeat(100);
        write!(term, "{prefix}{content}\n\x1b[1A").unwrap();

        // Verify content wrapped to multiple rows
        let rows_before = term.count_physical_rows();
        assert!(
            rows_before > 1,
            "Content should occupy multiple physical rows before clear attempt"
        );

        // Try to clear with cursor-up-1 + clear-line (legacy pattern)
        write!(term, "\x1b[1A\x1b[2K").unwrap();

        // Assert: orphaned content still visible
        let rows_after = term.count_physical_rows();
        assert!(
            rows_after > 0,
            "Cursor-up pattern leaves orphaned wrapped rows (cleared {} rows but {} remain)",
            rows_before - rows_after,
            rows_after
        );

        // Assert: VirtualTerminal can detect this failure mode.
        // NOTE: would_cursor_up_leave_orphans approximates display width; keep this test ASCII-only.
        assert!(
            term.would_cursor_up_leave_orphans(&format!("{prefix}{content}")),
            "VirtualTerminal should detect cursor-up would leave orphans"
        );
    });
}
