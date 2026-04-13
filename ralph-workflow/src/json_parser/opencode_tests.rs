//! Tests for `OpenCode` JSON parser.

use super::*;
use crate::config::Verbosity;
use crate::logger::Colors;
use crate::workspace::MemoryWorkspace;
use std::io::Cursor;

#[test]
fn test_parse_opencode_tool_output_object_payload() {
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Verbose);
    let json = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","type":"tool","tool":"read","state":{"status":"completed","input":{"filePath":"/test.rs"},"output":{"ok":true,"bytes":123}}}}"#;
    let output = parser.parse_event(json).unwrap();
    assert!(output.contains("Output"));
    assert!(output.contains("ok"));
}

#[test]
fn test_opencode_streaming_with_tool_use_events() {
    use crate::json_parser::printer::{SharedPrinter, TestPrinter};
    use std::cell::RefCell;
    use std::rc::Rc;

    // Create a TestPrinter to capture output
    let test_printer: SharedPrinter = Rc::new(RefCell::new(TestPrinter::new()));
    let mut parser =
        OpenCodeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, test_printer);

    // Simulate streaming tool_use events
    let input = r#"{"type":"tool_use","timestamp":1768191346712,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","type":"tool","tool":"read","state":{"status":"started","input":{"filePath":"/test.rs"}}}}
{"type":"tool_use","timestamp":1768191346713,"sessionID":"ses_44f9562d4ffe","part":{"id":"prt_bb06ac80c001","type":"tool","tool":"read","state":{"status":"completed","input":{"filePath":"/test.rs"}}}}"#;

    let reader = Cursor::new(input);

    // Verify the parse succeeds
    let workspace = MemoryWorkspace::new_test();
    let result = parser.parse_stream(reader, &workspace);
    assert!(
        result.is_ok(),
        "parse_stream should succeed for OpenCode events"
    );
}

/// Test that `with_terminal_mode` method works correctly
#[test]
#[cfg(feature = "test-utils")]
fn test_with_terminal_mode() {
    use crate::json_parser::terminal::TerminalMode;

    // Test that TerminalMode::None suppresses per-delta output (flushed at completion)
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::None);

    // In non-TTY modes, text deltas are suppressed to prevent repeated prefixed lines
    let json = r#"{"type":"text","timestamp":1768191347231,"sessionID":"test","part":{"id":"prt_001","type":"text","text":"Hello"}}"#;
    let output = parser.parse_event(json);
    assert!(
        output.is_none(),
        "text delta should be suppressed in TerminalMode::None"
    );

    // Test that TerminalMode::Full produces streaming output
    let parser_full = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::Full);
    let output_full = parser_full.parse_event(json);
    assert!(
        output_full.is_some(),
        "text delta should produce output in TerminalMode::Full"
    );
}


// ──────────────────────────────────────────────────────────────────────────────
// Regression tests for UX issues (Issues 1–7)
// ──────────────────────────────────────────────────────────────────────────────

/// Issue 1 — Truncation summary appears exactly once, not once per omitted line.
#[test]
fn test_truncation_summary_appears_exactly_once() {
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    // Build output with 60 short lines — far exceeds MAX_OUTPUT_LINES (5)
    let lines: Vec<String> = (1..=60).map(|i| format!("/path/to/file{i}.rs")).collect();
    let output_str = lines.join("\n");
    let json = format!(
        r#"{{"type":"tool_use","timestamp":1,"sessionID":"ses","part":{{"type":"tool","tool":"grep","state":{{"status":"completed","input":{{"pattern":"TODO"}},"output":{output_str:?}}}}}}}"#
    );
    let result = parser.parse_event(&json);
    assert!(result.is_some(), "expected Some output for tool_use event");
    let out = result.unwrap();
    let more_count = out.matches("more lines").count();
    assert_eq!(
        more_count, 1,
        "expected exactly one 'more lines' summary, got {more_count}:\n{out}"
    );
    // The count in the summary should equal total lines minus shown lines (5)
    assert!(
        out.contains("55 more lines"),
        "expected '55 more lines' in output, got:\n{out}"
    );
}

/// Issue 1 variant — char-budget path: one very long first line pushes budget, still one summary.
#[test]
fn test_truncation_summary_once_when_char_budget_exceeded() {
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    // First line is very long to exhaust the 500-char budget immediately.
    // Subsequent lines should all be summarised in a single "more lines" line.
    let long_first = "x".repeat(600);
    let mut lines = vec![long_first];
    for i in 1..=10 {
        lines.push(format!("/file{i}.rs"));
    }
    let output_str = lines.join("\n");
    let json = format!(
        r#"{{"type":"tool_use","timestamp":1,"sessionID":"ses","part":{{"type":"tool","tool":"grep","state":{{"status":"completed","input":{{"pattern":"x"}},"output":{output_str:?}}}}}}}"#
    );
    let result = parser.parse_event(&json);
    assert!(result.is_some(), "expected Some output");
    let out = result.unwrap();
    let more_count = out.matches("more lines").count();
    assert_eq!(
        more_count, 1,
        "expected exactly one 'more lines' summary (char-budget path), got {more_count}:\n{out}"
    );
}

/// Issue 2 — No blank └─ lines for tools with empty title or empty input.
#[test]
fn test_no_blank_continuation_lines() {
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    // Tool with empty title and empty command input
    let json = r#"{"type":"tool_use","timestamp":1,"sessionID":"ses","part":{"id":"p1","type":"tool","tool":"bash","state":{"status":"completed","title":"","input":{"command":""},"output":"done"}}}"#;
    let result = parser.parse_event(json);
    assert!(result.is_some(), "expected Some output");
    let out = result.unwrap();
    // No line should consist solely of the └─ continuation with nothing after it
    for line in out.lines() {
        let stripped = line.trim();
        assert!(
            stripped != "\u{2514}\u{2500}",
            "blank continuation line found: {:?}",
            line
        );
        assert!(
            !stripped.ends_with("\u{2514}\u{2500} "),
            "trailing-space continuation line found: {:?}",
            line
        );
    }
}

/// Issues 3 & 4 — Step-finished uses "reasoning:" label, shows cost, uses · delimiter.
#[test]
fn test_step_finished_format_labels_and_cost() {
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    // Prime the session with a step_start so the message ID is known
    let start = r#"{"type":"step_start","timestamp":1,"sessionID":"s","part":{"id":"p0","sessionID":"s","messageID":"m1","type":"step-start","snapshot":"abcdef1234567890"}}"#;
    let _ = parser.parse_event(start);
    let finish = r#"{"type":"step_finish","timestamp":2,"sessionID":"s","part":{"id":"p1","sessionID":"s","messageID":"m1","type":"step-finish","reason":"tool-calls","cost":0.0042,"tokens":{"input":532,"output":85,"reasoning":24,"cache":{"read":151680,"write":0}}}}"#;
    let result = parser.parse_event(finish);
    assert!(result.is_some(), "expected Some output for step_finish");
    let out = result.unwrap();
    assert!(
        out.contains("reasoning:"),
        "should use 'reasoning:' label, got:\n{out}"
    );
    assert!(
        !out.contains("reason:24"),
        "should not use 'reason:' for token count, got:\n{out}"
    );
    assert!(out.contains('$'), "cost should be present, got:\n{out}");
    assert!(
        out.contains('\u{00b7}'),
        "middle-dot delimiter should be present, got:\n{out}"
    );
}

/// Issue 5 — Step-started snapshot display: short/placeholder strings show no parenthetical.
#[test]
fn test_step_started_snapshot_display() {
    // Empty snapshot: no parenthetical at all
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json_empty = r#"{"type":"step_start","timestamp":1,"sessionID":"s","part":{"id":"p0","sessionID":"s","messageID":"m1","type":"step-start","snapshot":""}}"#;
    let out = parser.parse_event(json_empty).unwrap_or_default();
    assert!(
        !out.contains('('),
        "empty snapshot should show no parenthetical, got:\n{out}"
    );

    // Short placeholder "..." (3 chars < 8): no parenthetical
    let parser2 = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json_short = r#"{"type":"step_start","timestamp":1,"sessionID":"s","part":{"id":"p0","sessionID":"s","messageID":"m2","type":"step-start","snapshot":"..."}}"#;
    let out2 = parser2.parse_event(json_short).unwrap_or_default();
    assert!(
        !out2.contains('('),
        "3-char placeholder snapshot should show no parenthetical, got:\n{out2}"
    );

    // Real 40-char hash: shows truncated form with first 8 chars
    let parser3 = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json_real = r#"{"type":"step_start","timestamp":1,"sessionID":"s","part":{"id":"p0","sessionID":"s","messageID":"m3","type":"step-start","snapshot":"5d36aa035d4df6edb73a68058733063258114ed5"}}"#;
    let out3 = parser3.parse_event(json_real).unwrap_or_default();
    assert!(
        out3.contains("(5d36aa03"),
        "real 40-char hash should show first 8 chars, got:\n{out3}"
    );
}

/// Issue 6 — Blank lines from tool output are stripped/collapsed before display.
#[test]
fn test_tool_output_blank_line_normalization() {
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    // Output with leading blank, interior double-blank, trailing blank
    let raw_output = "\n\nfile1.rs\n\n\nfile2.rs\n\n";
    let json = format!(
        r#"{{"type":"tool_use","timestamp":1,"sessionID":"ses","part":{{"type":"tool","tool":"grep","state":{{"status":"completed","input":{{"pattern":"x"}},"output":{raw_output:?}}}}}}}"#
    );
    let result = parser.parse_event(&json);
    assert!(result.is_some(), "expected Some output");
    let out = result.unwrap();

    // The output lines should not start or end with a blank line in the content section
    let content_lines: Vec<&str> = out.lines().collect();
    // There should be no two consecutive blank lines in the formatted output
    let mut prev_blank = false;
    for line in &content_lines {
        let is_blank = line.trim().is_empty();
        assert!(
            !(is_blank && prev_blank),
            "consecutive blank lines found in formatted output:\n{out}"
        );
        prev_blank = is_blank;
    }
}

/// Issue 7 — Running and completed tools show elapsed duration.
#[test]
fn test_tool_running_shows_duration() {
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    // timestamp (event time) is 12s after start → duration "12s"
    let json = r#"{"type":"tool_use","timestamp":1768191358712,"sessionID":"ses","part":{"type":"tool","tool":"bash","state":{"status":"running","input":{"command":"npm test"},"time":{"start":1768191346712}}}}"#;
    let result = parser.parse_event(json);
    assert!(result.is_some(), "expected Some output for running tool");
    let out = result.unwrap();
    assert!(
        out.contains("12s"),
        "running tool should show 12s duration, got:\n{out}"
    );
}

#[test]
fn test_tool_completed_shows_duration() {
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    // end - start = 14s
    let json = r#"{"type":"tool_use","timestamp":1768191360712,"sessionID":"ses","part":{"type":"tool","tool":"bash","state":{"status":"completed","input":{"command":"cargo test"},"output":"ok","time":{"start":1768191346712,"end":1768191360712}}}}"#;
    let result = parser.parse_event(json);
    assert!(result.is_some(), "expected Some output for completed tool");
    let out = result.unwrap();
    assert!(
        out.contains("14s"),
        "completed tool should show 14s duration, got:\n{out}"
    );
}

#[test]
fn test_tool_pending_shows_no_duration() {
    let parser = OpenCodeParser::new(Colors { enabled: false }, Verbosity::Normal);
    // Pending tools have no timing data — duration must not appear
    let json = r#"{"type":"tool_use","timestamp":1,"sessionID":"ses","part":{"type":"tool","tool":"bash","state":{"status":"pending","input":{"command":"ls"}}}}"#;
    let result = parser.parse_event(json);
    assert!(result.is_some(), "expected Some output for pending tool");
    let out = result.unwrap();
    assert!(
        !out.contains("ms") && !out.contains("0s"),
        "pending tool should show no duration, got:\n{out}"
    );
}
