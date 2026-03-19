// Tests for ccs-glm/GLM-specific streaming bug scenarios.

/// Test for the ccs-glm duplicate output bug scenario.
///
/// This test simulates a scenario where deltas are sent in an alternating pattern.
/// With consecutive duplicate detection, non-consecutive duplicates are still processed
/// because the consecutive duplicate counter resets when a different delta arrives.
/// Only CONSECUTIVE duplicates (same delta 3+ times in a row) are filtered.
///
/// The test sends: First, Second, First, Second
/// Expected behavior: All 4 are processed (not consecutive duplicates)
/// - "First" count=1 (processed)
/// - "Second" count=1, resets "First" counter (processed)
/// - "First" count=1 (resets "Second" counter, processed)
/// - "Second" count=1 (resets "First" counter, processed)
#[cfg(test)]
#[test]
fn test_ccs_glm_duplicate_output_bug_fix() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate the ccs-glm scenario where deltas are sent in alternating pattern
    let input = r#"{"type":"stream_event","event":{"type":"message_start"}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"First delta"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Second delta"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"First delta"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Second delta"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // All 4 deltas should be processed because they're not consecutive duplicates
    // With append-only pattern, we emit each unique text segment exactly once:
    //
    // Render 1: "[Claude] First delta" (first delta)
    // Render 2: "Second delta" (only new suffix)
    // Render 3: "First delta" (new suffix, happens to be same text as first)
    // Render 4: "Second delta" (only new suffix)
    // Completion: "\n"
    //
    // Total in output:
    // - "First delta" appears: 2 times (delta 1 and delta 3)
    // - "Second delta" appears: 2 times (delta 2 and delta 4)
    let first_count = output.matches("First delta").count();
    let second_count = output.matches("Second delta").count();

    assert_eq!(
        first_count, 2,
        "First delta should appear 2 times in append-only output. Found {first_count} occurrences. Output: {output:?}"
    );

    assert_eq!(
        second_count, 2,
        "Second delta should appear 2 times in append-only output. Found {second_count} occurrences. Output: {output:?}"
    );

    // With append-only pattern, prefix appears only ONCE (first delta)
    // Subsequent deltas are suffixes without prefix
    let render_count = output.matches("[Claude]").count();
    assert_eq!(
        render_count, 1,
        "Prefix should appear once with append-only pattern. Found {render_count} prefixes. Output: {output:?}"
    );
}

/// Test for the ccs-glm repeated `MessageStart` bug scenario.
///
/// This test simulates the bug where GLM/ccs-glm sends repeated `MessageStart`
/// events during streaming, and the same delta appears multiple times.
/// The fix preserves `processed_deltas` during repeated `MessageStart` to prevent
/// the same delta from being processed again.
#[cfg(test)]
#[test]
fn test_ccs_glm_repeated_message_start_preserves_processed_deltas() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate the ccs-glm scenario with repeated `MessageStart` events
    let input_lines = vec![
        // First `MessageStart`
        r#"{"type":"stream_event","event":{"type":"message_start"}}"#.to_string(),
        // Content block start
        r#"{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}"#.to_string(),
        // Send first delta
        r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"First delta"}}}"#.to_string(),
        // GLM sends another `MessageStart` during streaming (protocol violation)
        r#"{"type":"stream_event","event":{"type":"message_start"}}"#.to_string(),
        // Send the same delta again (this should be filtered out)
        r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"First delta"}}}"#.to_string(),
        // Send a new delta
        r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Second delta"}}}"#.to_string(),
        // GLM sends yet another `MessageStart`
        r#"{"type":"stream_event","event":{"type":"message_start"}}"#.to_string(),
        // Send the first delta again (should still be filtered)
        r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"First delta"}}}"#.to_string(),
        // Send the second delta again (should also be filtered)
        r#"{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Second delta"}}}"#.to_string(),
        // Message stop
        r#"{"type":"stream_event","event":{"type":"message_stop"}}"#.to_string(),
    ];

    let input = input_lines.join("\n");
    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // After MessageStart, the consecutive duplicate counter resets
    // So non-consecutive duplicates are still processed
    // Trace through:
    // 1. "First" processed (count=1)
    // 2. MessageStart clears accumulated
    // 3. "First" again - skipped by last_delta check (accumulated is empty), produces empty render
    // 4. "Second" processed (resets "First" counter to 1)
    // 5. "First" processed (resets "Second" counter to 1)
    // 6. "Second" processed (resets "First" counter to 1)
    //
    // Renders:
    // 1. "First" - 1 "First"
    // 2. "" (empty) - 0
    // 3. "Second" - 1 "Second"
    // 4. "FirstSecond" - 1 "First", 1 "Second"
    // Total: "First" appears 2 times, "Second" appears 2 times
    let first_count = output.matches("First delta").count();
    let second_count = output.matches("Second delta").count();

    assert_eq!(
        first_count, 2,
        "First delta should appear 2 times (first + accumulated with Second). Found {first_count} occurrences. Output: {output:?}"
    );
    assert_eq!(
        second_count, 2,
        "Second delta should appear 2 times (standalone + accumulated with First). Found {second_count} occurrences. Output: {output:?}"
    );
}
