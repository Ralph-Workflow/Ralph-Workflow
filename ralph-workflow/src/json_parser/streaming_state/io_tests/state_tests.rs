// Tests for snapshot-as-delta detection methods.
//
// This file contains tests for:
// - is_likely_snapshot() - detecting when content appears to be a snapshot
// - get_delta_from_snapshot() - extracting delta portion from snapshot
// - Snapshot detection with various scenarios

#[test]
fn test_is_likely_snapshot_detects_snapshot() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_content_block_start(0);

    // First delta is long enough to meet threshold requirements
    // Using 40 chars to ensure it exceeds the 30 char minimum
    let initial = "This is a long message that exceeds threshold";
    session.on_text_delta(0, initial);

    // Simulate GLM sending full accumulated content as next "delta"
    // The overlap is 45 chars (100% of initial), meeting both thresholds:
    // - char_count = 45 >= 30
    // - ratio = 45/48 ~ 94% >= 50%
    // - ends at safe boundary (space)
    let snapshot = format!("{initial} plus new content");
    let is_snapshot = session.is_likely_snapshot(&snapshot, "0");
    assert!(
        is_snapshot,
        "Should detect snapshot-as-delta with strong overlap"
    );
}

#[test]
fn test_is_likely_snapshot_returns_false_for_genuine_delta() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_content_block_start(0);

    // First delta is "Hello"
    session.on_text_delta(0, "Hello");

    // Genuine delta " World" doesn't start with previous content
    let is_snapshot = session.is_likely_snapshot(" World", "0");
    assert!(
        !is_snapshot,
        "Genuine delta should not be flagged as snapshot"
    );
}

#[test]
fn test_is_likely_snapshot_returns_false_when_no_previous_content() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_content_block_start(0);

    // No previous content, so anything is a genuine first delta
    let is_snapshot = session.is_likely_snapshot("Hello", "0");
    assert!(
        !is_snapshot,
        "First delta should not be flagged as snapshot"
    );
}

#[test]
fn test_extract_delta_from_snapshot() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_content_block_start(0);

    // First delta is long enough to meet threshold requirements
    let initial = "This is a long message that exceeds threshold";
    session.on_text_delta(0, initial);

    // Snapshot should extract new portion
    let snapshot = format!("{initial} plus new content");
    let delta = session.get_delta_from_snapshot(&snapshot, "0").unwrap();
    assert_eq!(delta, " plus new content");
}

#[test]
fn test_extract_delta_from_snapshot_empty_delta() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_content_block_start(0);

    // First delta is "Hello"
    session.on_text_delta(0, "Hello");

    // Snapshot "Hello" (identical to previous) should extract "" as delta
    let delta = session.get_delta_from_snapshot("Hello", "0").unwrap();
    assert_eq!(delta, "");
}

#[test]
fn test_extract_delta_from_snapshot_returns_error_on_non_snapshot() {
    let mut session = StreamingSession::new();
    session.on_message_start();
    session.on_content_block_start(0);

    // First delta is "Hello"
    session.on_text_delta(0, "Hello");

    // Calling on non-snapshot should return error (not panic)
    let result = session.get_delta_from_snapshot("World", "0");
    assert!(result.is_err());
    assert!(result
        .unwrap_err()
        .contains("extract_delta_from_snapshot called on non-snapshot text"));
}

#[test]
fn test_snapshot_detection_with_string_keys() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // Test with string keys (like Codex/Gemini use)
    // Use content long enough to meet threshold requirements
    let initial = "This is a long message that exceeds threshold";
    session.on_text_delta_key("main", initial);

    // Should detect snapshot for string key with strong overlap
    let snapshot = format!("{initial} plus new content");
    let is_snapshot = session.is_likely_snapshot(&snapshot, "main");
    assert!(
        is_snapshot,
        "Should detect snapshot with string keys when thresholds are met"
    );

    // Should extract delta correctly
    let delta = session.get_delta_from_snapshot(&snapshot, "main").unwrap();
    assert_eq!(delta, " plus new content");
}

#[test]
fn test_snapshot_extraction_exact_match() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // First delta is long enough to meet threshold requirements
    let initial = "This is a long message that exceeds threshold";
    session.on_text_delta(0, initial);

    // Exact match with additional content (strong overlap)
    let exact_match = format!("{initial} World");
    let delta1 = session.get_delta_from_snapshot(&exact_match, "0").unwrap();
    assert_eq!(delta1, " World");
}

#[test]
fn test_snapshot_in_token_stream() {
    let mut session = StreamingSession::new();
    session.on_message_start();

    // First few tokens as genuine deltas - use longer content to meet thresholds
    let initial = "This is a long message that exceeds threshold";
    session.on_text_delta(0, initial);
    session.on_text_delta(0, " with more content");

    // Now GLM sends a snapshot instead of delta
    // The accumulated content plus new content should meet thresholds:
    // Accumulated: "This is a long message that exceeds threshold with more content" (62 chars)
    // Snapshot: accumulated + "! This is additional content" (88 chars total)
    // Overlap: 62 chars
    // Ratio: 62/88 ~ 70% >= 50%
    let accumulated = session
        .get_accumulated(ContentType::Text, "0")
        .unwrap()
        .to_string();
    let snapshot = format!("{accumulated}! This is additional content");
    assert!(
        session.is_likely_snapshot(&snapshot, "0"),
        "Should detect snapshot in token stream with strong overlap"
    );

    // Extract delta and continue
    let delta = session.get_delta_from_snapshot(&snapshot, "0").unwrap();
    assert!(delta.contains("! This is additional content"));

    // Apply the delta
    session.on_text_delta(0, delta);

    // Verify final accumulated content
    let expected = format!("{accumulated}! This is additional content");
    assert_eq!(
        session.get_accumulated(ContentType::Text, "0"),
        Some(expected.as_str())
    );
}
