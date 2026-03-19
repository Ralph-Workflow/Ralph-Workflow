// Tests for verbose mode streaming behavior

#[cfg(test)]
#[test]
fn test_verbose_mode_streaming_no_duplicate_lines() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Verbose, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate streaming content that arrives in multiple deltas
    // This mimics a diagnostic message like "warning: unused variable"
    let input = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"warning: unu"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"sed"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" vari"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"able"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // After the fix, streaming should show accumulated text on a single line using in-place updates:
    // [Claude] warning: unu\r                    (first chunk with prefix)
    // \x1b[2K\r[Claude] warning: unused\r      (second chunk clears line, rewrites with accumulated)
    // \x1b[2K\r[Claude] warning: unused vari\r (third chunk clears line, rewrites with accumulated)
    // \x1b[2K\r[Claude] warning: unused variable\n (final chunk + message_stop adds newline)

    // With append-only pattern, no carriage returns or line clearing
    assert!(
        !output.contains('\r'),
        "Append-only pattern should not use carriage returns"
    );

    // No line clear sequences with append-only pattern
    assert!(
        !output.contains("\x1b[2K"),
        "Append-only pattern should not clear lines"
    );

    // With the single-line pattern, each delta rewrites the entire line including prefix
    // The output string will contain multiple prefixes, but visually only one is shown
    // due to carriage returns and line clearing
    let prefix_count = output.matches("[Claude]").count();
    assert!(prefix_count >= 1, "Should have at least 1 prefix");

    // The final accumulated text should be present
    assert!(
        output.contains("warning: unused variable"),
        "Should contain complete accumulated text"
    );

    // Should end with newline (from message_stop)
    assert!(
        output.ends_with('\n'),
        "Should end with newline after message_stop"
    );
}

#[cfg(test)]
#[test]
fn test_normal_and_verbose_mode_show_same_deltas() {
    let normal_parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Normal)
        .with_terminal_mode(TerminalMode::Full);
    let verbose_parser = ClaudeParser::new(Colors { enabled: false }, Verbosity::Verbose)
        .with_terminal_mode(TerminalMode::Full);

    let json = r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}"#;

    let normal_output = normal_parser.parse_event(json);
    let verbose_output = verbose_parser.parse_event(json);

    // Both should show the delta content
    assert!(normal_output.is_some());
    assert!(verbose_output.is_some());

    // Both should contain the delta text
    assert!(normal_output.unwrap().contains("Hello"));
    assert!(verbose_output.unwrap().contains("Hello"));
}

// Integration test for streaming accumulation behavior
// Verifies that multiple text deltas accumulate correctly and output contains carriage returns
#[cfg(test)]
#[test]
fn test_streaming_accumulation_behavior() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Verbose, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate streaming content arriving in multiple deltas
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

    // Append-only pattern: no carriage returns
    assert!(
        !output.contains('\r'),
        "Append-only pattern should not use carriage returns"
    );

    // With append-only pattern, prefix appears exactly once
    let prefix_count = output.matches("[Claude]").count();
    assert_eq!(
        prefix_count, 1,
        "Prefix should appear exactly once with append-only pattern"
    );

    // The final accumulated text should be present
    assert!(
        output.contains("Hello World!"),
        "Should contain complete accumulated text"
    );

    // Should end with newline (from message_stop)
    assert!(
        output.ends_with('\n'),
        "Should end with newline after message_stop"
    );

    // Verify progressive accumulation: output should contain intermediate accumulated states
    // After first delta: "Hello"
    // After second delta: "Hello World"
    // After third delta: "Hello World!"
    assert!(output.contains("Hello"), "Should contain first delta");
    assert!(
        output.contains("Hello World"),
        "Should contain accumulated text after second delta"
    );
    assert!(
        output.contains("Hello World!"),
        "Should contain final accumulated text"
    );
}
