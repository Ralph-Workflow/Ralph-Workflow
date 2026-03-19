// Tests for ccs-glm streaming scenarios

// Test for ccs-glm streaming scenario
//
// This test simulates the problematic output pattern from the ccs-glm agent:
// - One token per line with repeated prefix (the bug we're fixing)
// - After the fix, output should have:
//   - Single-line in-place rendering with carriage returns
//   - Line clearing before each rewrite
//   - Single final newline
//   - No duplication of final message
//
// With the single-line pattern:
// - First delta: `[Claude] H\r`
// - Second delta: `\x1b[2K\r[Claude] He\r`
// - ...and so on
// - Each delta rewrites the entire line with prefix
// - Visually, the user sees only one prefix that updates in-place
#[cfg(test)]
#[test]
fn test_ccs_glm_streaming_no_duplicate_prefix() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate the problematic ccs-glm streaming pattern:
    // Many small deltas arriving one token at a time
    let mut input_lines = Vec::new();

    // Message start
    input_lines.push(r#"{"type":"stream_event","event":{"type":"message_start"}}"#.to_string());

    // Content block start
    input_lines.push(r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}"#.to_string());

    // Simulate streaming "Hello World" one token at a time
    for token in ["H", "e", "l", "l", "o", " ", "W", "o", "r", "l", "d", "!"] {
        let delta_json = format!(
            r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{token}"}}}}}}"#
        );
        input_lines.push(delta_json);
    }

    // Message stop
    input_lines.push(r#"{"type":"stream_event","event":{"type":"message_stop"}}"#.to_string());

    let input = input_lines.join("\n");
    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Verify append-only pattern:
    // 1. Prefix should appear exactly ONCE (not repeated for each delta)
    let prefix_count = output.matches("[Claude]").count();
    assert_eq!(
        prefix_count, 1,
        "Prefix should appear exactly once with append-only pattern. Output: {output:?}"
    );

    // 2. Should NOT contain carriage returns (append-only pattern avoids cursor movement)
    assert!(
        !output.contains('\r'),
        "Append-only pattern should not use carriage returns. Output: {output:?}"
    );

    // 3. Final message "Hello World!" should be present
    assert!(
        output.contains("Hello World!"),
        "Should contain complete message. Output: {output:?}"
    );

    // 4. Should end with newline (from message_stop)
    assert!(
        output.ends_with('\n'),
        "Should end with newline after message_stop. Output: {output:?}"
    );
}

// Test for ccs-glm complete message deduplication
//
// This test verifies that when a complete message event arrives after
// streaming has already displayed the content, the complete message
// is NOT re-displayed (preventing duplication).
#[cfg(test)]
#[test]
fn test_ccs_glm_complete_message_deduplication() {
    use std::io::Cursor;

    // Create a TestPrinter to capture output
    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    // This scenario targets non-TTY behavior: per-delta output is suppressed and
    // flushed once at message_stop to avoid repeated prefixed lines.
    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::None);

    // Simulate streaming followed by a complete message event
    let input = r#"{"type":"stream_event","event":{"type":"message_start"}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}}
{"type":"stream_event","event":{"type":"message_stop"}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Hello World!"}]}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();

    // Get the captured output from TestPrinter
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // The complete message should NOT be displayed because streaming already showed it
    // Count how many times the full text appears
    let full_text_count = output.matches("Hello World!").count();

    // The text should appear at most once (from the accumulated streaming output)
    // The complete message event should be skipped due to deduplication
    assert!(
        full_text_count <= 1,
        "Complete message should not be duplicated. Found {full_text_count} occurrences. Output: {output:?}"
    );

    // In non-TTY output, per-delta text output is suppressed and flushed once at message_stop.
    // We should see a single prefix for the flushed text line.
    let prefix_count = output.matches("[Claude]").count();
    assert_eq!(
        prefix_count, 1,
        "Should have 1 prefix from flushed streaming output. Output: {output:?}"
    );
}

// Test for message finalize without deltas producing no output
//
// This test verifies that when a message starts and stops without any
// content deltas, no extraneous output is produced (like spurious newlines).
#[cfg(test)]
#[test]
fn test_finalize_without_deltas_no_output() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate message_start -> message_stop with no content
    let input = r#"{"type":"stream_event","event":{"type":"message_start"}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Should have NO prefix since no content was streamed
    let prefix_count = output.matches("[Claude]").count();
    assert_eq!(
        prefix_count, 0,
        "Should have no prefix when no content was streamed. Output: {output:?}"
    );

    // Output should be empty or contain only whitespace (no actual content)
    let trimmed = output.trim();
    assert!(
        trimmed.is_empty(),
        "Should have no actual content when message has no deltas. Output: {output:?}"
    );
}

// Test for repeated `ContentBlockStart` not causing duplicate prefix
//
// This test simulates GLM sending `ContentBlockStart` repeatedly for the same
// index, which should NOT cause the next delta to show the prefix again.
// The fix ensures that accumulated content is only cleared when transitioning
// to a DIFFERENT block index, not the same index.
#[cfg(test)]
#[test]
fn test_repeated_content_block_start_no_duplicate_prefix() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate GLM sending ContentBlockStart before each delta
    let mut input_lines = Vec::new();

    // Message start
    input_lines.push(r#"{"type":"stream_event","event":{"type":"message_start"}}"#.to_string());

    // ContentBlockStart, Delta, ContentBlockStart, Delta, ContentBlockStart, Delta
    for i in 0..3 {
        // ContentBlockStart for the SAME index (0) each time
        input_lines.push(
            r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}"#
                .to_string(),
        );

        // Delta for this chunk
        let delta = format!("chunk{i} ");
        input_lines.push(format!(
            r#"{{"type":"stream_event","event":{{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{delta}"}}}}}}"#
        ));
    }

    // Message stop
    input_lines.push(r#"{"type":"stream_event","event":{"type":"message_stop"}}"#.to_string());

    let input = input_lines.join("\n");
    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // With append-only pattern, only the first delta includes the prefix.
    // Subsequent deltas emit only the new suffix without repeating the prefix.
    // Even though ContentBlockStart is repeated, it's for the same index so accumulation continues.
    let prefix_count = output.matches("[Claude]").count();
    assert_eq!(
        prefix_count, 1,
        "Should have 1 prefix (first delta only with append-only pattern) with repeated ContentBlockStart for same index. \
        Got {prefix_count} prefixes. Output: {output:?}"
    );

    // Should contain the accumulated content
    assert!(
        output.contains("chunk0"),
        "Should contain first chunk. Output: {output:?}"
    );
    assert!(
        output.contains("chunk1"),
        "Should contain second chunk. Output: {output:?}"
    );
    assert!(
        output.contains("chunk2"),
        "Should contain third chunk. Output: {output:?}"
    );
}

// Test for multi-message streaming with proper separation
//
// This test verifies that multiple complete messages in sequence are rendered
// independently with proper newlines between them, no duplication, and each
// message has its own prefix.
#[cfg(test)]
#[test]
fn test_multiple_messages_with_proper_separation() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Stream two complete messages in sequence
    let input = r#"{"type":"stream_event","event":{"type":"message_start"}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"First"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" message"}}}
{"type":"stream_event","event":{"type":"message_stop"}}
{"type":"stream_event","event":{"type":"message_start"}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Second"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" message"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // With append-only pattern, only the first delta of each message includes the prefix
    // 2 messages x 1 prefix each = 2 prefixes in output string
    let prefix_count = output.matches("[Claude]").count();
    assert_eq!(
        prefix_count, 2,
        "Should have 2 prefixes (1 per message with append-only pattern). Got {prefix_count}. Output: {output:?}"
    );

    // Should contain both messages
    assert!(
        output.contains("First message"),
        "Should contain first message. Output: {output:?}"
    );
    assert!(
        output.contains("Second message"),
        "Should contain second message. Output: {output:?}"
    );

    // Should end with newline (from final message_stop)
    assert!(
        output.ends_with('\n'),
        "Should end with newline after final message_stop. Output: {output:?}"
    );

    // Each message should appear only once (no duplication)
    let first_count = output.matches("First message").count();
    let second_count = output.matches("Second message").count();
    assert_eq!(
        first_count, 1,
        "First message should appear exactly once. Found {first_count} times. Output: {output:?}"
    );
    assert_eq!(
        second_count, 1,
        "Second message should appear exactly once. Found {second_count} times. Output: {output:?}"
    );
}
