// Tests for delta-level deduplication (hash-based)
// Verifies that identical deltas are detected and handled appropriately.

/// Test that identical deltas are detected as duplicates using hash.
#[test]
fn test_delta_hash_deduplication_identical_deltas() {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let delta1 = "Hello";
    let delta2 = "Hello";

    // Compute hashes
    let mut hasher1 = DefaultHasher::new();
    delta1.hash(&mut hasher1);
    let hash1 = hasher1.finish();

    let mut hasher2 = DefaultHasher::new();
    delta2.hash(&mut hasher2);
    let hash2 = hasher2.finish();

    // Identical content should produce identical hashes
    assert_eq!(hash1, hash2, "Identical deltas should have same hash");
}

/// Test that different deltas produce different hashes.
#[test]
fn test_delta_hash_deduplication_different_deltas() {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};

    let delta1 = "Hello";
    let delta2 = "World";

    // Compute hashes
    let mut hasher1 = DefaultHasher::new();
    delta1.hash(&mut hasher1);
    let hash1 = hasher1.finish();

    let mut hasher2 = DefaultHasher::new();
    delta2.hash(&mut hasher2);
    let hash2 = hasher2.finish();

    // Different content should produce different hashes (with high probability)
    assert_ne!(
        hash1, hash2,
        "Different deltas should have different hashes"
    );
}

/// Test that identical deltas only produce output once (integration test).
#[cfg(test)]
#[test]
fn test_identical_deltas_produce_output_once() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate sending the same delta multiple times (a common bug pattern)
    let input = r#"{"type":"stream_event","event":{"type":"message_start"}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Count how many times "Hello" appears in the output
    let hello_count = output.matches("Hello").count();

    // Should only appear once (first occurrence), subsequent identical deltas are skipped
    assert_eq!(
        hello_count, 1,
        "Identical deltas should only produce output once. Found {hello_count} occurrences. Output: {output:?}"
    );
}

/// Test that different deltas each produce output.
#[cfg(test)]
#[test]
fn test_different_deltas_produce_output() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate sending different deltas
    let input = r#"{"type":"stream_event","event":{"type":"message_start"}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" World"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    parser
        .parse_stream(reader, &MemoryWorkspace::new_test())
        .unwrap();
    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // All deltas should contribute to the final output
    assert!(
        output.contains("Hello World!"),
        "All different deltas should be accumulated. Output: {output:?}"
    );
}

/// Test that empty deltas are marked as processed and don't cause repeated processing.
#[cfg(test)]
#[test]
fn test_empty_deltas_marked_as_processed() {
    use std::io::Cursor;

    let test_printer = Rc::new(RefCell::new(TestPrinter::new()));
    let printer: SharedPrinter = test_printer.clone();

    let parser = ClaudeParser::with_printer(Colors { enabled: false }, Verbosity::Normal, printer)
        .with_terminal_mode(TerminalMode::Full);

    // Simulate sending multiple empty deltas
    let input = r#"{"type":"stream_event","event":{"type":"message_start"}}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":""}}}
{"type":"stream_event","event":{"type":"message_stop"}}"#;

    let reader = Cursor::new(input);

    // Should not panic or cause excessive processing
    let result = parser.parse_stream(reader, &MemoryWorkspace::new_test());
    assert!(result.is_ok(), "Empty deltas should be handled gracefully");

    let printer_ref = test_printer.borrow();
    let output = printer_ref.get_output();

    // Empty deltas should not produce visible content
    let non_empty_lines: Vec<&str> = output.lines().filter(|l| !l.trim().is_empty()).collect();
    assert!(
        non_empty_lines.is_empty(),
        "Empty deltas should not produce non-empty output. Found {} non-empty lines. Output: {output:?}",
        non_empty_lines.len()
    );
}
