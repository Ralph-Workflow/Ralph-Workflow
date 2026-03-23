//! Tests for Codex JSON parser.

use super::*;
use crate::config::Verbosity;
use crate::logger::Colors;

#[test]
fn test_parse_codex_thread_started() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"thread.started","thread_id":"xyz789"}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    assert!(output.unwrap().contains("Thread started"));
}

#[test]
fn test_parse_codex_turn_completed() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":50}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    assert!(output.unwrap().contains("Turn completed"));
}

/// Companion test: Normal verbosity uses "Thinking:" label (not "Thought:").
///
/// Guards against regressions where a refactor swaps the verbose/non-verbose branches
/// in `handle_reasoning_completed`. Verbose uses "Thought:", Normal uses "Thinking:".
#[test]
fn test_codex_reasoning_completed_normal_verbosity_uses_thinking_label() {
    use crate::json_parser::terminal::TerminalMode;

    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::None);
    let json = r#"{"type":"item.completed","item":{"type":"reasoning","id":"item_1","text":"some reasoning text"}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(
        out.contains("Thinking"),
        "Expected 'Thinking' in normal-verbosity output: {out}"
    );
    assert!(
        !out.contains("Thought:"),
        "Should not use 'Thought:' in normal-verbosity: {out}"
    );
}

#[test]
fn test_codex_file_operations_shown() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Verbose);
    let json = r#"{"type":"item.started","item":{"type":"file_read","path":"/src/main.rs"}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(out.contains("file_read"));
    assert!(out.contains("/src/main.rs"));
}

#[test]
fn test_codex_reasoning_event() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Verbose);
    let json = r#"{"type":"item.started","item":{"type":"reasoning","id":"item_1"}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    assert!(output.unwrap().contains("Reasoning"));
}

#[test]
fn test_codex_reasoning_completed_shows_text() {
    use crate::json_parser::terminal::TerminalMode;

    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Verbose)
        .with_terminal_mode(TerminalMode::None);
    let json = r#"{"type":"item.completed","item":{"type":"reasoning","id":"item_1","text":"I should analyze this file first"}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(out.contains("Thought"));
    assert!(out.contains("analyze"));
}

#[test]
fn test_codex_mcp_tool_call() {
    use crate::json_parser::terminal::TerminalMode;

    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::Full);
    let json = r#"{"type":"item.started","item":{"type":"mcp_tool_call","tool":"search_files","arguments":{"query":"main"}}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(out.contains("MCP Tool"));
    assert!(out.contains("search_files"));

    // Tool input rendering is suppressed in non-TTY output modes; only Full TTY mode streams
    // tool input lines. This test just verifies we don't crash and that the tool is identified.
}

#[test]
fn test_codex_mcp_tool_call_none_mode_is_plain_text() {
    use crate::json_parser::terminal::TerminalMode;

    let parser = CodexParser::new(Colors { enabled: true }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::None)
        .with_display_name("ccs/codex");

    let json = r#"{"type":"item.started","item":{"type":"mcp_tool_call","tool":"search_files","arguments":{"query":"main"}}}"#;
    let output = parser.parse_event(json).unwrap_or_default();

    assert!(output.contains("[ccs/codex] MCP Tool: search_files"));
    assert!(output.contains("[ccs/codex]   └─"));

    // TerminalMode::None must not contain ANSI escapes even if colors are enabled.
    assert!(
        !output.contains("\x1b["),
        "Unexpected ANSI escapes: {output}"
    );
}

#[test]
fn test_codex_web_search() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json =
        r#"{"type":"item.started","item":{"type":"web_search","query":"rust async tutorial"}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(out.contains("Search"));
    assert!(out.contains("rust async tutorial"));
}

#[test]
fn test_codex_plan_update() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Verbose);
    let json = r#"{"type":"item.started","item":{"type":"plan_update","id":"item_1"}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    assert!(output.unwrap().contains("Updating plan"));
}

#[test]
fn test_codex_turn_completed_with_cached_tokens() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"turn.completed","usage":{"input_tokens":24763,"cached_input_tokens":24448,"output_tokens":122}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(out.contains("Turn completed"));
    assert!(out.contains("in:24763"));
    assert!(out.contains("out:122"));
}

#[test]
fn test_codex_item_with_status() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"ls","status":"in_progress"}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(out.contains("Exec"));
    assert!(out.contains("ls"));
}

#[test]
fn test_codex_file_write_completed() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"item.completed","item":{"type":"file_write","path":"/src/main.rs"}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(out.contains("File"));
    assert!(out.contains("/src/main.rs"));
}

#[test]
fn test_codex_mcp_completed() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"item.completed","item":{"type":"mcp_tool_call","tool":"read_file"}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(out.contains("MCP"));
    assert!(out.contains("read_file"));
    assert!(out.contains("done"));
}

#[test]
fn test_codex_web_search_completed() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"item.completed","item":{"type":"web_search"}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    assert!(output.unwrap().contains("Search completed"));
}

#[test]
fn test_codex_parser_non_json_passthrough() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let output = parser.parse_event("Error: something went wrong");
    assert!(output.is_some());
    assert!(output.unwrap().contains("Error: something went wrong"));
}

/// Test that `with_terminal_mode` method works correctly
#[test]
fn test_with_terminal_mode() {
    use crate::json_parser::terminal::TerminalMode;

    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::None);

    // Verify the parser was created successfully
    let json = r#"{"type":"thread.started","thread_id":"test123"}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
}

/// Test that Codex parser handles Result events correctly.
///
/// Result events are synthetic events written by the parser (not from Codex CLI itself)
/// to enable content extraction. This test verifies that the Result event variant
/// is properly handled.
#[test]
fn test_codex_result_event() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json =
        r#"{"type":"result","result":"This is the accumulated content from agent_message items"}"#;
    let output = parser.parse_event(json);
    // Result events are control events that don't produce output in normal mode
    assert!(output.is_none() || output.unwrap().is_empty());
}

/// Test that Codex parser handles Result events in debug mode.
///
/// In debug mode, result events should be displayed for troubleshooting.
#[test]
fn test_codex_result_event_debug_mode() {
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Debug);
    let json = r#"{"type":"result","result":"Debug result content"}"#;
    let output = parser.parse_event(json);
    // In debug mode, result events should be shown
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(out.contains("Debug result content"));
}

/// Test that Codex parser correctly identifies Result events as control events.
///
/// Control events are state management events that don't produce user output.
#[test]
fn test_codex_result_event_is_control_event() {
    // Result events should be classified as control events
    // This means they won't produce output in normal mode
    let parser = CodexParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"result","result":"test content"}"#;
    let output = parser.parse_event(json);
    assert!(output.is_none() || output.unwrap().is_empty());
}

#[test]
fn test_codex_reasoning_no_spam_regression() {
    use crate::json_parser::printer::{SharedPrinter, TestPrinter};
    use crate::json_parser::terminal::TerminalMode;
    use crate::workspace::MemoryWorkspace;
    use std::cell::RefCell;
    use std::io::Cursor;
    use std::rc::Rc;

    // This test uses the actual captured log from tests/integration_tests/artifacts/example_log.log
    // which demonstrates the reasoning spam bug.
    // Pre-fix: Multiple "[ccs/codex] Thinking:" lines are printed
    // Post-fix: At most one thinking line in non-TTY mode (Basic/None)

    let workspace = MemoryWorkspace::new_test();
    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();
    let mut parser =
        CodexParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
            .with_terminal_mode(TerminalMode::None) // Non-TTY mode (logs)
            .with_display_name("ccs/codex");

    // Simulated JSON events from the log showing repeated reasoning deltas
    // (extracted pattern from the real log)
    let input = r#"{"type":"item.started","item":{"type":"reasoning","text":"**Reading diff in chunks** The diff is too large to read all at once"}}
{"type":"item.started","item":{"type":"reasoning","text":", so I need to break it down"}}
{"type":"item.started","item":{"type":"reasoning","text":" into smaller pieces using an offset and limit."}}
{"type":"item.started","item":{"type":"reasoning","text":" The instructions"}}
{"type":"item.completed","item":{"type":"reasoning"}}
"#;

    let reader = Cursor::new(input);
    parser.parse_stream(reader, &workspace).unwrap();

    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Count occurrences of "[ccs/codex] Thinking:" prefix
    let thinking_line_count = output
        .lines()
        .filter(|line| line.contains("[ccs/codex]") && line.contains("Thinking:"))
        .count();

    // In non-TTY mode (Basic/None), we should emit AT MOST one final thinking line
    // at the completion boundary, not one line per delta.
    assert!(
        thinking_line_count <= 1,
        "Expected at most 1 thinking line in non-TTY mode, got {thinking_line_count}. Output:\n{output}"
    );

    // Verify the final thinking line contains accumulated content
    if thinking_line_count == 1 {
        let thinking_line = output
            .lines()
            .find(|line| line.contains("Thinking:"))
            .unwrap();
        // Should contain some of the accumulated reasoning content
        assert!(
            thinking_line.contains("diff") || thinking_line.contains("Reading"),
            "Thinking line should contain accumulated content: {thinking_line}"
        );
    }
}

#[test]
fn test_codex_reasoning_full_mode_in_place_updates() {
    use crate::json_parser::printer::{SharedPrinter, TestPrinter};
    use crate::json_parser::terminal::TerminalMode;
    use crate::workspace::MemoryWorkspace;
    use std::cell::RefCell;
    use std::io::Cursor;
    use std::rc::Rc;

    let workspace = MemoryWorkspace::new_test();
    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();
    let mut parser =
        CodexParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
            .with_terminal_mode(TerminalMode::Full) // TTY with full capability
            .with_display_name("ccs/codex");

    let input = r#"{"type":"item.started","item":{"type":"reasoning","text":"First chunk"}}
{"type":"item.started","item":{"type":"reasoning","text":" second chunk"}}
{"type":"item.started","item":{"type":"reasoning","text":" third chunk"}}
{"type":"item.completed","item":{"type":"reasoning"}}
"#;

    let reader = Cursor::new(input);
    parser.parse_stream(reader, &workspace).unwrap();

    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // In Full mode, append-only pattern emits suffixes only (NO carriage return, NO cursor movement)
    // Subsequent deltas: just the new suffix text
    assert!(
        !output.contains('\r'),
        "Append-only pattern should NOT use carriage return. Output:\n{output}"
    );

    // NO cursor positioning sequences (append-only pattern)
    assert!(
        !output.contains("\x1b[1A"),
        "Should not contain cursor up in append-only pattern. Output:\n{output}"
    );

    assert!(
        !output.contains("\x1b[2K"),
        "Should not contain line clear in append-only pattern. Output:\n{output}"
    );

    assert!(
        !output.contains("\x1b[1B"),
        "Should not contain cursor down in append-only pattern. Output:\n{output}"
    );

    // Final completion: just newline
    assert!(
        output.ends_with('\n'),
        "Expected newline at completion. Output:\n{output}"
    );

    // Verify accumulated content is present
    assert!(
        output.contains("First chunk second chunk third chunk"),
        "Expected accumulated reasoning content. Output:\n{output}"
    );
}
