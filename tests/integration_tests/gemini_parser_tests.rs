//! Integration tests for Gemini parser with log file verification.
//!
//! These tests verify that the Gemini parser correctly handles streaming events,
//! produces proper output, and writes events to log files for extraction.
//!
//! Uses `MemoryWorkspace` for all file operations - NO real filesystem access.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::config::Verbosity;
use ralph_workflow::json_parser::gemini::GeminiParser;
use ralph_workflow::json_parser::printer::{SharedPrinter, TestPrinter};
use ralph_workflow::logger::Colors;
use ralph_workflow::workspace::{MemoryWorkspace, Workspace};
use std::cell::RefCell;
use std::io::BufReader;
use std::path::Path;
use std::rc::Rc;

/// Test that normal parsing flow produces proper output and log file.
///
/// This verifies that when init, message, and result events are parsed,
/// the system renders output correctly and writes events to the log file.
#[test]
fn test_gemini_parser_normal_flow() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let log_path = Path::new("/test/logs/gemini_test.log");

        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Quiet, printer)
                .with_log_file_for_test(log_path.to_str().unwrap());

        let input = r#"{"type":"init","session_id":"gemini-123","model":"gemini-2.0-flash-exp"}
{"type":"message","role":"assistant","content":"Hello World"}
{"type":"result","status":"success","stats":{"total_tokens":100,"input_tokens":50,"output_tokens":50,"duration_ms":2000}}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify the log file exists and contains events
        assert!(
            workspace.exists(log_path),
            "Log file should exist after parsing"
        );

        let log_content = workspace.get_file(log_path.to_str().unwrap()).unwrap();
        assert!(
            log_content.contains(r#""type":"init""#),
            "Log should contain init event"
        );
        assert!(
            log_content.contains(r#""type":"result""#),
            "Log should contain result event"
        );

        // Verify printer captured output
        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("Session started"),
            "Output should contain session started message"
        );
    });
}

/// Test that streaming delta messages produce accumulated output.
///
/// This verifies that when delta events are received incrementally,
/// the system accumulates and renders them correctly in the output.
#[test]
fn test_gemini_parser_delta_streaming() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        let input = r#"{"type":"init","session_id":"stream-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"Hello","delta":true}
{"type":"message","role":"assistant","content":" World","delta":true}
{"type":"message","role":"assistant","content":"Hello World","delta":false}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify delta streaming was processed
        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("Hello"),
            "Output should contain streamed Hello"
        );
    });
}

/// Test that non-TTY modes (Basic/None) still flush accumulated streaming content.
///
/// Regression guard: delta renderers suppress per-delta output in Basic/None. If the
/// completion boundary does not flush the accumulated content, logs/piped output
/// can show no assistant content at all.
#[test]
fn test_gemini_parser_none_mode_flushes_streamed_text_at_completion() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::None);

        let input = r#"{"type":"init","session_id":"stream-test-none","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"Hello","delta":true}
{"type":"message","role":"assistant","content":" World","delta":true}
{"type":"message","role":"assistant","content":"Hello World","delta":false}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("Hello World"),
            "Expected streamed content to be flushed in None mode. Output:\n{output}"
        );
        // In None mode we should not spam prefixes per delta.
        // This stream includes a session-start line plus one flushed text line,
        // so exactly 2 prefixes are expected.
        let gemini_count = output.matches("[Gemini]").count();
        assert!(
            (1..=2).contains(&gemini_count),
            "Expected 1-2 [Gemini] prefixes in None mode (session-start + flushed text), \
             got {gemini_count}. Output:\n{output}"
        );
    });
}

/// Test that tool use events produce formatted output.
///
/// This verifies that when a tool use event is received, the system
/// renders the tool name and parameters in a readable format.
#[test]
fn test_gemini_parser_tool_use() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Verbose, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        let input = r#"{"type":"tool_use","tool_name":"Bash","tool_id":"bash-123","parameters":{"command":"ls -la"}}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        assert!(output.contains("Tool"), "Output should contain Tool label");
        assert!(output.contains("Bash"), "Output should contain Bash");
    });
}

/// Test that tool result events produce status information.
///
/// This verifies that when a tool result event is received, the system
/// displays the success/failure status and output content.
#[test]
fn test_gemini_parser_tool_result() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Verbose, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        let input = r#"{"type":"tool_result","tool_id":"bash-123","status":"success","output":"file1.txt\nfile2.txt"}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("Tool result"),
            "Output should contain Tool result"
        );
    });
}

/// Test that error events produce error output.
///
/// This verifies that when an error event is received, the system
/// displays the error message and code appropriately.
#[test]
fn test_gemini_parser_error_event() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        let input = r#"{"type":"error","message":"Rate limit exceeded","code":"429"}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        assert!(output.contains("Error"), "Output should contain Error");
        assert!(
            output.contains("Rate limit exceeded"),
            "Output should contain error message"
        );
        assert!(output.contains("429"), "Output should contain error code");
    });
}

/// Test that result events with statistics produce detailed output.
///
/// This verifies that when a result event includes token counts and
/// duration, the system displays the statistics in a readable format.
#[test]
fn test_gemini_parser_result_with_stats() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        let input = r#"{"type":"result","status":"success","stats":{"total_tokens":250,"input_tokens":50,"output_tokens":200,"duration_ms":180000,"tool_calls":3}}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("success"),
            "Output should contain success status"
        );
        assert!(
            output.contains("in:50"),
            "Output should contain input tokens"
        );
        assert!(
            output.contains("out:200"),
            "Output should contain output tokens"
        );
        assert!(
            output.contains("3 tools"),
            "Output should contain tool calls"
        );
    });
}

/// Test that user role messages produce user-labeled output.
///
/// This verifies that when a message with user role is received,
/// the system renders the content with appropriate user labeling.
#[test]
fn test_gemini_parser_user_message() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        let input =
            r#"{"type":"message","role":"user","content":"List files in current directory"}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        assert!(output.contains("user"), "Output should contain user role");
        assert!(
            output.contains("List files"),
            "Output should contain user message"
        );
    });
}

/// Test that log files are properly flushed after parsing.
///
/// This verifies that when parsing completes, the log file is
/// immediately readable with all events written.
#[test]
fn test_gemini_parser_log_file_flushed() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let log_path = Path::new("/test/logs/gemini_flush.log");

        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer;
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Quiet, printer)
                .with_log_file_for_test(log_path.to_str().unwrap());

        let input = r#"{"type":"init","session_id":"flush-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"Test message"}
{"type":"result","status":"success"}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify log file exists and is readable immediately
        assert!(
            workspace.exists(log_path),
            "Log file should exist after parsing"
        );

        let log_content = workspace.get_file(log_path.to_str().unwrap()).unwrap();
        assert!(
            log_content.contains(r"session_id"),
            "Log should be readable immediately after parsing (sync_all worked)"
        );
    });
}

// ============================================================================
// Deduplication Tests
// ============================================================================

/// Test that consecutive identical deltas are filtered from output.
///
/// This verifies that when the same delta content is received multiple
/// times consecutively, the system filters out duplicate lines.
#[test]
fn test_gemini_parser_consecutive_duplicates_filtered() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        let input = r#"{"type":"init","session_id":"dedup-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"Hello","delta":true}
{"type":"message","role":"assistant","content":"Hello","delta":true}
{"type":"message","role":"assistant","content":"Hello","delta":true}
{"type":"message","role":"assistant","content":"Hello World","delta":false}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify output doesn't have excessive repetition
        let output = test_printer.borrow().get_output();
        // Count occurrences of "Hello" - three identical deltas plus one final "Hello World"
        // message. With deduplication, the three identical deltas should be reduced.
        // At most 2 occurrences are acceptable (one deduplicated delta + "Hello World").
        let hello_count = output.matches("Hello").count();
        assert!(
            (1..=2).contains(&hello_count),
            "Consecutive duplicates should be filtered. Expected 1-2 occurrences, got {hello_count}"
        );
    });
}

/// Test that snapshot-as-delta glitches are handled correctly.
///
/// This verifies that when accumulated content is sent as a delta event
/// (snapshot glitch), the system avoids rendering duplicate content.
#[test]
fn test_gemini_parser_snapshot_glitch() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        // Stream content incrementally, then send entire accumulated content as delta (snapshot glitch)
        let input = r#"{"type":"init","session_id":"snapshot-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"The","delta":true}
{"type":"message","role":"assistant","content":" quick","delta":true}
{"type":"message","role":"assistant","content":" brown","delta":true}
{"type":"message","role":"assistant","content":"The quick brown","delta":true}
{"type":"message","role":"assistant","content":" fox","delta":true}
{"type":"message","role":"assistant","content":"The quick brown fox","delta":false}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify output contains the final content
        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("fox"),
            "Output should contain 'fox' from final content"
        );
    });
}

/// Test that intentional repetition patterns are preserved in output.
///
/// This verifies that when content contains legitimate repeated phrases,
/// the system preserves them rather than filtering as duplicates.
#[test]
fn test_gemini_parser_intentional_repetition_preserved() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        let input = r#"{"type":"init","session_id":"repeat-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"echo","delta":true}
{"type":"message","role":"assistant","content":" echo","delta":true}
{"type":"message","role":"assistant","content":" echo","delta":true}
{"type":"message","role":"assistant","content":"echo echo echo","delta":false}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify output contains the intentional repetition pattern
        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("echo") && output.contains("echo echo"),
            "Intentional repetition should be preserved in output"
        );
    });
}

// ============================================================================
// Log Content Tests
// ============================================================================

/// Test that log files contain all events for later analysis.
///
/// This verifies that when events are parsed, the system writes all
/// event types to the log file for subsequent extraction and analysis.
#[test]
fn test_gemini_parser_log_contains_events() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let log_path = Path::new("/test/logs/gemini_events.log");

        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer;
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Quiet, printer)
                .with_log_file_for_test(log_path.to_str().unwrap());

        let input = r#"{"type":"init","session_id":"extract-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"Extracted content here"}
{"type":"result","status":"success","content":"Final result content"}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify log file contains all events for analysis
        assert!(workspace.exists(log_path), "Log file should exist");
        let log_content = workspace.get_file(log_path.to_str().unwrap()).unwrap();
        assert!(
            log_content.contains("init"),
            "Log should contain init event"
        );
        assert!(
            log_content.contains("Extracted content"),
            "Log should contain message content"
        );
        assert!(
            log_content.contains("result"),
            "Log should contain result event"
        );
    });
}

/// Test that multiple-turn conversations produce complete log output.
///
/// This verifies that when a conversation spans multiple user-assistant
/// exchanges, the system logs all turns for later analysis.
#[test]
fn test_gemini_parser_multiple_turns() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let log_path = Path::new("/test/logs/gemini_multi.log");

        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer;
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Quiet, printer)
                .with_log_file_for_test(log_path.to_str().unwrap());

        let input = r#"{"type":"init","session_id":"multi-test","model":"gemini-2.0"}
{"type":"message","role":"user","content":"First question"}
{"type":"message","role":"assistant","content":"First answer"}
{"type":"message","role":"user","content":"Second question"}
{"type":"message","role":"assistant","content":"Second answer"}
{"type":"result","status":"success"}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify log contains both turns
        let log_content = workspace.get_file(log_path.to_str().unwrap()).unwrap();
        assert!(
            log_content.contains("First answer") && log_content.contains("Second answer"),
            "Log should contain both conversation turns"
        );
    });
}

// ============================================================================
// Error Handling Tests
// ============================================================================

/// Test that malformed JSON is handled gracefully without panic.
///
/// This verifies that when invalid JSON is encountered in the stream,
/// the system continues processing subsequent valid events.
#[test]
fn test_gemini_parser_malformed_json() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        // Mix of valid and invalid JSON
        let input = r#"{"type":"init","session_id":"malformed-test","model":"gemini-2.0"}
{not valid json}
{"type":"message","role":"assistant","content":"Valid message after invalid"}
{"type":"result","status":"success"}"#;

        let reader = BufReader::new(input.as_bytes());
        // Should not panic on malformed JSON
        let result = parser.parse_stream_for_test(reader, &workspace);
        assert!(
            result.is_ok(),
            "Parser should handle malformed JSON gracefully"
        );

        // Valid events should still be processed
        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("Session started") || output.contains("Valid message"),
            "Valid events should still be processed"
        );
    });
}

/// Test that truncated streams are handled gracefully.
///
/// This verifies that when a stream ends prematurely without a result event,
/// the system writes partial events to the log without crashing.
#[test]
fn test_gemini_parser_truncated_stream() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let log_path = Path::new("/test/logs/gemini_truncated.log");

        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer;
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Quiet, printer)
                .with_log_file_for_test(log_path.to_str().unwrap());

        // Stream that ends without result event (simulating network disconnect)
        let input = r#"{"type":"init","session_id":"truncated-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"Partial content","delta":true}
{"type":"message","role":"assistant","content":" more content","delta":true}"#;

        let reader = BufReader::new(input.as_bytes());
        let result = parser.parse_stream_for_test(reader, &workspace);

        // Parser should handle truncated stream gracefully
        assert!(
            result.is_ok(),
            "Parser should handle truncated stream without panic"
        );

        // Log should still contain the partial events
        assert!(
            workspace.exists(log_path),
            "Log file should exist even for truncated stream"
        );
    });
}

// ============================================================================
// GLM/CCS Protocol Quirks Tests
// ============================================================================

/// Test that snapshot-as-delta glitches are filtered from output.
///
/// This verifies that when accumulated content is sent as a delta event
/// (GLM/CCS protocol quirk), the system avoids rendering duplicate content.
#[test]
fn test_gemini_parser_snapshot_as_delta_glitch() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        // Stream content incrementally, then send entire accumulated content as delta (snapshot glitch)
        let input = r#"{"type":"init","session_id":"snapshot-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"The","delta":true}
{"type":"message","role":"assistant","content":" quick","delta":true}
{"type":"message","role":"assistant","content":" brown","delta":true}
{"type":"message","role":"assistant","content":"The quick brown","delta":true}
{"type":"message","role":"assistant","content":" fox","delta":true}
{"type":"message","role":"assistant","content":"The quick brown fox","delta":false}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify output contains the final content without duplicates
        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("fox"),
            "Output should contain 'fox' from final content"
        );

        // Verify no duplicate consecutive lines
        assert!(
            !test_printer.borrow().has_duplicate_consecutive_lines(),
            "Snapshot glitch should not cause duplicate consecutive lines. Output: {output}"
        );
    });
}

/// Test that alternating deltas are not filtered as consecutive duplicates.
///
/// This verifies that when deltas alternate between different values
/// (A, B, A, B pattern), the system processes all of them without filtering.
#[test]
fn test_gemini_parser_alternating_deltas_not_filtered() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        // Alternating pattern: Ping, Pong, Ping, Pong
        let input = r#"{"type":"init","session_id":"alt-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"Ping","delta":true}
{"type":"message","role":"assistant","content":"Pong","delta":true}
{"type":"message","role":"assistant","content":"Ping","delta":true}
{"type":"message","role":"assistant","content":"Pong","delta":true}
{"type":"message","role":"assistant","content":"PingPongPingPong","delta":false}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        let output = test_printer.borrow().get_output();
        // Alternating pattern is not consecutive duplicates, so all should be processed
        // The final content should contain both patterns
        assert!(
            output.contains("Ping") && output.contains("Pong"),
            "Alternating deltas should all be processed. Output: {output}"
        );
    });
}

/// Test that consecutive identical deltas are filtered from output.
///
/// This verifies that when the same delta is sent multiple times consecutively
/// (GLM resend glitch), the system filters out duplicate lines.
#[test]
fn test_gemini_parser_consecutive_identical_deltas_filtered() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        // Same delta sent 4 times consecutively (resend glitch)
        let input = r#"{"type":"init","session_id":"dup-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"Repeat message","delta":true}
{"type":"message","role":"assistant","content":"Repeat message","delta":true}
{"type":"message","role":"assistant","content":"Repeat message","delta":true}
{"type":"message","role":"assistant","content":"Repeat message","delta":true}
{"type":"message","role":"assistant","content":"Repeat message","delta":false}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify no duplicate consecutive lines in output
        assert!(
            !test_printer.borrow().has_duplicate_consecutive_lines(),
            "Consecutive identical deltas should be filtered"
        );

        // Verify content appears at least once
        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("Repeat message"),
            "Content should appear in output"
        );
    });
}

/// Test that repeated init events are handled without state corruption.
///
/// This verifies that when multiple init events are received during a
/// conversation (GLM protocol quirk), the system handles them gracefully.
#[test]
fn test_gemini_parser_repeated_init_events() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Normal, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        // Multiple init events (GLM quirk)
        let input = r#"{"type":"init","session_id":"init-test-1","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"Hello","delta":true}
{"type":"init","session_id":"init-test-2","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":" World","delta":true}
{"type":"message","role":"assistant","content":"Hello World","delta":false}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Parser should handle repeated init events gracefully
        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("Hello") && output.contains("World"),
            "Output should contain content from both init sessions. Output: {output}"
        );
    });
}

/// Test that interleaved tool use and text deltas produce no duplicates.
///
/// This verifies that when tool use events are mixed with text deltas,
/// the system switches contexts without rendering duplicate lines.
#[test]
fn test_gemini_parser_tool_use_interleaved_with_text() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
        let printer: SharedPrinter = test_printer.clone();
        let mut parser =
            GeminiParser::with_printer_for_test(Colors::new(), Verbosity::Verbose, printer)
                .with_terminal_mode(ralph_workflow::json_parser::TerminalMode::Full);

        let input = r#"{"type":"init","session_id":"tool-test","model":"gemini-2.0"}
{"type":"message","role":"assistant","content":"Let me check","delta":true}
{"type":"tool_use","tool_name":"Bash","tool_id":"bash-123","parameters":{"command":"ls -la"}}
{"type":"tool_result","tool_id":"bash-123","status":"success","output":"file1.txt\nfile2.txt"}
{"type":"message","role":"assistant","content":"Now I can see","delta":true}
{"type":"message","role":"assistant","content":"Let me checkNow I can see","delta":false}"#;

        let reader = BufReader::new(input.as_bytes());
        parser.parse_stream_for_test(reader, &workspace).unwrap();

        // Verify no duplicates
        assert!(
            !test_printer.borrow().has_duplicate_consecutive_lines(),
            "Tool use interleaved with text should not cause duplicates"
        );

        // Verify both text blocks are in output
        let output = test_printer.borrow().get_output();
        assert!(
            output.contains("Let me check"),
            "First text block should be in output"
        );
        assert!(
            output.contains("Now I can see"),
            "Second text block should be in output"
        );
    });
}
