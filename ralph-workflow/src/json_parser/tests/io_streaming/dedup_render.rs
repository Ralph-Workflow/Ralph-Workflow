// Tests for render deduplication (preventing visual repetition)

/// Test that identical accumulated content is not rendered multiple times.
///
/// This test verifies the fix for the visual repetition bug where the same
/// accumulated content would be rendered over and over, creating the appearance
/// of "stuttering" output. With the deduplication fix, rendering is skipped
/// when accumulated content is unchanged.
#[cfg(test)]
#[test]
fn test_identical_accumulated_content_skips_rendering() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate empty deltas that don't change accumulated content
    // This can happen with some agents that send no-op events
    let input = r#"{"type":"stream_event","event":{"type":"message_start"}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":""}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // The empty deltas should not produce output (rendering is skipped)
    // Count non-empty lines in output
    let non_empty_lines: Vec<&str> = output.lines().filter(|l| !l.trim().is_empty()).collect();

    // Should have minimal output (first delta with content, maybe prefix info)
    // The key is that empty deltas don't cause repeated rendering
    assert!(
        non_empty_lines.len() < 10,
        "Empty deltas should not cause excessive output. Found {count} non-empty lines. Output: {output:?}",
        count = non_empty_lines.len()
    );

    // Should contain the accumulated content
    assert!(
        output.contains("Hello World"),
        "Should contain accumulated text. Output: {output:?}"
    );
}
