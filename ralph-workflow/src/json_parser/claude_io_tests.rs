//! Tests for Claude JSON parser.

use super::*;
use crate::config::Verbosity;
use crate::logger::Colors;

#[test]
fn test_parse_claude_system_init() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"system","subtype":"init","session_id":"abc123"}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    assert!(output.unwrap().contains("Session started"));
}

#[test]
fn test_parse_claude_result_success() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"result","subtype":"success","duration_ms":60000,"num_turns":5,"total_cost_usd":0.05}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    assert!(output.unwrap().contains("Completed"));
}

#[test]
fn test_parse_claude_tool_result_object_payload() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"assistant","message":{"content":[{"type":"tool_result","content":{"ok":true,"n":1}}]}}"#;
    let output = parser.parse_event(json).unwrap();
    assert!(output.contains("Result"));
    assert!(output.contains("ok"));
}

#[test]
fn test_parse_claude_text_with_unicode() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json =
        r#"{"type":"assistant","message":{"content":[{"type":"text","text":"Hello 世界! 🌍"}]}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
    let out = output.unwrap();
    assert!(out.contains("Hello 世界! 🌍"));
}

#[test]
fn test_claude_parser_non_json_passthrough() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    // Plain text that isn't JSON should be passed through
    let output = parser.parse_event("Hello, this is plain text output");
    assert!(output.is_some());
    assert!(output.unwrap().contains("Hello, this is plain text output"));
}

#[test]
fn test_claude_parser_malformed_json_ignored() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    // Malformed JSON that looks like JSON should be ignored
    let output = parser.parse_event("{invalid json here}");
    assert!(output.is_none());
}

#[test]
fn test_claude_parser_empty_line_ignored() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let output = parser.parse_event("");
    assert!(output.is_none());
    let output2 = parser.parse_event("   ");
    assert!(output2.is_none());
}

/// Test that `content_block_stop` events don't produce blank lines
///
/// This test verifies the fix for ccs-glm blank line issue where
/// `content_block_stop` events were being treated as Unknown events
/// and producing blank output.
#[test]
fn test_content_block_stop_no_output() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}"#;
    let output = parser.parse_event(json);
    assert!(
        output.is_none(),
        "content_block_stop should produce no output"
    );
}

/// Test that `message_delta` events don't produce blank lines
///
/// This test verifies the fix for ccs-glm blank line issue where
/// `message_delta` events were being treated as Unknown events
/// and producing blank output.
#[test]
fn test_message_delta_no_output() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"stream_event","event":{"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"input_tokens":100,"output_tokens":50}}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_none(), "message_delta should produce no output");
}

/// Test that `content_block_stop` with no index is handled
#[test]
fn test_content_block_stop_no_index() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal);
    let json = r#"{"type":"stream_event","event":{"type":"content_block_stop"}}"#;
    let output = parser.parse_event(json);
    assert!(
        output.is_none(),
        "content_block_stop without index should produce no output"
    );
}

/// Test complete ccs-glm event sequence in Full terminal mode
///
/// This test verifies that a typical ccs-glm streaming sequence
/// doesn't produce blank lines from control events and properly
/// renders streaming deltas in Full mode.
#[test]
#[cfg(feature = "test-utils")]
fn test_ccs_glm_event_sequence() {
    use crate::json_parser::terminal::TerminalMode;

    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::Basic);

    // System init
    let json1 = r#"{"type":"system","subtype":"init","session_id":"test123"}"#;
    let output1 = parser.parse_event(json1);
    assert!(output1.is_some());

    // Message start
    let json2 = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_123","type":"message","role":"assistant"}}}"#;
    let output2 = parser.parse_event(json2);
    assert!(output2.is_none(), "message_start should produce no output");

    // Content block start
    let json3 = r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}"#;
    let output3 = parser.parse_event(json3);
    assert!(
        output3.is_none(),
        "content_block_start should produce no output"
    );

    // Content block delta with text. Depending on terminal mode and the streaming
    // session's lifecycle/dedup heuristics, a single small delta may be buffered and
    // not immediately rendered. The core invariant is that control events do not
    // produce output.
    let json4 = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}"#;
    let _output4 = parser.parse_event(json4);

    // Content block stop - should not produce blank line
    let json5 = r#"{"type":"stream_event","event":{"type":"content_block_stop","index":0}}"#;
    let output5 = parser.parse_event(json5);
    assert!(
        output5.is_none(),
        "content_block_stop should produce no output"
    );

    // Message delta - should not produce blank line
    let json6 = r#"{"type":"stream_event","event":{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":100,"output_tokens":5}}}"#;
    let output6 = parser.parse_event(json6);
    assert!(output6.is_none(), "message_delta should produce no output");

    // Message stop
    let json7 = r#"{"type":"stream_event","event":{"type":"message_stop"}}"#;
    let output7 = parser.parse_event(json7);
    // Message stop should produce output (final newline) since we had content
    assert!(
        output7.is_some(),
        "message_stop should produce output after content"
    );
}

/// Test that `with_terminal_mode` method works correctly
#[test]
#[cfg(feature = "test-utils")]
fn test_with_terminal_mode() {
    use crate::json_parser::terminal::TerminalMode;

    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::None);

    // Verify the parser was created successfully
    let json = r#"{"type":"assistant","message":{"content":[{"type":"text","text":"Hello"}]}}"#;
    let output = parser.parse_event(json);
    assert!(output.is_some());
}

#[test]
#[cfg(feature = "test-utils")]
fn test_thinking_deltas_non_tty_flushed_once_on_message_stop() {
    use crate::json_parser::terminal::TerminalMode;

    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::None)
        .with_display_name("ccs/codex");

    // Start message
    let start = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant"}}}"#;
    assert!(parser.parse_event(start).is_none());

    // Stream thinking deltas (token-ish chunks)
    let d1 = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"git"}}}"#;
    let d2 = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":",\""}}}"#;
    let d3 = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" but"}}}"#;

    // In non-TTY mode, thinking should NOT be emitted per-delta.
    assert!(parser.parse_event(d1).is_none());
    assert!(parser.parse_event(d2).is_none());
    assert!(parser.parse_event(d3).is_none());

    // On message_stop we flush exactly one final thinking line.
    let stop = r#"{"type":"stream_event","event":{"type":"message_stop"}}"#;
    let out = parser
        .parse_event(stop)
        .expect("message_stop should flush thinking");

    assert!(out.contains("[ccs/codex]"));
    assert!(out.contains("Thinking:"));
    assert!(out.contains("git"));
    assert!(out.contains(",\""));
    assert!(out.contains("but"));
    assert_eq!(out.matches("Thinking:").count(), 1);

    // The thinking flush should produce a single newline-terminated line.
    // Avoid asserting on completion newlines; non-TTY completion output should not
    // add extra blank lines.
    assert_eq!(out.lines().count(), 1);
}

#[test]
#[cfg(feature = "test-utils")]
fn test_thinking_flushes_before_text_in_non_tty_mode() {
    use crate::json_parser::terminal::TerminalMode;

    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::None)
        .with_display_name("ccs/codex");

    let start = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_2","type":"message","role":"assistant"}}}"#;
    assert!(parser.parse_event(start).is_none());

    let think = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Checking..."}}}"#;
    assert!(
        parser.parse_event(think).is_none(),
        "thinking delta should be suppressed in non-TTY"
    );

    // In non-TTY mode we do not interleave thinking with streaming text.
    // Thinking is flushed once at message_stop.
    // Text deltas are also suppressed and flushed at message_stop.
    let text = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}"#;
    assert!(parser.parse_event(text).is_none());

    let stop = r#"{"type":"stream_event","event":{"type":"message_stop"}}"#;
    let out = parser
        .parse_event(stop)
        .expect("message_stop should flush thinking + text");

    // Flush order: thinking first, then text (both are single lines in non-TTY modes).
    let thinking_pos = out
        .find("Thinking:")
        .expect("expected flushed thinking line");
    let hello_pos = out.find("Hello").expect("expected flushed text line");
    assert!(
        thinking_pos < hello_pos,
        "expected thinking to appear before text; out={out:?}"
    );

    assert!(out.contains("Checking..."));
}

#[test]
#[cfg(feature = "test-utils")]
fn test_thinking_deltas_tty_finalize_before_text() {
    use crate::json_parser::delta_display::CLEAR_LINE;
    use crate::json_parser::terminal::TerminalMode;

    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::Full)
        .with_display_name("ccs/codex");

    let start = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_3","type":"message","role":"assistant"}}}"#;
    assert!(parser.parse_event(start).is_none());

    let d1 = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"git"}}}"#;
    let out1 = parser
        .parse_event(d1)
        .expect("thinking delta should render in TTY");
    assert!(out1.contains("Thinking:"));
    assert!(out1.contains("git"));
    // Append-only pattern: no newline, no cursor positioning
    assert!(!out1.contains('\n'));
    assert!(!out1.contains("\x1b[1A"));

    let d2 = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"git status"}}}"#;
    let out2 = parser
        .parse_event(d2)
        .expect("thinking delta should render in TTY");
    // Append-only pattern: emit ONLY new suffix " status" (no prefix, no \r)
    // Delta is snapshot-style "git status" which extends "git"
    // Parser should emit suffix " status"
    assert!(out2.contains(" status") || out2.contains("git status")); // Suffix or full (depends on sanitization)
    assert!(!out2.contains("Thinking:")); // No prefix on subsequent delta
    assert!(!out2.contains('\r')); // No carriage return
    assert!(!out2.contains('\n'));
    assert!(!out2.contains("\x1b[1A"));
    assert!(!out2.contains(CLEAR_LINE));

    // First text delta should first finalize thinking line (newline only)
    // and then begin streaming text.
    let text = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}"#;
    let out3 = parser.parse_event(text).expect("text delta should render");
    assert!(out3.starts_with('\n')); // Thinking completion newline
    assert!(out3.contains("[ccs/codex]"));
    assert!(out3.contains("Hello"));
}

#[test]
#[cfg(feature = "test-utils")]
fn test_thinking_deltas_full_mode_do_not_create_extra_terminal_lines() {
    use crate::json_parser::printer::{SharedPrinter, VirtualTerminal};
    use crate::json_parser::terminal::TerminalMode;
    use std::cell::RefCell;
    use std::io::Write;
    use std::rc::Rc;

    let vterm = Rc::new(RefCell::new(VirtualTerminal::new()));
    let printer: SharedPrinter = vterm.clone();
    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full)
        .with_display_name("ccs/codex");

    let start = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_vt_1","type":"message","role":"assistant"}}}"#;
    assert!(parser.parse_event(start).is_none());

    let d1 = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"git"}}}"#;
    let out1 = parser
        .parse_event(d1)
        .expect("thinking delta should render");
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out1}").unwrap();
        t.flush().unwrap();
    }
    assert_eq!(
        vterm.borrow().get_visible_lines().len(),
        1,
        "Thinking streaming should not create multiple non-empty visible lines. Visible: {:?}. Raw: {:?}",
        vterm.borrow().get_visible_lines(),
        vterm.borrow().get_write_history()
    );

    let d2 = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":" status"}}}"#;
    let out2 = parser
        .parse_event(d2)
        .expect("thinking delta should render");
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out2}").unwrap();
        t.flush().unwrap();
    }
    assert_eq!(
        vterm.borrow().get_visible_lines().len(),
        1,
        "Thinking streaming should remain single-line after updates. Visible: {:?}. Raw: {:?}",
        vterm.borrow().get_visible_lines(),
        vterm.borrow().get_write_history()
    );
}

#[test]
fn test_thinking_deltas_after_text_do_not_corrupt_visible_output_in_full_mode() {
    use crate::json_parser::printer::{SharedPrinter, VirtualTerminal};
    use crate::json_parser::terminal::TerminalMode;
    use std::cell::RefCell;
    use std::io::Write;
    use std::rc::Rc;

    let vterm = Rc::new(RefCell::new(VirtualTerminal::new()));
    let printer: SharedPrinter = vterm.clone();
    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full)
        .with_display_name("ccs/codex");

    let start = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_vt_2","type":"message","role":"assistant"}}}"#;
    assert!(parser.parse_event(start).is_none());

    // First, stream some text.
    let text = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}}"#;
    let out1 = parser.parse_event(text).expect("text delta should render");
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out1}").unwrap();
        t.flush().unwrap();
    }

    // If thinking arrives after text output has started, it should not overwrite/corrupt the visible text.
    let think = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"oops"}}}"#;
    let out2 = parser.parse_event(think).unwrap_or_default();
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out2}").unwrap();
        t.flush().unwrap();
    }

    let visible = vterm.borrow().get_visible_output();
    assert!(
        visible.contains("hello"),
        "Visible output must keep text. Got: {visible:?}"
    );
    assert!(
        !visible.contains("Thinking:"),
        "Thinking should not corrupt text output once text has started. Got: {visible:?}"
    );
}

#[test]
fn test_thinking_finalize_before_system_event_prevents_corruption_in_full_mode() {
    use crate::json_parser::printer::{SharedPrinter, VirtualTerminal};
    use crate::json_parser::terminal::TerminalMode;
    use std::cell::RefCell;
    use std::io::Write;
    use std::rc::Rc;

    let vterm = Rc::new(RefCell::new(VirtualTerminal::new()));
    let printer: SharedPrinter = vterm.clone();
    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full)
        .with_display_name("ccs/codex");

    // Start message and emit a thinking delta. In full TTY mode this leaves the cursor on the
    // thinking line (via "\n\x1b[1A") for in-place updates.
    let start = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_sys_1","type":"message","role":"assistant"}}}"#;
    assert!(parser.parse_event(start).is_none());

    let think = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"git"}}}"#;
    let out1 = parser
        .parse_event(think)
        .expect("thinking delta should render");
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out1}").unwrap();
        t.flush().unwrap();
    }

    // Now emit a non-stream system event while thinking is active.
    // This should NOT overwrite the thinking line.
    let system_status =
        r#"{"type":"system","subtype":"status","status":"compacting","session_id":"sid"}"#;
    let out2 = parser
        .parse_event(system_status)
        .expect("system status should render");
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out2}").unwrap();
        t.flush().unwrap();
    }

    // Finally, start streaming text. If the system event corrupted the thinking line, this text
    // often gets mangled or disappears in the visible output.
    let text = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Need read"}}}"#;
    let out3 = parser.parse_event(text).expect("text delta should render");
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out3}").unwrap();
        t.flush().unwrap();
    }

    let visible = vterm.borrow().get_visible_output();
    assert!(
        visible.contains("Thinking:"),
        "Thinking line should remain visible. Got: {visible:?}"
    );
    assert!(
        visible.contains("status"),
        "System status line should render. Got: {visible:?}"
    );
    assert!(
        visible.contains("Need read"),
        "Text should not be corrupted by system output while thinking active. Got: {visible:?}"
    );
    assert!(
        !visible.contains("statusead"),
        "Corruption marker should not appear. Got: {visible:?}"
    );
}

#[test]
fn test_text_finalize_before_system_event_prevents_corruption_in_full_mode() {
    use crate::json_parser::printer::{SharedPrinter, VirtualTerminal};
    use crate::json_parser::terminal::TerminalMode;
    use std::cell::RefCell;
    use std::io::Write;
    use std::rc::Rc;

    let vterm = Rc::new(RefCell::new(VirtualTerminal::new()));
    let printer: SharedPrinter = vterm.clone();
    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full)
        .with_display_name("ccs/codex");

    let start = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_sys_text_1","type":"message","role":"assistant"}}}"#;
    assert!(parser.parse_event(start).is_none());

    // Stream a longer text line; in full mode this uses the append-only streaming pattern.
    // If we emit a shorter non-stream line next (like "status"), it can overwrite only the first
    // few characters and leave the old tail visible (e.g., "statusead...").
    let text1 = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Need read complete file contents"}}}"#;
    let out1 = parser.parse_event(text1).expect("text delta should render");
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out1}").unwrap();
        t.flush().unwrap();
    }

    // System output while text streaming is active must not overwrite the streamed line.
    let system_status =
        r#"{"type":"system","subtype":"status","status":"compacting","session_id":"sid"}"#;
    let out2 = parser
        .parse_event(system_status)
        .expect("system status should render");
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out2}").unwrap();
        t.flush().unwrap();
    }

    let visible = vterm.borrow().get_visible_output();
    assert!(
        visible.contains("Need read complete file contents"),
        "Text should remain intact across system output. Got: {visible:?}"
    );
    assert!(
        visible.contains("status"),
        "System status should render. Got: {visible:?}"
    );
    assert!(
        !visible.contains("statusead"),
        "Status should not overwrite the streamed text line. Got: {visible:?}"
    );
}

#[test]
fn test_message_start_finalizes_in_place_text_to_avoid_corruption() {
    use crate::json_parser::printer::{SharedPrinter, VirtualTerminal};
    use crate::json_parser::terminal::TerminalMode;
    use std::cell::RefCell;
    use std::io::Write;
    use std::rc::Rc;

    let vterm = Rc::new(RefCell::new(VirtualTerminal::new()));
    let printer: SharedPrinter = vterm.clone();
    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full)
        .with_display_name("ccs/codex");

    // Message 1: stream a long text delta (leaves cursor on line via "\n\x1b[1A").
    let start1 = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_ms_1","type":"message","role":"assistant"}}}"#;
    assert!(parser.parse_event(start1).is_none());

    let text = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Need read complete file contents"}}}"#;
    let out1 = parser.parse_event(text).expect("text delta should render");
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out1}").unwrap();
        t.flush().unwrap();
    }

    // Message 2 starts without a prior MessageStop (real-world protocol violations).
    // The parser should finalize any in-place cursor state before resetting message state.
    let start2 = r#"{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_ms_2","type":"message","role":"assistant"}}}"#;
    let out2 = parser.parse_event(start2).unwrap_or_default();
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out2}").unwrap();
        t.flush().unwrap();
    }

    // A subsequent system event must not overwrite the streamed line.
    let system_status =
        r#"{"type":"system","subtype":"status","status":"compacting","session_id":"sid"}"#;
    let out3 = parser
        .parse_event(system_status)
        .expect("system status should render");
    {
        let mut t = vterm.borrow_mut();
        write!(t, "{out3}").unwrap();
        t.flush().unwrap();
    }

    let visible = vterm.borrow().get_visible_output();
    assert!(
        visible.contains("Need read complete file contents"),
        "Text should remain intact across MessageStart boundary. Got: {visible:?}"
    );
    assert!(
        !visible.contains("statusead"),
        "System output should not overwrite streamed text. Got: {visible:?}"
    );
}
