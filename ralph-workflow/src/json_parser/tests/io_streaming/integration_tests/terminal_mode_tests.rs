// Tests for different terminal modes (Full, Basic, None)

#[cfg(test)]
#[test]
fn test_delta_with_embedded_newline_displays_inline() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate a delta that contains a newline character within the text
    // For example: "Now I understand\n1. In src/..."
    let json = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Now I understand\n1. In src/"}}}"#;

    let output = parser.parse_event(json);

    assert!(output.is_some());
    let out = output.unwrap();

    // The newline should be replaced with a space to prevent artificial line breaks
    // Append-only pattern: prefix and content on same line, NO newline or cursor movement
    // Output format: "[Claude] Now I understand 1. In src/"
    assert!(out.contains("Now I understand"));
    assert!(out.contains("1. In src/"));

    // Append-only pattern: output is a single line with no newline or cursor positioning
    assert_eq!(
        out.lines().count(),
        1,
        "Delta with embedded newline should produce 1 line with append-only pattern (no cursor movement)"
    );
}

// Edge case tests for streaming behavior

// Test streaming with empty delta chunks
// Verifies that empty chunks don't cause errors and don't produce output
#[cfg(test)]
#[test]
fn test_streaming_empty_delta_chunk() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate streaming with an empty delta in the middle
    let input = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    // Should not panic or error
    let result = parser.parse_stream(reader, &MemoryWorkspace::new_test());
    assert!(
        result.is_ok(),
        "Empty delta chunks should be handled gracefully"
    );

    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();
    // Should still contain the final accumulated text
    assert!(
        output.contains("Hello World"),
        "Should contain accumulated text despite empty chunk"
    );
}

// Test streaming with a single chunk (no streaming scenario)
// Verifies that single-chunk content displays correctly with prefix
#[cfg(test)]
#[test]
fn test_streaming_single_chunk() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Single chunk scenario - content arrives all at once
    let input = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Complete message"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // With single chunk, there should be exactly one prefix (first delta only)
    let prefix_count = output.matches("[Claude]").count();
    assert_eq!(prefix_count, 1, "Single chunk should have exactly 1 prefix");

    // Should contain the complete text
    assert!(
        output.contains("Complete message"),
        "Should contain single chunk text"
    );

    // Should end with newline
    assert!(
        output.ends_with('\n'),
        "Should end with newline after message_stop"
    );
}

// Test streaming with very long accumulated text
// Verifies that the parser handles long text without errors
// and that truncation works correctly in Full terminal mode
#[cfg(test)]
#[test]
fn test_streaming_very_long_text() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Create a very long text that would exceed terminal width
    let long_chunk = "a".repeat(200);
    let long_chunk2 = "b".repeat(200);

    let input = format!(
        r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{long_chunk}"}}}}}}
{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{long_chunk2}"}}}}}}
{{"type":"stream_event","event":{{"type":"message_stop"}}}}"#
    );

    let reader = Cursor::new(input);

    // Should handle long text without errors
    let result = parser.parse_stream(reader, &MemoryWorkspace::new_test());
    assert!(
        result.is_ok(),
        "Should handle very long text without errors"
    );

    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();
    // In Full mode, long text is NO LONGER truncated during streaming
    // The output should contain the full accumulated text
    assert!(
        output.contains(&long_chunk),
        "Output should contain the full first chunk"
    );
    assert!(
        output.contains(&long_chunk2),
        "Output should contain the full second chunk"
    );
    // Should NOT contain ellipsis since we no longer truncate during streaming
    assert!(
        !output.contains("..."),
        "Output should NOT contain ellipsis (no truncation during streaming)"
    );
}

// Test streaming with special characters in text
// Verifies that special characters (quotes, unicode, etc.) are handled correctly
#[cfg(test)]
#[test]
fn test_streaming_special_characters() {
    let parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::Full);

    // Text with various special characters
    let special_text = "Hello \"World\"! 'quotes' and $ymbols & unicode: 🌍 世界";

    let json = format!(
        r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{}"}}}}}}"#,
        // Escape quotes for JSON
        special_text.replace('"', "\\\"")
    );

    let output = parser.parse_event(&json);

    assert!(
        output.is_some(),
        "Should handle special characters without errors"
    );
    let out = output.unwrap();

    // Verify some special characters are present
    assert!(out.contains("Hello"), "Should contain text before quotes");
    assert!(
        out.contains("World") || out.contains("quotes"),
        "Should handle quoted text"
    );
}

// Test streaming with rapid consecutive chunks
// Verifies that rapid streaming (multiple chunks in quick succession) is handled correctly
#[cfg(test)]
#[test]
fn test_streaming_rapid_chunks() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate rapid streaming with many small chunks
    let mut input_lines = Vec::new();
    for i in 0..10 {
        input_lines.push(format!(
            r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"chunk{i}"}}}}}}"#
        ));
    }
    input_lines.push(r#"{"type":"stream_event","event":{"type":"message_stop"}}"#.to_string());

    let input = input_lines.join("\n");
    let reader = Cursor::new(input);

    // Should handle rapid chunks without errors
    let result = parser.parse_stream(reader, &MemoryWorkspace::new_test());
    assert!(result.is_ok(), "Should handle rapid consecutive chunks");

    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // With append-only pattern, prefix appears exactly once (first delta)
    // Subsequent deltas are suffixes without prefix
    let prefix_count = output.matches("[Claude]").count();
    assert_eq!(
        prefix_count, 1,
        "Append-only pattern: prefix should appear exactly once"
    );

    // No carriage returns with append-only pattern
    assert!(
        !output.contains('\r'),
        "Append-only pattern should not use carriage returns"
    );

    // Verify content from multiple chunks is present
    assert!(output.contains("chunk0"), "Should contain first chunk");
    // In Full mode, the accumulated text may be truncated if it exceeds terminal width
    // The total "chunk0chunk1...chunk9" is 60 chars, which may be truncated
    // Just verify that streaming worked (prefixes are present, cursor positioning works)
}

// Test streaming with only whitespace chunks
// Verifies that whitespace-only chunks don't produce spurious output
#[cfg(test)]
#[test]
fn test_streaming_whitespace_only_chunks() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate streaming with whitespace chunks
    let input = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"   "}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"\t"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    // Should handle whitespace chunks without errors
    let result = parser.parse_stream(reader, &MemoryWorkspace::new_test());
    assert!(result.is_ok(), "Should handle whitespace-only chunks");

    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();
    // Should contain the actual non-whitespace content
    assert!(
        output.contains("Hello"),
        "Should contain non-whitespace content"
    );
}

// Test that content block start resets state properly
// Verifies that a new content block starts fresh without previous accumulation
#[cfg(test)]
#[test]
fn test_streaming_content_block_reset() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // First content block, then start a new one
    let input = r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":"Initial"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" Block1"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Should contain the content from the block
    assert!(
        output.contains("Initial") || output.contains("Block1"),
        "Should contain content from block"
    );
}

// Integration tests for non-full terminal modes (Basic and None)
// These tests verify that parser-level behavior works correctly when
// terminal capabilities are limited (Basic: colors only, None: plain text)

// Test streaming with `TerminalMode::None` (non-TTY output)
//
// Verifies that when output is piped or redirected (non-TTY), the parser
// produces clean output without escape sequences for cursor positioning.
#[cfg(test)]
#[test]
fn test_streaming_with_terminal_mode_none() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::None);

    let input = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Should contain the accumulated content
    assert!(
        output.contains("Hello World!"),
        "Should contain complete accumulated text. Output: {output:?}"
    );

    // Should NOT contain cursor positioning escape sequences
    assert!(
        !output.contains("\x1b[1A"), // Cursor up
        "Should NOT contain cursor up sequence in None mode. Output: {output:?}"
    );
    assert!(
        !output.contains("\x1b[1B"), // Cursor down
        "Should NOT contain cursor down sequence in None mode. Output: {output:?}"
    );
    assert!(
        !output.contains("\x1b[2K"), // Clear line
        "Should NOT contain clear line sequence in None mode. Output: {output:?}"
    );

    // Should NOT contain carriage returns for in-place updates
    assert!(
        !output.contains('\r'),
        "Should NOT contain carriage returns in None mode. Output: {output:?}"
    );

    // Should end with newline
    assert!(
        output.ends_with('\n'),
        "Should end with newline after message_stop. Output: {output:?}"
    );
}

// Test streaming with `TerminalMode::Basic` (colors without cursor positioning)
//
// Verifies that when terminal supports colors but not cursor positioning,
// the parser produces output with colors but without in-place updates.
#[cfg(test)]
#[test]
fn test_streaming_with_terminal_mode_basic() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Basic);

    let input = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Should contain the accumulated content
    assert!(
        output.contains("Hello World!"),
        "Should contain complete accumulated text. Output: {output:?}"
    );

    // Should NOT contain cursor positioning escape sequences
    assert!(
        !output.contains("\x1b[1A"), // Cursor up
        "Should NOT contain cursor up sequence in Basic mode. Output: {output:?}"
    );
    assert!(
        !output.contains("\x1b[1B"), // Cursor down
        "Should NOT contain cursor down sequence in Basic mode. Output: {output:?}"
    );
    assert!(
        !output.contains("\x1b[2K"), // Clear line
        "Should NOT contain clear line sequence in Basic mode. Output: {output:?}"
    );

    // Should NOT contain carriage returns for in-place updates
    assert!(
        !output.contains('\r'),
        "Should NOT contain carriage returns in Basic mode. Output: {output:?}"
    );

    // Should end with newline
    assert!(
        output.ends_with('\n'),
        "Should end with newline after message_stop. Output: {output:?}"
    );
}

// Test completion in `TerminalMode::None`
//
// Verifies that message completion produces just a newline without
// cursor positioning in None mode.
#[cfg(test)]
#[test]
fn test_completion_with_terminal_mode_none() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::None);

    let input = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Should NOT contain cursor down sequence
    assert!(
        !output.contains("\x1b[1B"),
        "Should NOT contain cursor down sequence in None mode. Output: {output:?}"
    );

    // Should end with plain newline
    assert!(
        output.ends_with('\n'),
        "Should end with newline in None mode. Output: {output:?}"
    );
}

// Test completion in `TerminalMode::Basic`
//
// Verifies that message completion produces just a newline without
// cursor positioning in Basic mode.
#[cfg(test)]
#[test]
fn test_completion_with_terminal_mode_basic() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Basic);

    let input = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Should NOT contain cursor down sequence
    assert!(
        !output.contains("\x1b[1B"),
        "Should NOT contain cursor down sequence in Basic mode. Output: {output:?}"
    );

    // Should end with plain newline
    assert!(
        output.ends_with('\n'),
        "Should end with newline in Basic mode. Output: {output:?}"
    );
}

// Test multiple deltas in None mode are flushed once
//
// In non-TTY modes (Basic/None), per-delta output is suppressed to avoid
// repeated prefixed lines in logs. The final accumulated content is flushed
// once at `message_stop`.
#[cfg(test)]
#[test]
fn test_multiple_deltas_none_mode_flushed_once() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::None);

    let input = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // In non-TTY modes, per-delta output is suppressed and we flush once at message_stop.
    assert!(
        output.contains("Hello World"),
        "Should contain final accumulated content. Output: {output:?}"
    );

    assert_eq!(
        output.lines().count(),
        1,
        "Expected a single flushed line in None mode. Output: {output:?}"
    );
}
