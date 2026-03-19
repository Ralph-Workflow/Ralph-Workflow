// Tests for render deduplication (preventing visual repetition)
// Verifies that identical accumulated content is not rendered multiple times.

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

/// Test that `StreamingSession`'s `is_content_rendered` works correctly with prefix trie.
#[cfg(test)]
#[test]
fn test_streaming_session_is_content_rendered() {
    use crate::json_parser::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // First delta - should not skip (not rendered yet)
    session.on_text_delta(0, "Hello");
    assert!(
        !session.is_content_rendered(super::types::ContentType::Text, "0"),
        "First delta should not be detected as rendered"
    );

    // Mark as rendered using trie
    session.mark_content_rendered(super::types::ContentType::Text, "0");

    // Now should skip (exact match in trie)
    assert!(
        session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Same content should be detected as already rendered"
    );

    // Second delta that changes content - should not skip (new content)
    session.on_text_delta(0, " World");
    // "Hello World" is not an exact match for "Hello" in trie
    assert!(
        !session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Changed content should not be detected as rendered"
    );

    // But it should detect prefix match
    assert!(
        session.has_rendered_prefix(super::types::ContentType::Text, "0"),
        "Changed content should have prefix match (starts with 'Hello')"
    );

    // Mark new content as rendered
    session.mark_content_rendered(super::types::ContentType::Text, "0");

    // Now exact match should work
    assert!(
        session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Marked content should be detected as rendered"
    );
}

/// Test that `mark_content_rendered` updates the prefix trie correctly.
#[cfg(test)]
#[test]
fn test_streaming_session_mark_content_rendered() {
    use crate::json_parser::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // First delta
    session.on_text_delta(0, "Hello");

    // Initially, should not skip (nothing in trie yet)
    assert!(
        !session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Should not skip before first render"
    );

    // Mark as rendered using trie
    session.mark_content_rendered(super::types::ContentType::Text, "0");

    // Now should skip (exact match in trie)
    assert!(
        session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Should skip after marking same content as rendered"
    );

    // Add more content
    session.on_text_delta(0, " World");

    // Should not skip anymore (content is different - "Hello World" != "Hello")
    assert!(
        !session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Should not skip after content changes"
    );

    // But prefix match should detect that "Hello World" starts with "Hello"
    assert!(
        session.has_rendered_prefix(super::types::ContentType::Text, "0"),
        "Should detect prefix match (Hello World starts with Hello)"
    );
}

/// Test that `message_start` clears the rendered content trie.
#[cfg(test)]
#[test]
fn test_message_start_clears_rendered_content() {
    use crate::json_parser::streaming_state::StreamingSession;

    let mut session = StreamingSession::new();
    session.on_message_start();

    // First delta and mark as rendered
    session.on_text_delta(0, "Hello");
    session.mark_content_rendered(super::types::ContentType::Text, "0");

    // Should skip (exact match in trie)
    assert!(
        session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Should detect rendered content before message_start"
    );

    // New message - should clear trie
    session.on_message_start();

    // Add same content again
    session.on_text_delta(0, "Hello");

    // Should not skip (trie was cleared)
    assert!(
        !session.is_content_rendered(super::types::ContentType::Text, "0"),
        "Should not skip after message_start clears trie"
    );
}
